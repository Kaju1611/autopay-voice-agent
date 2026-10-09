"""Tools the voice agent can invoke mid-call. Hot path: tiny responses, minimal DB work, time-boxed downstream calls."""
import logging
from datetime import datetime

from sqlalchemy import select

from shared.config import get_settings
from shared.db import sessionmaker
from shared.models import Call, CallEvent, RecoveryAttempt
from shared.utils import http, state_machine as sm
from shared.utils import logging as slog
from shared.utils.latency import Timer, recorder

from .providers.factory import get_provider
from .service import REASON_TEXT, add_attempt, get_ctx, inr, move, save_ctx

logger = logging.getLogger("functions")


async def _load_call(s, ctx) -> Call:
    return await s.scalar(select(Call).where(Call.call_id == ctx["call_id"]))


def _result_message(status: str, ctx: dict, reason: str | None) -> dict:
    """Short, honest instruction for the agent: never claims success unless the payment really succeeded."""
    amt = inr(ctx.get("amount", 0))
    if status == "SUCCESS":
        return {"status": "SUCCESS", "message": f"The payment of {amt} was successful. Confirm this to the customer and end the call."}
    if status == "PENDING":
        return {"status": "PENDING", "message": "The payment is still processing. Tell the customer it may take a moment and not to retry."}
    why = REASON_TEXT.get(reason or "", "a processing error")
    return {"status": status, "message": f"The retry was unsuccessful due to {why}. Tell the customer honestly, and offer a later retry or a human agent."}


async def get_customer_payment_status(call_ctx: dict, args: dict) -> dict:
    cfg = get_settings()
    try:  # live read with a tight timeout; fall back to the pre-warmed snapshot rather than leave dead air
        r = await http.call("payment", "GET", f"/payments/{call_ctx['payment_id']}", timeout=cfg.status_lookup_timeout_s)
        r.raise_for_status()
        p = r.json()
        return {"payment_id": p["payment_id"], "status": p["status"], "amount": p["amount"], "currency": p["currency"],
                "failure_reason": p["failure_reason"], "source": "live"}
    except Exception:  # noqa: BLE001
        recorder.incr("status_lookup_fallback")
        return {"payment_id": call_ctx["payment_id"], "status": call_ctx.get("payment_status", "FAILED"),
                "amount": call_ctx.get("amount"), "currency": call_ctx.get("currency"),
                "failure_reason": call_ctx.get("failure_reason"), "source": "cache"}


async def confirm_identity(call_ctx: dict, args: dict) -> dict:
    confirmed = bool(args.get("confirmed"))
    async with sessionmaker()() as s:
        call = await _load_call(s, call_ctx)
        if confirmed:
            await move(s, call, sm.IDENTITY_CONFIRMED, "customer confirmed identity")
            await move(s, call, sm.PAYMENT_PROBLEM_EXPLAINED, "payment problem explained")
            add_attempt(s, call, "IDENTITY_CONFIRMED", "OK")
        else:
            await move(s, call, sm.WRONG_NUMBER, "identity not confirmed")
            add_attempt(s, call, "IDENTITY_CONFIRMED", "WRONG_NUMBER")
        await s.commit()
    if not confirmed:
        return {"ok": True, "message": "Apologise briefly, share no payment details, and end the call."}
    reason = REASON_TEXT.get(call_ctx.get("failure_reason", ""), "a processing error")
    amt = inr(call_ctx.get("amount", 0))
    return {"ok": True, "amount": amt, "reason": reason,
            "message": f"Explain that the scheduled payment of {amt} failed due to {reason}, then ask if they want to retry now."}


async def retry_payment(call_ctx: dict, args: dict) -> dict:
    cfg = get_settings()
    async with sessionmaker()() as s:
        call = await _load_call(s, call_ctx)
        prior = await s.scalar(select(RecoveryAttempt).where(RecoveryAttempt.call_id == call.call_id,
                                                             RecoveryAttempt.action == "RETRY_PAYMENT"))
        if prior:  # idempotent: the agent (or provider) repeating this tool call can never double-charge
            return {**_result_message(prior.result, call_ctx, call_ctx.get("failure_reason")), "idempotent_replay": True}
        await move(s, call, sm.RETRY_PAYMENT, "customer agreed to retry")
        await s.commit()   # session closed BEFORE the slow network call: no DB connection held while waiting
    status, reason = "PENDING", None
    try:
        r = await http.call("payment", "POST", f"/payments/{call_ctx['payment_id']}/retry", timeout=cfg.payment_http_timeout_s,
                            headers={"Idempotency-Key": f"{call_ctx['call_id']}:{call_ctx['payment_id']}"})
        if r.status_code == 200:
            body = r.json()
            status, reason = body["status"], body.get("failure_reason")
        else:
            status = "FAILED"
    except Exception:  # noqa: BLE001  (timeout etc.): tell the agent it's pending instead of going silent
        recorder.incr("payment_retry_timeout")
        logger.warning("payment retry timed out; returning PENDING to the agent")
    async with sessionmaker()() as s:
        call = await _load_call(s, call_ctx)
        await move(s, call, sm.PAYMENT_RESULT, f"payment {status}")
        call.outcome = {"SUCCESS": "RECOVERED", "PENDING": "PAYMENT_PENDING"}.get(status, "RETRY_FAILED")
        add_attempt(s, call, "RETRY_PAYMENT", status)
        await s.commit()
    call_ctx["payment_status"], call_ctx["failure_reason"] = status, reason or call_ctx.get("failure_reason")
    await save_ctx(args["_pcid"], call_ctx)
    return _result_message(status, call_ctx, reason)


async def schedule_payment_retry(call_ctx: dict, args: dict) -> dict:
    when = None
    if args.get("retry_at"):
        try:
            when = datetime.fromisoformat(args["retry_at"]).isoformat()
        except ValueError:
            return {"ok": False, "message": "retry_at must be an ISO-8601 date-time; ask the customer for a clearer date."}
    r = await http.call("payment", "POST", f"/payments/{call_ctx['payment_id']}/schedule-retry", json={"retry_at": when})
    if r.status_code != 200:
        return {"ok": False, "message": "I could not schedule that retry."}
    async with sessionmaker()() as s:
        call = await _load_call(s, call_ctx)
        await move(s, call, sm.RETRY_LATER, "customer asked to retry later")
        add_attempt(s, call, "SCHEDULE_RETRY", "SCHEDULED")
        await s.commit()
    return {"ok": True, "scheduled_for": r.json().get("scheduled_retry_at"), "message": "Retry scheduled. Confirm and end the call."}


async def request_human_agent(call_ctx: dict, args: dict) -> dict:
    async with sessionmaker()() as s:
        call = await _load_call(s, call_ctx)
        await move(s, call, sm.HUMAN_AGENT, "customer requested a human")
        add_attempt(s, call, "HUMAN_REQUESTED", "CALLBACK_QUEUED")
        await s.commit()
    return {"ok": True, "message": "Tell the customer a human agent will call back. Do not claim a live transfer."}


async def record_outcome(call_ctx: dict, args: dict) -> dict:
    target = {"DECLINED": sm.DECLINED, "ALREADY_PAID": sm.ALREADY_PAID,
              "INFORMATION_PROVIDED": sm.INFORMATION_PROVIDED}.get(args.get("outcome"))
    if not target:
        return {"ok": False, "message": "outcome must be DECLINED, ALREADY_PAID or INFORMATION_PROVIDED"}
    async with sessionmaker()() as s:
        call = await _load_call(s, call_ctx)
        await move(s, call, target, "recorded by agent")
        add_attempt(s, call, "OUTCOME_RECORDED", target)
        await s.commit()
    msg = {"DECLINED": "Acknowledge politely; do not push.",
           "ALREADY_PAID": "Thank them; say the team will verify. Do not confirm or deny payment status.",
           "INFORMATION_PROVIDED": f"Explain the failure reason ({REASON_TEXT.get(call_ctx.get('failure_reason', ''), 'a processing error')}) and ask again if they want to retry."}[target]
    return {"ok": True, "message": msg}


async def end_call(call_ctx: dict, args: dict) -> dict:
    return {"ok": True, "forced_hangup": await get_provider().end_call(args["_pcid"])}


REGISTRY = {f.__name__: f for f in (get_customer_payment_status, confirm_identity, retry_payment, schedule_payment_retry,
                                    request_human_agent, record_outcome, end_call)}


async def execute(name: str, args: dict, pcid: str) -> dict:
    ctx = await get_ctx(pcid)
    if not ctx:
        return {"ok": False, "message": "Unknown call."}
    slog.bind(call_id=ctx["call_id"], customer_id=ctx["customer_id"], payment_id=ctx.get("payment_id"), provider_call_id=pcid)
    # SECURITY: never trust ids supplied by the model. They must match this call, or the request is refused.
    for forbidden in ("customer_id", "payment_id"):
        if args.get(forbidden) not in (None, ctx.get(forbidden)):
            return {"ok": False, "message": "That request does not match this call."}
    args = {**args, "_pcid": pcid}
    cfg = get_settings()
    with Timer() as t:
        try:
            result = await REGISTRY[name](ctx, args)
        except sm.InvalidTransition as e:
            result = {"ok": False, "message": "That action isn't available at this point in the call.", "detail": str(e)}
        except Exception:  # noqa: BLE001
            logger.exception("function %s failed", name)
            result = {"ok": False, "message": "Something went wrong. Apologise and offer a human agent."}
    recorder.observe(f"function:{name}", t.ms)          # per-tool latency, shown on the dashboard in Step 9
    if t.ms > cfg.function_call_soft_budget_ms:
        recorder.incr("function_call_over_budget")
    async with sessionmaker()() as s:                   # audit trail including the measured latency
        s.add(CallEvent(call_id=ctx["call_id"], event_type="function_call",
                        payload={"name": name, "args": {k: v for k, v in args.items() if k != "_pcid"},
                                 "result": result, "latency_ms": round(t.ms, 1)}))
        await s.commit()
    return result