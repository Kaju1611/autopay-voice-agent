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

@lru_cache
def get_settings() -> Settings:
    return Settings()