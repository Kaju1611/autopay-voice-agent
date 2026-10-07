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