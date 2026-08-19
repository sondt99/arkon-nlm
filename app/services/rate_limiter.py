"""Redis-backed rate limiter for API endpoints."""

import uuid
from typing import Any, Optional

from fastapi import HTTPException
from loguru import logger

# One client per process instead of one per check. Every call used to construct an
# `aioredis.Redis` and `aclose()` it again, so a rate-limited endpoint paid a fresh TCP
# (plus AUTH) handshake per request and then discarded the connection the pool exists to
# reuse. redis.asyncio binds its connections to the loop that created them and the API runs
# one loop per process, so a module-level client is safe here.
_client: Optional[Any] = None


async def _get_client():
    """Return the shared Redis client, constructing it on first use.

    No lock: there is no await between the check and the assignment and the Redis
    constructor is synchronous (the connection pool is lazy), so a single-threaded event loop
    cannot interleave two initialisations. A module-level asyncio.Lock would meanwhile bind
    itself to whichever loop first awaited it.
    """
    global _client
    if _client is None:
        import redis.asyncio as aioredis

        from app.config import settings

        _client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            socket_connect_timeout=2,
        )
    return _client


async def check_rate_limit(
    *,
    key: str,
    max_requests: int,
    window_seconds: int,
    error_message: str = "Rate limit exceeded. Please try again later.",
) -> None:
    """Increment a Redis counter and raise 429 if the limit is exceeded.

    Fails open: if Redis is unavailable the request is allowed through.
    """
    try:
        r = await _get_client()

        # Seeding the counter and its TTL in one MULTI/EXEC. As two round-trips
        # (INCR, then EXPIRE only when `count == 1`) a dropped EXPIRE left the key with no
        # expiry and nothing ever retried it, so the counter climbed monotonically and every
        # later request 429'd forever — with `ttl == -1` also dropping the Retry-After
        # header, the client had no way to learn the window had no end.
        async with r.pipeline(transaction=True) as pipe:
            pipe.set(key, 0, ex=window_seconds, nx=True)
            pipe.incr(key)
            pipe.ttl(key)
            _, count, ttl = await pipe.execute()

        if ttl is None or ttl < 0:
            # A key with no expiry can only be one left behind by a lost EXPIRE. Healing it
            # here is what turns a permanent lockout back into a bounded window.
            await r.expire(key, window_seconds)
            ttl = window_seconds

        if count > max_requests:
            logger.warning("Rate limit exceeded: key={} count={}", key, count)
            raise HTTPException(
                status_code=429,
                detail=error_message,
                headers={"Retry-After": str(max(1, ttl))},
            )
    except HTTPException:
        raise
    except Exception as exc:
        # Fail open by design (Redis outage must not block logins), but never
        # silently: an unreachable Redis means rate limiting is OFF.
        logger.error("Rate limiter unavailable (failing open): key={} error={}", key, exc)


async def check_token_rate_limit(
    employee_id: uuid.UUID,
    endpoint: str,
    max_requests: int = 60,
    window_seconds: int = 60,
) -> None:
    """Per-token rate limit for API endpoints that invoke external LLMs."""
    key = f"arkon:api_rate:{endpoint}:{employee_id}"
    await check_rate_limit(
        key=key,
        max_requests=max_requests,
        window_seconds=window_seconds,
        error_message="Rate limit exceeded for this API token. Please slow down.",
    )
