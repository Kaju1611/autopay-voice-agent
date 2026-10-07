"""Redis Streams consumer: turns recovery.requested / payment.failed events into outbound calls."""
import asyncio
import json
import logging
import socket

import httpx
from redis.exceptions import ResponseError

from shared.config import get_settings
from shared.db import sessionmaker
from shared.events import events
from shared.models import RecoveryAttempt
from shared.redis import get_redis
from shared.utils import http
from shared.utils import logging as slog

logger = logging.getLogger("recovery-worker")
CONSUMER = f"worker-{socket.gethostname()}"


async def ensure_group() -> None:
    try:
        await get_redis().xgroup_create(events.STREAM, events.GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def record(customer_id: str, payment_id: str | None, action: str, result: str) -> None:
    async with sessionmaker()() as s:
        s.add(RecoveryAttempt(customer_id=customer_id, payment_id=payment_id, action=action, result=result[:40]))
        await s.commit()


async def handle(envelope: dict) -> str:
    slog.reset_ctx()
    slog.bind(request_id=envelope.get("request_id"), correlation_id=envelope.get("correlation_id"))
    etype, data = envelope["type"], envelope["data"]
    customer_id, payment_id = data["customer_id"], data.get("payment_id")
    slog.bind(customer_id=customer_id, payment_id=payment_id)

    # Safety default: failed-payment events never trigger a call unless AUTO_RECOVERY=true.
    if etype == events.PAYMENT_FAILED and not get_settings().auto_recovery:
        slog.log(logger, "payment.failed ignored (AUTO_RECOVERY off)")
        return "IGNORED_AUTO_RECOVERY_OFF"

    # one recovery per customer at a time (stops double-clicks / duplicate events)
    redis = get_redis()
    lock = f"recovery:lock:{customer_id}"
    if not await redis.set(lock, "1", nx=True, ex=60):
        await record(customer_id, payment_id, "RECOVERY_SKIPPED", "DUPLICATE_REQUEST")
        return "DUPLICATE"

    # 1) permission check
    try:
        r = await http.call("customer", "GET", f"/customers/{customer_id}/eligibility")
    except httpx.HTTPError:
        await redis.delete(lock)
        await record(customer_id, payment_id, "RECOVERY_SKIPPED", "CUSTOMER_SVC_UNAVAILABLE")
        return "SKIPPED"
    if r.status_code != 200 or not r.json()["eligible"]:
        reasons = ",".join(r.json().get("reasons", [])) if r.status_code == 200 else "CUSTOMER_NOT_FOUND"
        await redis.delete(lock)
        await record(customer_id, payment_id, "RECOVERY_SKIPPED", reasons or "NOT_ELIGIBLE")
        return "SKIPPED"

    # 2) ask the voice service to place the call
    try:
        r = await http.call("voice", "POST", f"/calls/{customer_id}", json={"payment_id": payment_id})
    except httpx.HTTPError:
        await redis.delete(lock)
        await record(customer_id, payment_id, "RECOVERY_SKIPPED", "VOICE_SVC_UNAVAILABLE")
        return "SKIPPED"
    if r.status_code >= 400:
        await redis.delete(lock)
        await record(customer_id, payment_id, "RECOVERY_SKIPPED", str(r.json().get("detail", r.status_code)))
        return "SKIPPED"
    return "CALL_STARTED"


async def consume_once(count: int = 10, block_ms: int = 2000) -> int:
    redis = get_redis()
    resp = await redis.xreadgroup(events.GROUP, CONSUMER, {events.STREAM: ">"}, count=count, block=block_ms)
    n = 0
    for _stream, messages in resp or []:
        for msg_id, fields in messages:
            try:
                result = await handle(json.loads(fields["payload"]))
                slog.log(logger, "event handled", result=result)
            except Exception:  # noqa: BLE001
                logger.exception("event failed; moved to dead-letter stream")
                await redis.xadd(events.DEAD_LETTER, fields, maxlen=1000)
            await redis.xack(events.STREAM, events.GROUP, msg_id)
            n += 1
    return n


async def run_forever() -> None:
    await ensure_group()
    while True:
        try:
            await consume_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("consumer loop error; backing off 1s")
            await asyncio.sleep(1)