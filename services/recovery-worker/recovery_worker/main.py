import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import get_session
from shared.events import events
from shared.models import Call, Customer, Payment, RecoveryAttempt
from shared.redis import get_redis
from shared.schemas.models import RecoveryAttemptOut, RecoveryRequestIn
from shared.utils import logging as slog
from shared.utils.app import create_app
from shared.utils.security import require_internal

from . import worker

_task: asyncio.Task | None = None


async def _start(app):
    global _task
    _task = asyncio.create_task(worker.run_forever())  # consumer runs alongside the API


async def _stop(app):
    if _task:
        _task.cancel()


app = create_app("recovery-worker", title="Recovery Service + Worker", on_startup=_start, on_shutdown=_stop)
rec = APIRouter(prefix="/recovery", dependencies=[Depends(require_internal)], tags=["recovery"])
ana = APIRouter(prefix="/analytics", dependencies=[Depends(require_internal)], tags=["analytics"])


@rec.post("/{customer_id}", status_code=202)
async def request_recovery(customer_id: str, body: RecoveryRequestIn | None = None, s: AsyncSession = Depends(get_session)):
    """Enqueue and return immediately: the dashboard click is acknowledged in milliseconds, dialling happens async."""
    slog.bind(customer_id=customer_id)
    payment_id = body.payment_id if body else None
    s.add(RecoveryAttempt(customer_id=customer_id, payment_id=payment_id, action="RECOVERY_REQUESTED", result="QUEUED"))
    await s.commit()
    job = await events.publish(get_redis(), events.RECOVERY_REQUESTED, {"customer_id": customer_id, "payment_id": payment_id})
    return {"job_id": job, "status": "QUEUED", "customer_id": customer_id}


@rec.get("", response_model=list[RecoveryAttemptOut])
async def list_attempts(customer_id: str | None = None, limit: int = Query(200, le=1000), s: AsyncSession = Depends(get_session)):
    q = select(RecoveryAttempt).order_by(RecoveryAttempt.id.desc()).limit(limit)
    if customer_id:
        q = q.where(RecoveryAttempt.customer_id == customer_id)
    return (await s.scalars(q)).all()


@ana.get("")
async def summary(s: AsyncSession = Depends(get_session)):
    """Numbers for the dashboard cards. recovery_rate = successful recoveries / calls attempted (percent)."""
    total_customers = await s.scalar(select(func.count(Customer.id)))
    failed = await s.scalar(select(func.count(Payment.id)).where(Payment.status == "FAILED"))
    attempted = await s.scalar(select(func.count(Call.id)))
    answered = await s.scalar(select(func.count(Call.id)).where(Call.started_at.is_not(None)))
    recovered = await s.scalar(select(func.count(Call.id)).where(Call.outcome == "RECOVERED"))
    outcomes = (await s.execute(select(Call.outcome, func.count(Call.id)).where(Call.outcome.is_not(None)).group_by(Call.outcome))).all()
    return {"total_customers": total_customers, "failed_payments": failed, "calls_attempted": attempted,
            "calls_answered": answered, "successful_recoveries": recovered,
            "recovery_rate": round(recovered / attempted * 100, 1) if attempted else 0.0,
            "outcomes": {k: v for k, v in outcomes}}


@ana.get("/overview")
async def overview(s: AsyncSession = Depends(get_session)):
    """One row per customer for the Customers table: three small queries joined in memory (no N+1)."""
    customers = (await s.scalars(select(Customer).order_by(Customer.customer_id))).all()
    payments = {p.customer_id: p for p in (await s.scalars(select(Payment).order_by(Payment.id))).all()}
    latest_call: dict[str, Call] = {}
    for c in (await s.scalars(select(Call).order_by(Call.id))).all():
        latest_call[c.customer_id] = c
    rows = []
    for cu in customers:
        p, c = payments.get(cu.customer_id), latest_call.get(cu.customer_id)
        recovery = "NOT_CONTACTED" if not c else ("IN_PROGRESS" if c.status != "COMPLETED" else (c.outcome or "UNKNOWN"))
        rows.append({"customer_id": cu.customer_id, "name": cu.name,
                     "payment_id": p.payment_id if p else None, "amount": float(p.amount) if p else None,
                     "currency": p.currency if p else None, "payment_status": p.status if p else None,
                     "call_id": c.call_id if c else None, "call_status": c.status if c else "NO_CALL",
                     "recovery_status": recovery})
    return rows


app.include_router(rec)
app.include_router(ana)