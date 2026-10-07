from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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