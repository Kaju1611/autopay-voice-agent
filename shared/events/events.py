"""Redis Streams act as our lightweight message broker."""
import json

from shared.utils import logging as slog

STREAM = "autopay:events"
GROUP = "recovery-workers"
DEAD_LETTER = "autopay:events:dead"

PAYMENT_FAILED = "payment.failed"
RECOVERY_REQUESTED = "recovery.requested"


async def publish(redis, event_type: str, data: dict, maxlen: int = 10_000) -> str:
    ctx = slog.get_ctx()  # carries request_id/correlation_id across services
    envelope = {"type": event_type, "data": data,
                "request_id": ctx.get("request_id"), "correlation_id": ctx.get("correlation_id")}
    return await redis.xadd(STREAM, {"payload": json.dumps(envelope)}, maxlen=maxlen, approximate=True)