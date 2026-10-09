import hashlib
import hmac

from fastapi import Header, HTTPException

from shared.config import get_settings


async def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    """Protects service-to-service routes (everything except /health and /metrics)."""
    if not x_internal_token or not hmac.compare_digest(x_internal_token, get_settings().internal_token):
        raise HTTPException(status_code=401, detail="internal token required")


def sign(body: bytes, secret: str | None = None) -> str:
    """HMAC-SHA256 signature the provider (or our mock) puts in the X-Webhook-Signature header."""
    secret = secret or get_settings().webhook_secret
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_webhook(headers, body: bytes) -> bool:
    """Accept an HMAC body signature, or a static shared-secret header for providers
    that can only send custom headers (such as Retell custom functions)."""
    secret = get_settings().webhook_secret
    sig = headers.get("x-webhook-signature")
    if sig and hmac.compare_digest(sig, sign(body, secret)):
        return True
    static = headers.get("x-webhook-secret")
    return bool(static and hmac.compare_digest(static, secret))