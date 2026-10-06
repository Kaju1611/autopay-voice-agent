import redis.asyncio as aioredis

from shared.config import get_settings

_redis = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            get_settings().redis_url, decode_responses=True, max_connections=50,
            health_check_interval=30, socket_keepalive=True,
        )
    return _redis