"""Retell AI adapter (https://docs.retellai.com)."""
import httpx

from shared.config import get_settings

from .base import CallRequest, ProviderCall, VoiceProvider

BASE = "https://api.retellai.com"


class RetellAdapter(VoiceProvider):
    def __init__(self):
        s = get_settings()
        if not s.voice_provider_api_key:
            raise RuntimeError("VOICE_PROVIDER_API_KEY is required for VOICE_PROVIDER=retell")
        # long-lived client: the TLS connection is reused, so "start call" doesn't pay a handshake every time
        self.client = httpx.AsyncClient(
            base_url=BASE, headers={"Authorization": f"Bearer {s.voice_provider_api_key}"},
            timeout=httpx.Timeout(8.0, connect=2.0), limits=httpx.Limits(max_keepalive_connections=5))

    async def create_call(self, req: CallRequest) -> ProviderCall:
        body = {
            "from_number": req.from_number, "to_number": req.to_number,
            "override_agent_id": req.agent_id or None,
            "retell_llm_dynamic_variables": req.dynamic_variables,   # string values only
            "metadata": req.metadata,
        }
        r = await self.client.post("/v2/create-phone-call", json={k: v for k, v in body.items() if v})
        r.raise_for_status()
        d = r.json()
        return ProviderCall(d["call_id"], d.get("call_status", "registered"), d)

    async def get_call(self, provider_call_id: str) -> ProviderCall:
        r = await self.client.get(f"/v2/get-call/{provider_call_id}")
        r.raise_for_status()
        d = r.json()
        return ProviderCall(provider_call_id, d.get("call_status", "unknown"), d)

    async def end_call(self, provider_call_id: str) -> bool:
        # Retell ends calls through the agent's built-in "End Call" tool (set up in their dashboard), not over REST here.
        return False