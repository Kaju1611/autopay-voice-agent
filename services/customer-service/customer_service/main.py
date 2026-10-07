from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import get_session
from shared.models import Customer
from shared.schemas.models import CustomerIn, CustomerOut
from shared.utils import logging as slog
from shared.utils.app import create_app
from shared.utils.security import require_internal

app = create_app("customer-service", title="Customer Service", uses_redis=False)
router = APIRouter(prefix="/customers", dependencies=[Depends(require_internal)], tags=["customers"])


@router.get("", response_model=list[CustomerOut])
async def list_customers(limit: int = Query(100, le=500), offset: int = 0, s: AsyncSession = Depends(get_session)):
    rows = await s.scalars(select(Customer).order_by(Customer.customer_id).limit(limit).offset(offset))
    return rows.all()


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: str, s: AsyncSession = Depends(get_session)):
    slog.bind(customer_id=customer_id)
    row = await s.scalar(select(Customer).where(Customer.customer_id == customer_id))
    if not row:
        raise HTTPException(404, "customer not found")
    return row


@router.get("/{customer_id}/eligibility")
async def call_eligibility(customer_id: str, s: AsyncSession = Depends(get_session)):
    """One cheap lookup the recovery worker uses before dialling: is this customer allowed to be called?"""
    row = await s.scalar(select(Customer).where(Customer.customer_id == customer_id))
    if not row:
        raise HTTPException(404, "customer not found")
    reasons = []
    if not row.call_permission:
        reasons.append("NO_CALL_PERMISSION")
    if not row.autopay_enabled:
        reasons.append("AUTOPAY_DISABLED")
    return {"customer_id": customer_id, "name": row.name, "eligible": not reasons, "reasons": reasons}


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(body: CustomerIn, s: AsyncSession = Depends(get_session)):
    if await s.scalar(select(Customer.id).where(Customer.customer_id == body.customer_id)):
        raise HTTPException(409, "customer already exists")
    row = Customer(**body.model_dump())
    s.add(row)
    await s.commit()
    return row


app.include_router(router)