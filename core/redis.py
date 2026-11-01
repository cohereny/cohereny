"""
core/redis.py

Redis connection — singleton for the entire application lifetime.

Analogous to core.db, the connection is created on app startup (api/main.py lifespan)
and closed on shutdown. Uses the redis-py async API.

decode_responses=True: all Redis values are returned as Python strings (not bytes)
— simplifies handling of JSON-cached values.

DB index separation (via redis_url in config):
  DB 0 → prod   (redis://host:6379/0)
  DB 1 → dev    (redis://host:6379/1)
  DB 2 → test   (redis://host:6379/2)
Three stage containers share one Redis instance without collisions.

Called by:
  - api.main      (create_redis / close_redis in lifespan)
  - api/v1/*.py   (get_redis in route handlers, once implemented)

Dependencies:
  - redis[asyncio]    (async Redis client)
  - api.config        (settings.redis_url)
"""
import redis.asyncio as aioredis
from api.config import settings

_redis: aioredis.Redis | None = None


async def create_redis() -> None:
    """Create the Redis connection. Called once on app startup."""
    global _redis
    _redis = await aioredis.from_url(settings.redis_url, decode_responses=True)


async def close_redis() -> None:
    """Close the connection cleanly on shutdown."""
    if _redis:
        await _redis.aclose()


async def get_redis() -> aioredis.Redis:
    """Provide the Redis client to route handlers. Raises RuntimeError if not initialized."""
    if _redis is None:
        raise RuntimeError("Redis not initialized")
    return _redis
