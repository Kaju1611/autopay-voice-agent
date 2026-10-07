from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://autopay:autopay@localhost:5432/autopay"
    redis_url: str = "redis://localhost:6379/0"

    # service-to-service shared secret (only the gateway is public)
    internal_token: str = "dev-internal-token"
    slow_request_ms: int = 500  # requests slower than this are logged as WARNING

        # --- mock payment provider ---
    mock_provider_latency_ms: int = 300        # simulated gateway round trip, makes latency visible in the demo
    mock_provider_default_outcome: str = ""    # SUCCESS | FAILED | DECLINED forces every retry; empty = per-payment setting
    max_retry_attempts: int = 3

        # --- service discovery (docker-compose names by default; override for local runs) ---
    customer_service_url: str = "http://customer-service:8001"
    payment_service_url: str = "http://payment-service:8002"
    voice_service_url: str = "http://voice-service:8003"
    webhook_service_url: str = "http://webhook-service:8004"
    recovery_service_url: str = "http://recovery-worker:8005"

    # --- latency: inter-service HTTP ---
    http_connect_timeout_s: float = 1.0
    http_default_timeout_s: float = 5.0

    # --- recovery ---
    # False (default): a call is only placed when someone explicitly requests one.
    # payment.failed events are ignored unless this is true. Never enable with a real provider unless every number is authorised.
    auto_recovery: bool = False
    
@lru_cache
def get_settings() -> Settings:
    return Settings()