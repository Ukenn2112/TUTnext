# core/redis.py
# Async Redis client factory.
from redis import asyncio as aioredis


def get_redis(redis_url: str = "redis://localhost:6379") -> aioredis.Redis:
    """Return an async Redis client connected to *redis_url*."""
    return aioredis.from_url(redis_url)
