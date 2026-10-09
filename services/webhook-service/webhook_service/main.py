"""Webhook service: verifies and dedupes provider events, ACKs immediately, applies them to the voice service in order.

Latency design
  * call-started / transcript / call-ended -> accepted after one Redis round trip, processed in the background.
  * function-call -> the caller is waiting, so it runs synchronously under a HARD timeout; on timeout the agent gets a
    spoken-friendly message instead of silence, and the (idempotent) work keeps running server-side.
  * events of ONE call are applied strictly in arrival order; different calls run concurrently.
"""
import asyncio
import hashlib
import json
import logging

from fastapi import FastAPI, HTTPException, Request

from shared.config import get_settings
from shared.redis import get_redis
from shared.utils import http
from shared.utils import logging as slog
from shared.utils.app import create_app
from shared.utils.latency import Timer, recorder
from shared.utils.security import verify_webhook

logger = logging.getLogger("webhooks")
app: FastAPI = create_app("webhook-service", title="Webhook Service", uses_db=False)

_tail: dict[str, asyncio.Task] = {}   # last queued task per call => ordering within a call
_tasks: set[asyncio.Task] = set()
DEAD_LETTER = "webhook:dead"


def _verify_retell(body: bytes, signature: str | None) -> bool:
    """Optional: Retell's own signature (needs `pip install retell-sdk`; check verify() against your SDK version)."""
    if not signature:
        return False
    try:
        from retell import Retell
        return bool(Retell.verify(body.decode(), api_key=get_settings().voice_provider_api_key, signature=signature))
    except Exception:  # noqa: BLE001
        return False


async def _read(request: Request, allow_retell: bool = False) -> tuple[dict, bytes]:
    raw = await request.body()
    ok = verify_webhook(request.headers, raw) or (allow_retell and _verify_retell(raw, request.headers.get("x-retell-signature")))
    if not ok:
        recorder.incr("webhook_rejected")
        raise HTTPException(401, "invalid webhook signature")
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON") from None


def _pcid(body: dict) -> str:
    pcid = body.get("provider_call_id") or (body.get("call") or {}).get("call_id") or body.get("call_id")
    if not pcid:
        raise HTTPException(422, "provider_call_id missing")
    slog.bind(provider_call_id=pcid)
    return pcid


async def _first_delivery(event_id: str) -> bool:
    """Redis SET NX: only the first delivery of an event id returns True (24h memory)."""
    return bool(await get_redis().set(f"webhook:evt:{event_id}", "1", nx=True, ex=86_400))


async def _forward(event_type: str, event_id: str, pcid: str, data: dict) -> None:
    for attempt in range(4):
        try:
            r = await http.call("voice", "POST", "/internal/events", json={
                "event_type": event_type, "event_id": event_id, "provider_call_id": pcid, "data": data})
            if r.status_code < 400:
                return
            if r.status_code != 404:       # 404 = call context not registered yet -> retry shortly
                break
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.2 * 2 ** attempt)
    recorder.incr("webhook_dead_letter")
    await get_redis().lpush(DEAD_LETTER, json.dumps({"type": event_type, "event_id": event_id, "pcid": pcid, "data": data}))
    logger.error("event moved to dead-letter list")


def _spawn(pcid: str, coro_fn) -> None:
    """Queue background work behind the previous task of the SAME call (ordering); concurrent across calls."""
    prev = _tail.get(pcid)

    async def run():
        if prev is not None:
            await asyncio.wait({prev})
        await coro_fn()

    t = asyncio.create_task(run())
    _tail[pcid] = t
    _tasks.add(t)

    def _done(task):
        _tasks.discard(task)
        if _tail.get(pcid) is task:
            del _tail[pcid]
    t.add_done_callback(_done)


async def _after_pending(pcid: str) -> None:
    """A tool call must see every earlier event of its call (e.g. call_started) already applied."""
    tail = _tail.get(pcid)
    if tail is not None:
        await asyncio.wait({tail})


async def drain() -> None:
    """Wait for in-flight background work (used by tests / graceful shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _ingest(event_type: str, body: dict, raw: bytes) -> dict:
    pcid = _pcid(body)
    event_id = body.get("event_id") or hashlib.sha256(event_type.encode() + raw).hexdigest()[:40]
    if not await _first_delivery(event_id):
        recorder.incr("webhook_duplicate")
        return {"status": "duplicate"}
    data = dict(body)
    _spawn(pcid, lambda: _forward(event_type, event_id, pcid, data))
    return {"status": "accepted"}


@app.post("/webhooks/voice/call-started")
async def call_started(request: Request):
    body, raw = await _read(request)
    return await _ingest("call_started", body, raw)


@app.post("/webhooks/voice/call-ended")
async def call_ended(request: Request):
    body, raw = await _read(request)
    return await _ingest("call_ended", body, raw)


@app.post("/webhooks/voice/transcript")
async def transcript(request: Request):
    body, raw = await _read(request)
    return await _ingest("transcript", body, raw)


@app.post("/webhooks/voice/function-call")
async def function_call(request: Request):
    body, raw = await _read(request)
    cfg = get_settings()
    pcid = _pcid(body)
    name, args = body.get("name"), body.get("args") or {}
    event_id = body.get("event_id") or request.headers.get("x-idempotency-key")
    redis = get_redis()
    if event_id and not await _first_delivery("fn:" + event_id):
        cached = await redis.get(f"webhook:res:{event_id}")      # provider retry => same answer, no re-execution
        return json.loads(cached) if cached else {"ok": False, "message": "Request is already being processed."}
    with Timer() as t:
        try:
            async def _execute():
                await _after_pending(pcid)
                return await http.call("voice", "POST", "/internal/functions/execute",
                                       json={"name": name, "args": args, "provider_call_id": pcid})
            r = await asyncio.wait_for(_execute(), timeout=cfg.function_call_hard_timeout_ms / 1000)
            result = r.json() if r.status_code == 200 else {"ok": False, "message": "That action could not be completed."}
        except asyncio.TimeoutError:
            recorder.incr("function_call_timeout")
            logger.warning("function call exceeded hard timeout")
            result = {"ok": False, "status": "PENDING",
                      "message": "This is taking longer than expected. Tell the customer it is still being processed and do not repeat the request."}
    recorder.observe(f"function:{name}:e2e", t.ms)
    if event_id:
        await redis.set(f"webhook:res:{event_id}", json.dumps(result), ex=3600)
    return result


@app.post("/webhooks/voice/retell")
async def retell_events(request: Request):
    """Retell sends all lifecycle events to ONE url: {"event": "call_started"|"call_ended"|..., "call": {...}}."""
    body, raw = await _read(request, allow_retell=True)
    call, event = body.get("call") or {}, body.get("event")
    if event == "call_started":
        return await _ingest("call_started", {"call": call}, raw)
    if event == "call_ended":
        return await _ingest("call_ended", {"call": call, "disconnection_reason": call.get("disconnection_reason"),
                                            "duration_ms": call.get("duration_ms"), "transcript": call.get("transcript")}, raw)
    return {"status": "ignored", "event": event}