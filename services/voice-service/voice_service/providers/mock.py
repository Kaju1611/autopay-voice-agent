"""Mock voice provider: simulates a phone call by sending signed webhook events to the webhook-service, exactly as a
real provider would, including real tool-call round trips. Lets the whole stack run with no phone and no API key."""
import asyncio
import json
import logging
import uuid

from shared.config import get_settings
from shared.utils import http
from shared.utils.security import sign

from ..scenarios import scenario_for
from .base import CallRequest, ProviderCall, VoiceProvider

logger = logging.getLogger("mock-provider")


class MockVoiceProvider(VoiceProvider):
    def __init__(self):
        self.calls: dict[str, dict] = {}
        self.ready: dict[str, asyncio.Event] = {}
        self.tasks: set[asyncio.Task] = set()

    async def create_call(self, req: CallRequest) -> ProviderCall:
        pcid = f"mock_call_{uuid.uuid4().hex[:12]}"
        self.calls[pcid] = {"req": req, "status": "registered"}
        self.ready[pcid] = asyncio.Event()
        task = asyncio.create_task(self._run(pcid, req))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return ProviderCall(pcid, "registered", {"mock": True})

    async def on_registered(self, provider_call_id: str) -> None:
        # the script only starts after our call row + Redis context exist, so the first webhook can never beat them
        if provider_call_id in self.ready:
            self.ready[provider_call_id].set()

    async def get_call(self, provider_call_id: str) -> ProviderCall:
        return ProviderCall(provider_call_id, self.calls.get(provider_call_id, {}).get("status", "unknown"))

    async def end_call(self, provider_call_id: str) -> bool:
        if provider_call_id in self.calls:
            self.calls[provider_call_id]["status"] = "ended"
        return True

    async def wait_idle(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def _post(self, path: str, body: dict) -> dict:
        raw = json.dumps(body).encode()
        r = await http.call("webhook", "POST", path, content=raw, headers={
            "Content-Type": "application/json", "X-Webhook-Signature": sign(raw)})
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {}

    async def _run(self, pcid: str, req: CallRequest) -> None:
        delay = get_settings().mock_call_step_delay_ms / 1000
        try:
            await asyncio.wait_for(self.ready[pcid].wait(), 10)
            steps = scenario_for(req.metadata["customer_id"])
            dv = req.dynamic_variables
            await asyncio.sleep(delay)
            if steps[0][0] == "noanswer":
                await self._post("/webhooks/voice/call-ended", {"event_id": f"{pcid}-end", "provider_call_id": pcid,
                                 "disconnection_reason": "dial_no_answer", "duration_ms": 0})
                return
            await self._post("/webhooks/voice/call-started", {"event_id": f"{pcid}-start", "provider_call_id": pcid})
            fmt = {"name": dv["customer_name"], "amount_fmt": dv["amount_formatted"], "reason": dv["failure_reason_text"]}
            last: dict = {}
            for i, step in enumerate(steps):
                await asyncio.sleep(delay)
                kind = step[0]
                if kind in ("agent", "user"):
                    await self._post("/webhooks/voice/transcript", {
                        "event_id": f"{pcid}-t{i}", "provider_call_id": pcid, "role": kind, "content": step[1].format(**fmt)})
                elif kind == "fn":
                    last = await self._post("/webhooks/voice/function-call", {
                        "event_id": f"{pcid}-f{i}", "name": step[1], "args": step[2], "call": {"call_id": pcid}})
                elif kind == "say_result":
                    ok = last.get("status") == "SUCCESS"
                    text = (f"Great news, the payment of {fmt['amount_fmt']} was successful. You'll receive a confirmation shortly."
                            if ok else "I'm sorry, the retry was unsuccessful. A support specialist can follow up with other options.")
                    await self._post("/webhooks/voice/transcript", {"event_id": f"{pcid}-t{i}", "provider_call_id": pcid,
                                                                    "role": "agent", "content": text})
            await asyncio.sleep(delay)
            await self._post("/webhooks/voice/call-ended", {
                "event_id": f"{pcid}-end", "provider_call_id": pcid, "disconnection_reason": "agent_hangup",
                "duration_ms": int(delay * 1000 * (len(steps) + 2))})
        except Exception:  # noqa: BLE001
            logger.exception("mock call script failed (expected until the webhook service exists in Step 7)")
        finally:
            self.calls[pcid]["status"] = "ended"