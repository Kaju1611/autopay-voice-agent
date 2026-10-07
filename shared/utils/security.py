import hmac

from fastapi import Header, HTTPException

from shared.config import get_settings


async def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    """Protects service-to-service routes (everything except /health and /metrics)."""
    if not x_internal_token or not hmac.compare_digest(x_internal_token, get_settings().internal_token):
        raise HTTPException(status_code=401, detail="internal token required")