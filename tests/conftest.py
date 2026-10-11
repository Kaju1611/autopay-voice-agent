"""Every service runs IN-PROCESS behind httpx ASGI transports: no sockets, no Docker, no phone.
SQLite replaces PostgreSQL and fakeredis replaces Redis; the code under test is otherwise identical."""
import os

# Set BEFORE any app is imported. Environment variables beat .env, so your real secrets are never used.
os.environ.update({
    "MOCK_CALL_STEP_DELAY_MS": "0", "MOCK_PROVIDER_LATENCY_MS": "0", "VOICE_PROVIDER": "mock",
    "DEMO_PHONE_NUMBER": "+919999999999", "LOG_LEVEL": "WARNING", "RATE_LIMIT_PER_MINUTE": "1000",
    "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "test-pass", "INTERNAL_TOKEN": "test-internal-token",
    "WEBHOOK_SECRET": "test-webhook-secret", "JWT_SECRET": "test-jwt-secret-test-jwt-secret-1234",
    "MOCK_PROVIDER_DEFAULT_OUTCOME": "", "AUTO_RECOVERY": "false",
})

import fakeredis
import httpx
import pytest_asyncio

from shared import db
from shared.config import reset_settings
from shared.redis import set_redis
from shared.utils import http

INTERNAL = {"X-Internal-Token": "test-internal-token"}


@pytest_asyncio.fixture
async def stack(tmp_path):
    reset_settings()
    from database.seed.seed import seed
    from shared.db import Base
    import shared.models  # noqa: F401
    from api_gateway.main import app as gateway
    from customer_service.main import app as customer
    from payment_service.main import app as payment
    from recovery_worker.main import app as recovery
    from voice_service.main import app as voice
    from voice_service.providers.factory import get_provider, set_provider
    from webhook_service.main import app as webhook

    engine = db.init_db(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed()
    set_redis(fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True))
    set_provider(None)
    apps = {"customer": customer, "payment": payment, "voice": voice, "webhook": webhook, "recovery": recovery}
    for name, app in apps.items():
        http.register_transport(name, httpx.ASGITransport(app=app))
    gw = httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://gateway")
    r = await gw.post("/api/auth/login", json={"username": "admin", "password": "test-pass"})
    gw.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    class Stack:
        pass

    st = Stack()
    st.gateway, st.provider, st.apps, st.gateway_app = gw, get_provider(), apps, gateway

    async def run_recovery(customer_id: str) -> httpx.Response:
        """Dashboard click -> queue -> worker -> voice -> (mock) provider -> webhooks, all awaited to completion."""
        from recovery_worker import worker
        from webhook_service.main import drain
        r = await gw.post(f"/api/recovery/{customer_id}")
        await worker.ensure_group()
        await worker.consume_once(block_ms=10)
        for _ in range(3):
            await st.provider.wait_idle()
            await drain()
        return r

    st.run_recovery = run_recovery
    yield st
    await gw.aclose()
    await http.close_all()
    await db.dispose_db()
    set_provider(None)