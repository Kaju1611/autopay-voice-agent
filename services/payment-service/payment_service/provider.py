"""Mock payment provider. NO real money or payment gateway is ever involved.

It sits behind a small interface so a real gateway adapter could replace it later.
It runs in-process: the retry a caller is waiting for skips one network hop (a deliberate latency choice).
The same logic is also exposed over REST under /mock-provider (see main.py).
"""
import asyncio

from shared.config import get_settings

OVERRIDE_KEY = "mockpay:outcome_override"


class MockPaymentProvider:
    def __init__(self, redis):
        self.redis = redis

    async def outcome_override(self) -> str | None:
        return get_settings().mock_provider_default_outcome or await self.redis.get(OVERRIDE_KEY) or None

    async def charge(self, payment_id: str, amount: float, currency: str, configured_outcome: str) -> dict:
        s = get_settings()
        if s.mock_provider_latency_ms:
            await asyncio.sleep(s.mock_provider_latency_ms / 1000)  # simulate a gateway round trip
        outcome = await self.outcome_override() or configured_outcome or "SUCCESS"
        failure = {"FAILED": "INSUFFICIENT_FUNDS", "DECLINED": "CARD_DECLINED_BY_ISSUER"}.get(outcome)
        return {"payment_id": payment_id, "outcome": outcome, "failure_reason": failure, "mock": True}