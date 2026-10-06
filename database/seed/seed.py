"""Usage: python -m database.seed.seed [--reset]
--reset wipes calls/events/attempts/payments and restores the 10 FAILED payments (handy between demos)."""
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import delete, select

from shared.db import init_db, sessionmaker
from shared.models import Call, CallEvent, Customer, Payment, RecoveryAttempt

# (customer_id, name, amount, failure_reason, what the MOCK payment provider returns on retry)
SEED = [
    ("CUS001", "Rahul Sharma", 2499, "INSUFFICIENT_FUNDS", "SUCCESS"),
    ("CUS002", "Priya Nair", 1299, "CARD_EXPIRED", "SUCCESS"),
    ("CUS003", "Amit Verma", 899, "BANK_TIMEOUT", "SUCCESS"),
    ("CUS004", "Sneha Iyer", 3499, "INSUFFICIENT_FUNDS", "FAILED"),
    ("CUS005", "Vikram Rao", 1999, "CARD_DECLINED_BY_ISSUER", "SUCCESS"),
    ("CUS006", "Neha Gupta", 599, "INSUFFICIENT_FUNDS", "SUCCESS"),
    ("CUS007", "Arjun Mehta", 4999, "DAILY_LIMIT_EXCEEDED", "SUCCESS"),
    ("CUS008", "Kavya Reddy", 749, "BANK_TIMEOUT", "SUCCESS"),
    ("CUS009", "Sanjay Patel", 1599, "INSUFFICIENT_FUNDS", "SUCCESS"),
    ("CUS010", "Divya Menon", 2899, "CARD_EXPIRED", "SUCCESS"),
]


async def seed(reset: bool = False) -> None:
    async with sessionmaker()() as s:
        if reset:
            for model in (RecoveryAttempt, CallEvent, Call, Payment):
                await s.execute(delete(model))
            await s.flush()
        for i, (cid, name, *_rest) in enumerate(SEED, start=1):
            if not await s.scalar(select(Customer.id).where(Customer.customer_id == cid)):
                s.add(Customer(customer_id=cid, name=name, phone=f"+91000000{i:04d}",
                               email=f"{name.split()[0].lower()}.{cid.lower()}@example.com",
                               autopay_enabled=True, call_permission=True))
        await s.flush()
        for i, (cid, _n, amount, reason, outcome) in enumerate(SEED, start=1):
            if not await s.scalar(select(Payment.id).where(Payment.payment_id == f"PAY{i:03d}")):
                s.add(Payment(payment_id=f"PAY{i:03d}", customer_id=cid, amount=amount, currency="INR",
                              status="FAILED", failure_reason=reason,
                              attempted_at=datetime.now(timezone.utc), mock_retry_outcome=outcome))
        await s.commit()
    print(f"seeded {len(SEED)} fictional customers (reset={reset})")


if __name__ == "__main__":
    init_db()
    asyncio.run(seed(reset="--reset" in sys.argv))