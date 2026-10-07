import httpx

from shared.config import get_settings
from shared.utils import logging as slog
from shared.utils.latency import Timer, recorder

_clients: dict[str, httpx.AsyncClient] = {}


def _base_url(service: str) -> str:
    s = get_settings()
    return {"customer": s.customer_service_url, "payment": s.payment_service_url, "voice": s.voice_service_url,
            "webhook": s.webhook_service_url, "recovery": s.recovery_service_url}[service]


def get_client(service: str) -> httpx.AsyncClient:
    if service not in _clients:
        s = get_settings()
        _clients[service] = httpx.AsyncClient(
            base_url=_base_url(service),
            timeout=httpx.Timeout(s.http_default_timeout_s, connect=s.http_connect_timeout_s),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100, keepalive_expiry=30))
    return _clients[service]


def trace_headers(extra: dict | None = None) -> dict:
    ctx = slog.get_ctx()
    h = {"X-Internal-Token": get_settings().internal_token}
    if ctx.get("request_id"):
        h["X-Request-ID"] = ctx["request_id"]
    if ctx.get("correlation_id"):
        h["X-Correlation-ID"] = ctx["correlation_id"]
    return {**h, **(extra or {})}


async def call(service: str, method: str, path: str, *, json=None, params=None, content: bytes | None = None,
               timeout: float | None = None, headers: dict | None = None) -> httpx.Response:
    with Timer() as t:
        resp = await get_client(service).request(
            method, path, json=json, params=params, content=content, headers=trace_headers(headers),
            timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
    recorder.observe(f"downstream:{service} {method}", t.ms)  # shows up in /metrics
    return resp


async def close_all() -> None:
    for c in _clients.values():
        await c.aclose()
    _clients.clear()