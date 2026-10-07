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


@lru_cache
def get_settings() -> Settings:
    return Settings()