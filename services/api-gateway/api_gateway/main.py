"""API gateway: the only public entry point. Auth, rate limiting, routing, audit log, aggregation."""
import asyncio
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared.config import get_settings
from shared.redis import get_redis
from shared.schemas.models import MockConfigIn
from shared.utils import http
from shared.utils import logging as slog
from shared.utils.app import create_app
from shared.utils.latency import recorder

logger = logging.getLogger("gateway")
audit = logging.getLogger("audit")
cfg = get_settings()
app: FastAPI = create_app("api-gateway", title="API Gateway", uses_db=False)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in cfg.cors_origins.split(",") if o.strip()],
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"],
                   expose_headers=["X-Request-ID", "Server-Timing"])

# /api/<resource>  ->  (service, path prefix inside that service)
ROUTES = {"customers": ("customer", "/customers"), "payments": ("payment", "/payments"), "calls": ("voice", "/calls"),
          "recovery": ("recovery", "/recovery"), "analytics": ("recovery", "/analytics")}
SERVICES = ["customer", "payment", "voice", "webhook", "recovery"]


# ---------------- auth ----------------
class Login(BaseModel):
    username: str
    password: str


async def rate_limit(request: Request, limit: int | None = None) -> None:
    """Fixed-window limiter per client IP: ONE Redis round trip (INCR + EXPIRE pipelined). Fails open if Redis is down."""
    limit = limit or get_settings().rate_limit_per_minute
    ip = request.client.host if request.client else "unknown"
    key = f"rl:{ip}:{int(time.time() // 60)}:{limit}"
    try:
        async with get_redis().pipeline(transaction=False) as p:
            p.incr(key)
            p.expire(key, 70)
            count, _ = await p.execute()
    except Exception:  # noqa: BLE001
        return
    if count > limit:
        recorder.incr("rate_limited")
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "30"})


@app.post("/api/auth/login", tags=["auth"])
async def login(body: Login, request: Request):
    await rate_limit(request, limit=20)  # stricter limit on login: brute-force guard
    s = get_settings()
    ok = hmac.compare_digest(body.username, s.admin_username) and hmac.compare_digest(body.password, s.admin_password)
    if not ok:
        audit.warning("login failed", extra={"fields": {"username": body.username}})
        raise HTTPException(401, "invalid credentials")
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.jwt_ttl_minutes)
    return {"access_token": jwt.encode({"sub": body.username, "exp": exp}, s.jwt_secret, algorithm="HS256"),
            "token_type": "bearer"}


async def current_user(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        return jwt.decode(auth[7:], get_settings().jwt_secret, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token") from None


# ---------------- aggregation ----------------
@app.get("/api/customers/{customer_id}/details", tags=["aggregates"])
async def customer_details(customer_id: str, user: str = Depends(current_user), _: None = Depends(rate_limit)):
    """Fans out to four services IN PARALLEL: latency = the slowest call, not the sum."""
    slog.bind(customer_id=customer_id)
    try:
        cust, pays, calls, rec = await asyncio.gather(
            http.call("customer", "GET", f"/customers/{customer_id}"),
            http.call("payment", "GET", "/payments", params={"customer_id": customer_id}),
            http.call("voice", "GET", "/calls", params={"customer_id": customer_id}),
            http.call("recovery", "GET", "/recovery", params={"customer_id": customer_id}))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"a backend service is unavailable ({e.__class__.__name__}); check /api/health/all") from None
    if cust.status_code == 404:
        raise HTTPException(404, "customer not found")
    call_list = calls.json()
    latest = None
    if call_list:  # newest call with transcript, state timeline and tool latencies
        d = await http.call("voice", "GET", f"/calls/{call_list[0]['call_id']}")
        latest = d.json() if d.status_code == 200 else None
    return {"customer": cust.json(), "payments": pays.json(), "calls": call_list,
            "recovery_attempts": rec.json(), "latest_call": latest}


@app.get("/api/analytics/latency", tags=["aggregates"])
async def latency(user: str = Depends(current_user)):
    """p50/p95/p99 for every route and agent tool, from every service."""
    async def fetch(svc: str):
        try:
            r = await http.call(svc, "GET", "/metrics", timeout=1.5)
            return svc, r.json()
        except Exception:  # noqa: BLE001
            return svc, {"error": "unreachable"}
    results = dict(await asyncio.gather(*[fetch(s) for s in SERVICES]))
    results["api-gateway"] = {"service": "api-gateway", **recorder.snapshot()}
    return results


@app.get("/api/health/all", tags=["ops"])
async def health_all():
    async def check(svc: str):
        try:
            r = await http.call(svc, "GET", "/health", timeout=1.5)
            return svc, r.json()
        except Exception:  # noqa: BLE001
            return svc, {"status": "unreachable"}
    out = dict(await asyncio.gather(*[check(s) for s in SERVICES]))
    out["api-gateway"] = {"status": "ok"}
    return out


@app.post("/api/demo/payment-outcome", tags=["demo"])
async def set_mock_outcome(body: MockConfigIn, user: str = Depends(current_user)):
    """Force the MOCK payment provider's retries to SUCCESS / FAILED / DECLINED (null = per-customer defaults)."""
    r = await http.call("payment", "POST", "/mock-provider/config", json=body.model_dump())
    return r.json()


# ---------------- generic proxy (keep this LAST so the specific routes above win) ----------------
@app.api_route("/api/{resource}", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/api/{resource}/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def proxy(resource: str, request: Request, path: str = "",
                user: str = Depends(current_user), _: None = Depends(rate_limit)):
    if resource not in ROUTES:
        raise HTTPException(404, "unknown resource")
    service, prefix = ROUTES[resource]
    target = f"{prefix}/{path}" if path else prefix
    body = await request.body()
    try:
        r = await http.call(service, request.method, target, params=dict(request.query_params), content=body or None,
                            headers={"Content-Type": request.headers.get("content-type", "application/json")} if body else None,
                            timeout=15)
    except httpx.TimeoutException:
        raise HTTPException(504, f"{service} service timed out") from None
    except httpx.TransportError:
        raise HTTPException(502, f"{service} service unavailable") from None
    if request.method != "GET":   # audit trail: who changed what
        audit.info("audit", extra={"fields": {"user": user, "method": request.method,
                                              "path": f"/api/{resource}/{path}".rstrip("/"), "status": r.status_code}})
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))