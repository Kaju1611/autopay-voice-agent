import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import get_session
from shared.models import Call, CallEvent
from shared.schemas.models import CallDetailOut, CallOut
from shared.utils.app import create_app
from shared.utils.security import require_internal

from . import service
from .providers.factory import get_provider

logger = logging.getLogger("voice")


async def _startup(app):
    get_provider()  # build the provider (and its pooled HTTP client) before the first call


app = create_app("voice-service", title="Voice Service", on_startup=_startup)
calls = APIRouter(prefix="/calls", dependencies=[Depends(require_internal)], tags=["calls"])


class StartCallIn(BaseModel):
    payment_id: str | None = None


@calls.get("", response_model=list[CallOut])
async def list_calls(customer_id: str | None = None, limit: int = Query(100, le=500), s: AsyncSession = Depends(get_session)):
    q = select(Call).order_by(Call.id.desc()).limit(limit)
    if customer_id:
        q = q.where(Call.customer_id == customer_id)
    return (await s.scalars(q)).all()


@calls.get("/{call_id}", response_model=CallDetailOut)
async def get_call(call_id: str, s: AsyncSession = Depends(get_session)):
    call = await s.scalar(select(Call).where(Call.call_id == call_id))
    if not call:
        raise HTTPException(404, "call not found")
    events = (await s.scalars(select(CallEvent).where(CallEvent.call_id == call_id).order_by(CallEvent.id))).all()
    out = CallDetailOut.model_validate(call)
    out.events = [e for e in events if e.event_type != "transcript"]
    if not out.transcript:  # live view while the call is still running
        out.transcript = "\n".join(
            f"{'Agent' if e.payload.get('role') == 'agent' else 'Customer'}: {e.payload.get('content', '')}"
            for e in events if e.event_type == "transcript")
    return out


@calls.post("/{customer_id}", response_model=CallOut, status_code=202)
async def start_call(customer_id: str, body: StartCallIn | None = None):
    return await service.start_call(customer_id, body.payment_id if body else None)


@calls.post("/{call_id}/end")
async def end_call(call_id: str, s: AsyncSession = Depends(get_session)):
    call = await s.scalar(select(Call).where(Call.call_id == call_id))
    if not call or not call.provider_call_id:
        raise HTTPException(404, "call not found")
    return {"forced_hangup": await get_provider().end_call(call.provider_call_id)}


app.include_router(calls)