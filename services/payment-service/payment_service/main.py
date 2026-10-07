import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import get_settings
from shared.db import get_session
from shared.events import events
from shared.models import Payment, utcnow
from shared.redis import get_redis
from shared.schemas.models import MockConfigIn, PaymentOut, ScheduleRetryIn
from shared.utils import logging as slog
from shared.utils.app import create_app
from shared.utils.security import require_internal

from .provider import OVERRIDE_KEY, MockPaymentProvider

logger = logging.getLogger("payment")
app = create_app("payment-service", title="Payment Service")
router = APIRouter(prefix="/payments", dependencies=[Depends(require_internal)], tags=["payments"])
mock_router = APIRouter(prefix="/mock-provider", dependencies=[Depends(require_internal)], tags=["mock-provider"])


def provider() -> MockPaymentProvider:
    return MockPaymentProvider(get_redis())


async def _get(s: AsyncSession, payment_id: str) -> Payment:
    row = await s.scalar(select(Payment).where(Payment.payment_id == payment_id))
    if not row:
        raise HTTPException(404, "payment not found")
    slog.bind(payment_id=payment_id, customer_id=row.customer_id)
    return row


@router.get("", response_model=list[PaymentOut])
async def list_payments(customer_id: str | None = None, status: str | None = None,
                        limit: int = Query(200, le=500), s: AsyncSession = Depends(get_session)):
    q = select(Payment).order_by(Payment.payment_id).limit(limit)
    if customer_id:
        q = q.where(Payment.customer_id == customer_id)
    if status:
        q = q.where(Payment.status == status)
    return (await s.scalars(q)).all()


@router.post("/detect-failed")
async def detect_failed(s: AsyncSession = Depends(get_session)):
    """Find FAILED autopay payments and publish a payment.failed event for each (the recovery worker listens)."""
    rows = (await s.scalars(select(Payment).where(Payment.status == "FAILED"))).all()
    for p in rows:
        await events.publish(get_redis(), events.PAYMENT_FAILED, {"customer_id": p.customer_id, "payment_id": p.payment_id})
    return {"failed_payments": len(rows), "published": len(rows)}


@router.get("/{payment_id}", response_model=PaymentOut)
async def get_payment(payment_id: str, s: AsyncSession = Depends(get_session)):
    return await _get(s, payment_id)


@router.post("/{payment_id}/fail", response_model=PaymentOut)
async def simulate_failure(payment_id: str, reason: str = "INSUFFICIENT_FUNDS", s: AsyncSession = Depends(get_session)):
    """Simulate an autopay run failing; publishes payment.failed."""
    p = await _get(s, payment_id)
    p.status, p.failure_reason, p.attempted_at = "FAILED", reason, utcnow()
    await s.commit()
    await events.publish(get_redis(), events.PAYMENT_FAILED, {"customer_id": p.customer_id, "payment_id": p.payment_id})
    return p


@router.post("/{payment_id}/retry")
async def retry_payment(payment_id: str, idempotency_key: str | None = Header(default=None),
                        s: AsyncSession = Depends(get_session)):
    """Retry a failed payment via the mock provider. Safe against double clicks and repeated tool calls."""
    cfg, redis = get_settings(), get_redis()
    p = await _get(s, payment_id)
    if p.status == "SUCCESS":
        return {"payment_id": payment_id, "status": "SUCCESS", "already_paid": True, "message": "payment already successful"}
    if p.status not in ("FAILED", "RETRY_REQUESTED"):
        raise HTTPException(409, f"payment in status {p.status} cannot be retried")
    if p.retry_count >= cfg.max_retry_attempts:
        raise HTTPException(409, "maximum retry attempts reached")
    # only one in-flight retry per payment -> prevents double charging
    lock_key = f"payretry:lock:{payment_id}"
    if not await redis.set(lock_key, idempotency_key or "1", nx=True, ex=30):
        raise HTTPException(409, "retry already in progress")
    try:
        p.status = "RETRY_REQUESTED"
        p.retry_count += 1
        await s.flush()
        result = await provider().charge(p.payment_id, float(p.amount), p.currency, p.mock_retry_outcome)
        p.status = result["outcome"]
        p.failure_reason = result["failure_reason"]
        p.attempted_at = utcnow()
        await s.commit()
        slog.log(logger, "payment retry finished", status=p.status, retry_count=p.retry_count)
        return {"payment_id": payment_id, "status": p.status, "failure_reason": p.failure_reason,
                "amount": float(p.amount), "currency": p.currency, "retry_count": p.retry_count}
    finally:
        await redis.delete(lock_key)


@router.post("/{payment_id}/schedule-retry", response_model=PaymentOut)
async def schedule_retry(payment_id: str, body: ScheduleRetryIn, s: AsyncSession = Depends(get_session)):
    p = await _get(s, payment_id)
    if p.status not in ("FAILED", "RETRY_REQUESTED"):
        raise HTTPException(409, f"payment in status {p.status} cannot be scheduled")
    p.scheduled_retry_at = body.retry_at or (utcnow() + timedelta(hours=24))
    await s.commit()
    return p


# ---------- Mock payment provider REST surface (the contract a real gateway adapter would also have) ----------
@mock_router.post("/payments/{payment_id}/retry")
async def mock_provider_retry(payment_id: str, s: AsyncSession = Depends(get_session)):
    """Provider side only: one charge attempt; does not change our payment record."""
    p = await _get(s, payment_id)
    return await provider().charge(p.payment_id, float(p.amount), p.currency, p.mock_retry_outcome)


@mock_router.get("/payments/{payment_id}")
async def mock_provider_get(payment_id: str, s: AsyncSession = Depends(get_session)):
    p = await _get(s, payment_id)
    return {"payment_id": p.payment_id, "status": p.status, "configured_retry_outcome": p.mock_retry_outcome,
            "override": await provider().outcome_override()}


@mock_router.post("/config")
async def mock_provider_config(body: MockConfigIn):
    """Force every mock retry to SUCCESS / FAILED / DECLINED (null clears it): handy for demos."""
    if body.outcome:
        await get_redis().set(OVERRIDE_KEY, body.outcome)
    else:
        await get_redis().delete(OVERRIDE_KEY)
    return {"override": body.outcome}


app.include_router(router)
app.include_router(mock_router)