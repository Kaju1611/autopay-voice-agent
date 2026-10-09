import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shared.config import get_settings
from shared.db import sessionmaker
from shared.models import Call, CallEvent, RecoveryAttempt, new_id, utcnow
from shared.redis import get_redis
from shared.utils import http, state_machine as sm
from shared.utils import logging as slog

from .providers.base import CallRequest
from .providers.factory import get_provider

logger = logging.getLogger("voice")

REASON_TEXT = {
    "INSUFFICIENT_FUNDS": "insufficient funds in the linked account",
    "CARD_DECLINED_BY_ISSUER": "the card being declined by the issuing bank",
    "CARD_EXPIRED": "the card on file having expired",
    "BANK_TIMEOUT": "a temporary timeout at the bank",
    "DAILY_LIMIT_EXCEEDED": "the daily transaction limit being exceeded",
}


def ctx_key(pcid: str) -> str:
    return f"callctx:{pcid}"


def lock_key(customer_id: str) -> str:
    return f"callactive:{customer_id}"


async def get_ctx(pcid: str) -> dict | None:
    """Call context from Redis (about 1 ms). On a cache miss, rebuild the essentials from the database."""
    raw = await get_redis().get(ctx_key(pcid))
    if raw:
        return json.loads(raw)
    async with sessionmaker()() as s:
        call = await s.scalar(select(Call).where(Call.provider_call_id == pcid))
    if not call:
        return None
    return {"call_id": call.call_id, "customer_id": call.customer_id, "payment_id": call.payment_id}


async def save_ctx(pcid: str, ctx: dict) -> None:
    await get_redis().set(ctx_key(pcid), json.dumps(ctx), ex=get_settings().context_cache_ttl_s)


def inr(amount: float) -> str:
    return f"₹{amount:,.0f}"


async def start_call(customer_id: str, payment_id: str | None = None) -> Call:
    cfg = get_settings()
    slog.bind(customer_id=customer_id, payment_id=payment_id)

    # 1) LATENCY: fetch customer eligibility and payments IN PARALLEL (one round trip, not two)
    cust_r, pay_r = await asyncio.gather(
        http.call("customer", "GET", f"/customers/{customer_id}/eligibility"),
        http.call("payment", "GET", "/payments", params={"customer_id": customer_id}))
    if cust_r.status_code == 404:
        raise HTTPException(404, "customer not found")
    elig = cust_r.json()
    if not elig["eligible"]:
        raise HTTPException(409, f"customer not eligible for calls: {elig['reasons']}")
    failed = [p for p in pay_r.json() if p["status"] == "FAILED" and (payment_id in (None, p["payment_id"]))]
    if not failed:
        raise HTTPException(409, "no failed payment to recover")
    pay = failed[0]
    slog.bind(payment_id=pay["payment_id"])

    # 2) SAFETY: only ever dial the authorised demo number, never the customer's (fictional) phone
    if cfg.voice_provider.lower() != "mock" and not cfg.demo_phone_number:
        raise HTTPException(503, "DEMO_PHONE_NUMBER must be set to a number you control before placing real calls")
    to_number = cfg.demo_phone_number or "demo-line"

    # 3) SAFETY: one active call per customer, so we can never double-dial
    redis = get_redis()
    if not await redis.set(lock_key(customer_id), "1", nx=True, ex=600):
        raise HTTPException(409, "a call for this customer is already in progress")

    call = Call(call_id=new_id("CALL"), customer_id=customer_id, payment_id=pay["payment_id"],
                status=sm.CALL_INITIATED, request_id=slog.get_ctx().get("request_id"))
    async with sessionmaker()() as s:
        s.add(call)
        s.add(CallEvent(call_id=call.call_id, event_type="STATE:" + sm.CALL_INITIATED, payload={"to": sm.CALL_INITIATED}))
        await s.commit()
    slog.bind(call_id=call.call_id)

    # 4) LATENCY: dynamic variables pre-load everything the opening needs => the agent speaks without any tool call
    reason = pay.get("failure_reason") or "UNKNOWN"
    dyn = {"customer_name": elig["name"], "amount_formatted": inr(pay["amount"]), "currency": pay["currency"],
           "failure_reason_text": REASON_TEXT.get(reason, "a processing error"), "payment_id": pay["payment_id"]}
    try:
        pc = await get_provider().create_call(CallRequest(
            to_number=to_number, from_number=cfg.voice_provider_from_number, agent_id=cfg.voice_provider_agent_id,
            dynamic_variables=dyn,
            metadata={"call_id": call.call_id, "customer_id": customer_id, "payment_id": pay["payment_id"]}))
    except Exception as e:  # noqa: BLE001
        await redis.delete(lock_key(customer_id))
        async with sessionmaker()() as s:
            row = await s.scalar(select(Call).where(Call.call_id == call.call_id))
            row.status, row.outcome = sm.COMPLETED, "PROVIDER_ERROR"
            await s.commit()
        logger.exception("provider create_call failed")
        raise HTTPException(502, f"voice provider error: {e.__class__.__name__}") from e

    slog.bind(provider_call_id=pc.provider_call_id)
    slog.log(logger, "dialling", to_last4=to_number[-4:], provider=cfg.voice_provider)
    async with sessionmaker()() as s:
        row = await s.scalar(select(Call).where(Call.call_id == call.call_id))
        row.provider_call_id = pc.provider_call_id
        s.add(RecoveryAttempt(customer_id=customer_id, payment_id=pay["payment_id"], call_id=call.call_id,
                              action="CALL_INITIATED", result="DIALING"))
        await s.commit()
        call = row

    # 5) LATENCY: context cached in Redis BEFORE the provider can send its first webhook or tool call
    await save_ctx(pc.provider_call_id, {
        "call_id": call.call_id, "customer_id": customer_id, "payment_id": pay["payment_id"],
        "customer_name": elig["name"], "amount": pay["amount"], "currency": pay["currency"],
        "failure_reason": reason, "payment_status": pay["status"]})
    await get_provider().on_registered(pc.provider_call_id)
    return call


async def move(s, call: Call, to: str, reason: str = "", **extra) -> None:
    """Validate and store a state transition (saved as a call_event)."""
    sm.assert_transition(call.status, to)
    s.add(CallEvent(call_id=call.call_id, event_type="STATE:" + to,
                    payload={"from": call.status, "to": to, "reason": reason, **extra}))
    call.status = to
    if to in sm.STATE_OUTCOME:
        call.outcome = sm.STATE_OUTCOME[to]
    slog.log(logger, "state transition", to=to, reason=reason)


def add_attempt(s, call: Call, action: str, result: str) -> None:
    s.add(RecoveryAttempt(customer_id=call.customer_id, payment_id=call.payment_id,
                          call_id=call.call_id, action=action, result=result))

def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


async def apply_event(event_type: str, event_id: str | None, pcid: str, data: dict) -> dict:
    """Apply a provider event (call_started / transcript / call_ended) to the call. Safe to receive twice."""
    ctx = await get_ctx(pcid)
    if not ctx:
        raise HTTPException(404, "unknown provider_call_id")
    slog.bind(call_id=ctx["call_id"], customer_id=ctx["customer_id"], payment_id=ctx.get("payment_id"), provider_call_id=pcid)
    async with sessionmaker()() as s:
        s.add(CallEvent(call_id=ctx["call_id"], event_id=event_id, event_type=event_type, payload=data))
        try:
            await s.flush()
        except IntegrityError:        # event_id is UNIQUE: a duplicate delivery is ignored (idempotency backstop)
            await s.rollback()
            return {"status": "duplicate"}
        call = await s.scalar(select(Call).where(Call.call_id == ctx["call_id"]))

        if event_type == "call_started":
            call.started_at = utcnow()
            if call.status == sm.CALL_INITIATED:
                await move(s, call, sm.CUSTOMER_ANSWERED, "call_started")

        elif event_type == "call_ended":
            call.ended_at = utcnow()
            dur = data.get("duration_ms")
            call.duration = round(dur / 1000, 1) if dur is not None else (
                (call.ended_at - _aware(call.started_at)).total_seconds() if call.started_at else 0.0)
            if call.status == sm.CALL_INITIATED:           # never answered
                await move(s, call, sm.NO_RESPONSE, data.get("disconnection_reason", "no answer"))
            lines = (await s.scalars(select(CallEvent).where(CallEvent.call_id == call.call_id, CallEvent.event_type == "transcript")
                                     .order_by(CallEvent.id))).all()
            call.transcript = data.get("transcript") or "\n".join(
                f"{'Agent' if e.payload.get('role') == 'agent' else 'Customer'}: {e.payload.get('content', '')}" for e in lines)
            if sm.can_transition(call.status, sm.COMPLETED):
                await move(s, call, sm.COMPLETED, data.get("disconnection_reason", "ended"))
            call.outcome = call.outcome or "INCOMPLETE"
            add_attempt(s, call, "CALL_COMPLETED", call.outcome)
            await get_redis().delete(lock_key(call.customer_id))     # customer can be called again
        # "transcript" events are just stored; the dashboard reads them live
        await s.commit()
        return {"status": "processed", "call_id": call.call_id, "state": call.status}