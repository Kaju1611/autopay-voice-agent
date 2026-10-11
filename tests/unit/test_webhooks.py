import json

import httpx

from shared.utils.security import sign


def client(stack):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=stack.apps["webhook"]), base_url="http://w")


async def post(c, path, body, signed=True):
    raw = json.dumps(body).encode()
    return await c.post(path, content=raw, headers={"X-Webhook-Signature": sign(raw)} if signed else {})


async def test_rejects_unsigned_and_bad_signature(stack):
    c = client(stack)
    assert (await post(c, "/webhooks/voice/call-started", {"provider_call_id": "x"}, signed=False)).status_code == 401
    r = await c.post("/webhooks/voice/call-started", content=b"{}", headers={"X-Webhook-Signature": "sha256=nope"})
    assert r.status_code == 401


async def test_duplicate_event_is_ignored(stack):
    c = client(stack)
    body = {"event_id": "evt-1", "provider_call_id": "unknown-call"}
    assert (await post(c, "/webhooks/voice/call-started", body)).json()["status"] == "accepted"
    assert (await post(c, "/webhooks/voice/call-started", body)).json()["status"] == "duplicate"


async def test_function_call_for_unknown_call_is_graceful(stack):
    r = await post(client(stack), "/webhooks/voice/function-call",
                   {"name": "retry_payment", "args": {}, "call": {"call_id": "nope"}})
    assert r.status_code == 200 and r.json()["ok"] is False


async def test_slow_tool_call_gets_a_message_not_silence(stack, monkeypatch):
    import asyncio

    from shared.config import reset_settings
    from shared.utils import http as shttp

    async def slow(*a, **k):
        await asyncio.sleep(1)

    monkeypatch.setattr(shttp, "call", slow)
    monkeypatch.setenv("FUNCTION_CALL_HARD_TIMEOUT_MS", "50")
    reset_settings()
    r = await post(client(stack), "/webhooks/voice/function-call",
                   {"name": "retry_payment", "args": {}, "call": {"call_id": "c"}})
    assert r.json()["status"] == "PENDING" and "still being processed" in r.json()["message"]
    monkeypatch.undo()
    reset_settings()