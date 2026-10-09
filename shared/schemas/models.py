from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CustomerIn(BaseModel):
    customer_id: str = Field(pattern=r"^CUS\d{3,6}$")
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(pattern=r"^\+\d{8,15}$")           # E.164
    email: str = Field(max_length=160, pattern=r"^[^@\s]+@[^@\s]+$")
    autopay_enabled: bool = True
    call_permission: bool = True

class CustomerOut(ORM, CustomerIn):
    created_at: datetime | None = None
    
MockOutcome = Literal["SUCCESS", "FAILED", "DECLINED"]


class PaymentOut(ORM):
    payment_id: str
    customer_id: str
    amount: float
    currency: str
    status: str
    failure_reason: str | None = None
    attempted_at: datetime | None = None
    updated_at: datetime | None = None
    retry_count: int = 0
    scheduled_retry_at: datetime | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _dec(cls, v):
        return float(v) if isinstance(v, Decimal) else v


class ScheduleRetryIn(BaseModel):
    retry_at: datetime | None = None  # default: now + 24h


class MockConfigIn(BaseModel):
    outcome: MockOutcome | None = None  # null clears the override

class RecoveryAttemptOut(ORM):
    id: int
    customer_id: str
    payment_id: str | None = None
    call_id: str | None = None
    action: str
    result: str
    created_at: datetime


class RecoveryRequestIn(BaseModel):
    payment_id: str | None = None

class CallOut(ORM):
    call_id: str
    customer_id: str
    payment_id: str | None = None
    provider_call_id: str | None = None
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration: float | None = None
    outcome: str | None = None
    created_at: datetime | None = None


class CallEventOut(ORM):
    event_type: str
    payload: dict
    created_at: datetime


class CallDetailOut(CallOut):
    transcript: str | None = None
    events: list[CallEventOut] = []

class FunctionCallIn(BaseModel):
    name: Literal["get_customer_payment_status", "confirm_identity", "retry_payment", "schedule_payment_retry",
                  "request_human_agent", "record_outcome", "end_call"]
    args: dict = Field(default_factory=dict)
    provider_call_id: str