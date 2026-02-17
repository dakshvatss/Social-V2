"""
Redis async caching utilities.

Usage:
    from cache import cache_response, invalidate_prefix, get_redis

    # Cache a route for 5 minutes:
    @app.get("/api/stats")
    @cache_response(prefix="stats", ttl=300)
    async def stats(...):
        ...

    # Bust all stats caches after a write:
    await invalidate_prefix("stats")
"""

import os
import json
import hashlib
import functools
from typing import Any, Callable, Optional

import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Single shared connection pool — created once on first use.
_redis_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _redis_pool


async def cache_get(key: str) -> Optional[Any]:
    r = await get_redis()
    raw = await r.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    r = await get_redis()
    await r.setex(key, ttl, json.dumps(value, default=str))


async def invalidate_prefix(prefix: str) -> int:
    """Delete all Redis keys that start with `prefix:`. Returns count deleted."""
    r = await get_redis()
    keys = await r.keys(f"{prefix}:*")
    if keys:
        return await r.delete(*keys)
    return 0


def _make_cache_key(prefix: str, kwargs: dict) -> str:
    """Build a deterministic cache key from a prefix + the endpoint's query params."""
    # Sort for determinism, then hash to keep key length bounded.
    payload = json.dumps(kwargs, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def cache_response(prefix: str, ttl: int = 300):
    """
    Decorator for async FastAPI route functions.
    Caches the return value in Redis for `ttl` seconds.
    The cache key is derived from the function's keyword arguments,
    so different query params get different cache entries.

    Example:
        @cache_response(prefix="stats", ttl=300)
        async def stats(db: Session = Depends(get_db)):
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Strip FastAPI injected dependencies (db, request) from cache key.
            cache_kwargs = {
                k: v for k, v in kwargs.items()
                if k not in ("db", "request", "background_tasks")
            }
            key = _make_cache_key(prefix, cache_kwargs)

            cached = await cache_get(key)
            if cached is not None:
                return cached

            result = await func(*args, **kwargs)
            await cache_set(key, result, ttl=ttl)
            return result

        return wrapper
    return decorator


async def close_redis() -> None:
    global _redis_pool
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None
