from shared.config import get_settings

from .base import VoiceProvider

_provider: VoiceProvider | None = None


def get_provider() -> VoiceProvider:
    global _provider
    if _provider is None:
        name = get_settings().voice_provider.lower()
        if name == "mock":
            from .mock import MockVoiceProvider
            _provider = MockVoiceProvider()
        elif name == "retell":
            from .retell import RetellAdapter   # added in Step 6B
            _provider = RetellAdapter()
        else:
            raise RuntimeError(f"unsupported VOICE_PROVIDER '{name}' (supported: mock, retell)")
    return _provider