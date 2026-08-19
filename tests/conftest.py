"""Shared pytest configuration and fixtures.

The environment setup at the top of this module must run *before* anything imports
``app.config``, which is why it is here rather than in a fixture. pytest imports
conftest.py before collecting test modules, so this is the earliest reliable hook.
"""

import os

# Settings.validate_secrets used to bypass itself whenever "pytest" was in sys.modules.
# Keying a production security guard on a module-import side effect meant any process
# that happened to import pytest booted with the default SECRET_KEY. The bypass is now
# an explicit setting, declared here so it is greppable and cannot fire in production.
os.environ.setdefault("ARKON_ALLOW_DEFAULT_SECRET", "1")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_redis_rate_limiting(monkeypatch):
    """Stop unit tests from opening real Redis sockets.

    ``check_rate_limit`` fails open by design (it catches Exception and only logs), so
    without this fixture every rate-limited route silently takes the no-limiting branch
    after a socket timeout — making the suite slow and its result dependent on whether a
    Redis happens to be reachable. Tests that exercise the limiter itself should patch it
    back or call the internals directly; see tests/test_rate_limiter.py.
    """
    try:
        from app.services import rate_limiter
    except ImportError:  # pragma: no cover - rate limiter is optional at import time
        return

    async def _allow(*_args, **_kwargs):
        return None

    for name in ("check_rate_limit", "check_token_rate_limit"):
        if hasattr(rate_limiter, name):
            monkeypatch.setattr(rate_limiter, name, _allow)


@pytest.fixture
def anyio_backend():
    return "asyncio"
