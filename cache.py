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
import time

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Single shared connection pool — created once on first use.
_redis_pool: Optional[aioredis.Redis] = None
# Timestamp of last failure to connect to Redis. Used to fast-fail subsequent
# cache attempts for a short cooldown window to avoid repeatedly waiting on
# socket timeouts when Redis is down or unreachable.
_last_failed: float = 0.0
# Cooldown (seconds) after a failure during which cache calls will immediately
# return a miss instead of attempting to reconnect.
_FAIL_COOLDOWN = 10.0


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    # If we recently failed to connect, skip attempting again for a short
    # cooldown window — this prevents each incoming request from waiting
    # on a socket timeout when Redis is down.
    if _redis_pool is None and (_last_failed and (time.time() - _last_failed) < _FAIL_COOLDOWN):
        return None

    if _redis_pool is None:
        try:
            _redis_pool = aioredis.from_url(
                REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20,
                socket_connect_timeout=0.01,   # keep it snappy
                socket_timeout=0.01,
            )
            return _redis_pool
        except Exception:
            # Record failure time and return None so callers treat this as
            # a cache miss without blocking.
            nonlocal_flag = globals()
            nonlocal_flag['_last_failed'] = time.time()
            return None

    return _redis_pool


# ── FIX: all Redis operations are now wrapped in try/except so a missing or
#    unavailable Redis instance never crashes the application.  The app simply
#    falls back to no-cache mode and continues serving requests normally.
# ─────────────────────────────────────────────────────────────────────────────

async def cache_get(key: str) -> Optional[Any]:
    try:
        r = await get_redis()
        if r is None:
            return None
        raw = await r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        # Redis unavailable — treat as a cache miss; caller fetches from DB.
        return None


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    try:
        r = await get_redis()
        if r is None:
            return
        await r.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass  # Best-effort — skip caching when Redis is down.


async def invalidate_prefix(prefix: str) -> int:
    """Delete all Redis keys that start with `prefix:`. Returns count deleted."""
    try:
        r = await get_redis()
        if r is None:
            return 0
        keys = await r.keys(f"{prefix}:*")
        if keys:
            return await r.delete(*keys)
        return 0
    except Exception:
        # Redis unavailable — stale entries will expire on their own TTL.
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
        try:
            await _redis_pool.aclose()
        except Exception:
            pass
        _redis_pool = None