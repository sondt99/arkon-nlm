"""Redis-backed rate limiter for API endpoints."""

import uuid

from fastapi import HTTPException
from loguru import logger


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
        import redis.asyncio as aioredis

        from app.config import settings

        r = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            socket_connect_timeout=2,
        )
        try:
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, window_seconds)
            if count > max_requests:
                ttl = await r.ttl(key)
                logger.warning("Rate limit exceeded: key={} count={}", key, count)
                raise HTTPException(
                    status_code=429,
                    detail=error_message,
                    headers={"Retry-After": str(max(1, ttl))} if ttl > 0 else None,
                )
        finally:
            await r.aclose()
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
