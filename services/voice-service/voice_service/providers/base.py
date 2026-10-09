"""Voice provider abstraction. The rest of the system only ever imports this interface,
so switching vendors means writing one new adapter file."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CallRequest:
    to_number: str
    from_number: str
    agent_id: str
    dynamic_variables: dict[str, str]   # pre-loaded context: the agent can open the call with ZERO lookups
    metadata: dict = field(default_factory=dict)


@dataclass
class ProviderCall:
    provider_call_id: str
    status: str
    raw: dict = field(default_factory=dict)


class VoiceProvider(ABC):
    @abstractmethod
    async def create_call(self, req: CallRequest) -> ProviderCall: ...

    @abstractmethod
    async def get_call(self, provider_call_id: str) -> ProviderCall: ...

    @abstractmethod
    async def end_call(self, provider_call_id: str) -> bool: ...

    async def on_registered(self, provider_call_id: str) -> None:
        """Called once our call row and context cache exist. Real providers ignore it; the mock starts its script here."""