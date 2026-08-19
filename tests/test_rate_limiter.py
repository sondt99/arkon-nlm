"""The counter and its TTL must be set atomically (issue #89).

`check_rate_limit` used to `INCR` and then, only when the returned count was 1, `EXPIRE`.
Two round-trips: if the EXPIRE was lost the key lived forever with no TTL, and the `count == 1`
guard meant no later call ever retried it. The counter then climbed monotonically until every
request 429'd forever — with `ttl == -1` also dropping the `Retry-After` header, so the client
could not even tell that the window had no end. It also built and `aclose()`d a fresh client
on every single check.

`check_rate_limit` is imported by name here so the autouse `_no_redis_rate_limiting` fixture
in conftest (which replaces the module attribute) cannot stub out the function under test.
"""

import pytest
from fastapi import HTTPException

from app.services import rate_limiter
from app.services.rate_limiter import check_rate_limit, check_token_rate_limit

_NO_KEY = -2
_NO_TTL = -1


class _FakePipeline:
    """MULTI/EXEC: queued commands, then applied in one step by execute()."""

    def __init__(self, redis: "_FakeRedis"):
        self._redis = redis
        self._queued: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def set(self, key, value, ex=None, nx=False):
        self._queued.append(("set", (key, value, ex, nx)))
        return self

    def incr(self, key):
        self._queued.append(("incr", (key,)))
        return self

    def ttl(self, key):
        self._queued.append(("ttl", (key,)))
        return self

    async def execute(self):
        results = []
        for name, args in self._queued:
            results.append(getattr(self._redis, f"_{name}")(*args))
        self._queued.clear()
        return results


class _FakeRedis:
    """In-memory Redis with an EXPIRE that can be made to fail, as a lost one would."""

    def __init__(self, expire_fails: bool = False):
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.expire_fails = expire_fails
        self.commands: list[str] = []
        self.pipelines: list[bool] = []
        self.closed = False

    # --- command implementations, shared with the pipeline ---

    def _set(self, key, value, ex, nx):
        self.commands.append("set")
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def _incr(self, key):
        self.commands.append("incr")
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def _ttl(self, key):
        self.commands.append("ttl")
        if key not in self.values:
            return _NO_KEY
        return self.ttls.get(key, _NO_TTL)

    # --- client surface ---

    def pipeline(self, transaction=False):
        self.pipelines.append(transaction)
        return _FakePipeline(self)

    async def incr(self, key):
        return self._incr(key)

    async def ttl(self, key):
        return self._ttl(key)

    async def expire(self, key, seconds):
        self.commands.append("expire")
        if self.expire_fails:
            raise ConnectionError("EXPIRE lost")
        self.ttls[key] = seconds
        return True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def redis(monkeypatch):
    """Install a fake client and hand it to the test."""
    client = _FakeRedis()

    async def _get_client():
        return client

    monkeypatch.setattr(rate_limiter, "_get_client", _get_client)
    monkeypatch.setattr(rate_limiter, "_client", None)
    return client


async def _check(key="arkon:test", limit=3, window=60):
    await check_rate_limit(key=key, max_requests=limit, window_seconds=window)


@pytest.mark.asyncio
async def test_a_fresh_key_gets_its_ttl_without_a_second_round_trip(redis):
    redis.expire_fails = True  # any standalone EXPIRE is a lockout waiting to happen

    await _check()

    assert redis.ttls["arkon:test"] == 60
    assert redis.pipelines == [True], "the commands were not sent as one MULTI/EXEC"
    assert "expire" not in redis.commands, (
        "the TTL still depends on a separate EXPIRE, which is the round-trip that gets lost"
    )


@pytest.mark.asyncio
async def test_a_lost_expire_cannot_leave_the_key_immortal(redis):
    """The reported failure mode end to end: unbounded counter, permanent 429."""
    redis.expire_fails = True

    for _ in range(3):
        await _check()

    assert redis._ttl("arkon:test") > 0, (
        "the counter has no expiry, so it will climb past the limit and stay there"
    )

    with pytest.raises(HTTPException) as exc:
        await _check()

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "60"}


@pytest.mark.asyncio
async def test_an_existing_ttl_less_key_is_healed_rather_than_locking_the_user_out(redis):
    """A key left behind by the old two-round-trip version must not be a life sentence."""
    redis.values["arkon:test"] = 500  # way over the limit, and
    assert redis._ttl("arkon:test") == _NO_TTL  # no expiry at all

    with pytest.raises(HTTPException) as exc:
        await _check()

    assert exc.value.status_code == 429
    assert redis.ttls["arkon:test"] == 60, (
        "the key still has no TTL, so this principal is rate limited forever"
    )
    assert exc.value.headers["Retry-After"] == "60"


@pytest.mark.asyncio
async def test_the_window_is_not_extended_by_later_requests(redis):
    await _check()
    redis.ttls["arkon:test"] = 12  # 48 seconds into the window

    await _check()

    assert redis.ttls["arkon:test"] == 12, "SET is resetting the TTL instead of using NX"
    assert redis.values["arkon:test"] == 2


@pytest.mark.asyncio
async def test_requests_under_the_limit_are_allowed(redis):
    for _ in range(3):
        await _check()
    assert redis.values["arkon:test"] == 3


@pytest.mark.asyncio
async def test_retry_after_is_present_on_every_429(redis):
    for _ in range(3):
        await _check()

    for expected in ("60", "60"):
        with pytest.raises(HTTPException) as exc:
            await _check()
        assert exc.value.headers == {"Retry-After": expected}


@pytest.mark.asyncio
async def test_one_client_is_shared_across_checks(monkeypatch):
    """A per-check client threw away the connection the pool exists to reuse."""
    import redis.asyncio as aioredis

    built = []

    def _factory(**kwargs):
        client = _FakeRedis()
        built.append(client)
        return client

    monkeypatch.setattr(aioredis, "Redis", _factory)
    monkeypatch.setattr(rate_limiter, "_client", None)

    await _check(key="arkon:shared")
    await _check(key="arkon:shared")

    assert len(built) == 1, f"a new Redis client per check ({len(built)} built)"
    assert built[0].closed is False, "the shared client was closed while still in use"
    assert built[0].values["arkon:shared"] == 2

    monkeypatch.setattr(rate_limiter, "_client", None)


@pytest.mark.asyncio
async def test_an_unreachable_redis_fails_open(monkeypatch):
    async def _boom():
        raise ConnectionError("no redis")

    monkeypatch.setattr(rate_limiter, "_get_client", _boom)

    await _check()  # must not raise


@pytest.mark.asyncio
async def test_the_token_limiter_keys_on_the_employee(redis, monkeypatch):
    import uuid

    # check_token_rate_limit resolves check_rate_limit through the module globals, which the
    # autouse fixture has stubbed out.
    monkeypatch.setattr(rate_limiter, "check_rate_limit", check_rate_limit)
    employee_id = uuid.uuid4()

    await check_token_rate_limit(employee_id, "export", max_requests=1)

    assert f"arkon:api_rate:export:{employee_id}" in redis.values

    with pytest.raises(HTTPException) as exc:
        await check_token_rate_limit(employee_id, "export", max_requests=1)
    assert exc.value.status_code == 429
