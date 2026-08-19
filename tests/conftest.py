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

import uuid as _uuid  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import UniqueConstraint  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.sql import Delete, Select, Update, operators  # noqa: E402
from sqlalchemy.sql.elements import BindParameter  # noqa: E402
from sqlalchemy.sql.schema import Column  # noqa: E402


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


# ---------------------------------------------------------------------------
# Route introspection
#
# Handlers in this codebase take their guard as a default argument
# (`_user: Employee = require_permission("org:roles:read")`), so calling a handler
# directly in a test bypasses the guard entirely. These helpers pull the guard object
# off the *registered route* instead, so a test can call it and observe a real 403.
# Deleting `require_permission(...)` from a handler makes the lookup return nothing,
# which fails the assertion — that is the whole point.
# ---------------------------------------------------------------------------

_ROUTE_INDEX: dict[tuple[str, str], object] = {}


def _build_route_index() -> dict[tuple[str, str], object]:
    from fastapi.routing import APIRoute

    from app.main import app

    # FastAPI 0.141 includes routers lazily: app.routes holds _IncludedRouter
    # placeholders and the real APIRoute objects (with resolved dependency trees) are
    # only built when something forces resolution. Building the OpenAPI schema forces
    # it; without this call the index would hold 3 routes instead of 170. See
    # tests/test_route_auth_coverage.py for the longer version of this note.
    app.openapi()

    index: dict[tuple[str, str], object] = {}
    for entry in app.routes:
        if isinstance(entry, APIRoute):
            routes = [(entry.path, entry)]
        else:
            original = getattr(entry, "original_router", None)
            if original is None:
                continue
            prefix = getattr(getattr(entry, "include_context", None), "prefix", "") or ""
            routes = [
                (f"{prefix}{r.path}", r)
                for r in original.routes
                if isinstance(r, APIRoute)
            ]
        for path, route in routes:
            for method in route.methods or ():
                index[(method.upper(), path)] = route
    return index


def _permission_guards(dependant, found=None) -> list[tuple[str, object]]:
    """Collect (permission, guard_callable) for every require_permission on a route.

    ``require_permission(p)`` returns ``Depends(_check)`` where ``_check`` closes over
    ``p``, so the permission string is read out of the closure rather than out of the
    source text. A commented-out guard has no closure and is therefore invisible here,
    which a source-string assertion could not distinguish.
    """
    if found is None:
        found = []
    call = dependant.call
    if getattr(call, "__name__", None) == "_check" and call.__closure__:
        cells = dict(zip(call.__code__.co_freevars, call.__closure__))
        cell = cells.get("permission")
        if cell is not None:
            found.append((cell.cell_contents, call))
    for sub in dependant.dependencies:
        _permission_guards(sub, found)
    return found


@pytest.fixture(scope="session")
def route_guards():
    """Look up the permission guards attached to a live route.

    Usage: ``route_guards("PUT", "/api/roles/{role_id}")`` ->
    ``[("org:roles:manage", <coroutine function>)]``. Await the callable with a user
    object to exercise the real check.
    """
    if not _ROUTE_INDEX:
        _ROUTE_INDEX.update(_build_route_index())

    def _lookup(method: str, path: str) -> list[tuple[str, object]]:
        route = _ROUTE_INDEX.get((method.upper(), path))
        assert route is not None, (
            f"no route registered for {method.upper()} {path} — the path or method in "
            f"this test is stale, or the router is no longer included"
        )
        return _permission_guards(route.dependant)

    return _lookup


# ---------------------------------------------------------------------------
# In-memory stand-in for app.database.async_session_factory
#
# The arq jobs in app/worker.py are not callable without a session factory: every one
# of them opens `async with async_session_factory() as session` as its first statement,
# and the error handlers deliberately open a *second*, independent session so the status
# write survives a rollback. There is no live PostgreSQL in this suite, so the factory is
# replaced with the harness below.
#
# It is deliberately more than a mock. Three of the bugs the worker tests exist for are
# only reachable if the fake knows a little SQL:
#   * the non-idempotent source_images insert needs UniqueConstraint enforcement at
#     flush time and a DELETE that actually removes rows,
#   * run_map_phase's resume logic re-SELECTs the rows it just added,
#   * caption_images_task writes through an UPDATE statement rather than the ORM.
#
# Known limits, so tests do not assert things it cannot model:
#   * Attribute writes on seeded objects are visible immediately; there is no
#     transaction snapshot and `__aexit__` does not roll anything back. Assert on
#     `db.commits` when a test needs to prove that nothing was *committed*.
#   * Only `col == value` and `col.in_([...])` are understood in WHERE clauses. Any
#     other operator is ignored (not inverted) — `notin_` therefore filters nothing.
# ---------------------------------------------------------------------------


class FakeResult:
    """Enough of sqlalchemy's Result/ScalarResult for the worker's call sites."""

    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        assert len(self._rows) <= 1, "scalar_one_or_none() matched more than one row"
        return self._rows[0] if self._rows else None


def _where_filters(clause) -> list[tuple[str, object, bool]]:
    """(column, value, is_membership) for each `col == v` / `col.in_([...])` term."""
    if clause is None:
        return []
    terms = list(getattr(clause, "clauses", None) or [clause])
    filters: list[tuple[str, object, bool]] = []
    for term in terms:
        left = getattr(term, "left", None)
        right = getattr(term, "right", None)
        op = getattr(term, "operator", None)
        if not (isinstance(left, Column) and isinstance(right, BindParameter)):
            continue
        if op is operators.eq:
            filters.append((left.name, right.value, False))
        elif op is operators.in_op:
            filters.append((left.name, right.value, True))
    return filters


def _matches(row, filters) -> bool:
    for name, value, is_membership in filters:
        actual = getattr(row, name, None)
        if is_membership:
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True


def _table_name(row) -> object:
    table = getattr(type(row), "__table__", None)
    return getattr(table, "name", None)


def _unique_keys(row) -> list[tuple]:
    table = getattr(type(row), "__table__", None)
    if table is None:
        return []
    return [
        (table.name, c.name, tuple(getattr(row, col.name, None) for col in c.columns))
        for c in table.constraints
        if isinstance(c, UniqueConstraint)
    ]


class FakeDB:
    """The shared store behind every FakeSession handed out by `factory()`."""

    def __init__(self):
        self.rows: dict[tuple[str, str], object] = {}
        self.inserted: list[object] = []
        self.deleted: list[object] = []
        self.statements: list[object] = []
        # table name -> list of rows, or callable(statement) -> rows. WHERE filtering and
        # single-column projection are still applied to whatever this returns.
        self.select_rows: dict[str, object] = {}
        self.commits = 0
        self.flushes = 0
        self.sessions_opened = 0
        self._live_unique: set[tuple] = set()
        self._pending: list[object] = []

    # --- seeding ---------------------------------------------------------

    def seed(self, model, row):
        """Make `session.get(model, row.id)` return `row`. Accepts stub objects."""
        self.rows[(model.__name__, str(row.id))] = row
        return row

    def insert(self, row):
        """Seed a mapped row as if a previous run had already flushed it."""
        if getattr(row, "id", None) is None:
            row.id = _uuid.uuid4()
        self.inserted.append(row)
        self.rows[(type(row).__name__, str(row.id))] = row
        self._live_unique.update(_unique_keys(row))
        return row

    def table_rows(self, name: str) -> list[object]:
        return [r for r in self.inserted if _table_name(r) == name]

    def factory(self):
        self.sessions_opened += 1
        return FakeSession(self)

    # --- statement execution ---------------------------------------------

    def execute(self, statement):
        self.statements.append(statement)
        if isinstance(statement, Delete):
            return self._delete(statement)
        if isinstance(statement, Update):
            return self._update(statement)
        if isinstance(statement, Select):
            return self._select(statement)
        return FakeResult([])

    def _select(self, statement):
        froms = statement.get_final_froms()
        if not froms:
            # e.g. select(func.pg_advisory_xact_lock(...)) — no table, no rows.
            return FakeResult([])
        override = self.select_rows.get(froms[0].name)
        if override is None:
            rows = self.table_rows(froms[0].name)
        elif callable(override):
            rows = list(override(statement))
        else:
            rows = list(override)
        rows = [r for r in rows if _matches(r, _where_filters(statement.whereclause))]
        columns = list(statement.selected_columns)
        if len(columns) == 1 and isinstance(columns[0], Column):
            return FakeResult([getattr(r, columns[0].name, None) for r in rows])
        return FakeResult(rows)

    def _delete(self, statement):
        filters = _where_filters(statement.whereclause)
        gone = [
            r for r in self.inserted
            if _table_name(r) == statement.table.name and _matches(r, filters)
        ]
        for row in gone:
            self.inserted.remove(row)
            self.rows.pop((type(row).__name__, str(getattr(row, "id", None))), None)
            for key in _unique_keys(row):
                self._live_unique.discard(key)
        self.deleted.extend(gone)
        return FakeResult([])

    def _update(self, statement):
        filters = _where_filters(statement.whereclause)
        values = {
            col.name: getattr(bind, "value", bind)
            for col, bind in statement._values.items()
        }
        for row in self.inserted:
            if _table_name(row) == statement.table.name and _matches(row, filters):
                for column, value in values.items():
                    setattr(row, column, value)
        return FakeResult([])

    # --- unit of work -----------------------------------------------------

    def flush(self):
        self.flushes += 1
        pending, self._pending = self._pending, []
        for row in pending:
            keys = _unique_keys(row)
            for key in keys:
                if key in self._live_unique:
                    raise IntegrityError(
                        f'duplicate key value violates unique constraint "{key[1]}"',
                        None,
                        Exception(f"UniqueViolation: {key[2]}"),
                    )
            if hasattr(row, "id") and getattr(row, "id", None) is None:
                row.id = _uuid.uuid4()
            self._live_unique.update(keys)
            self.inserted.append(row)
            if getattr(row, "id", None) is not None:
                self.rows[(type(row).__name__, str(row.id))] = row


class FakeSession:
    """One `async with async_session_factory() as session` block."""

    def __init__(self, db: FakeDB):
        self.db = db
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        self.closed = True
        return False

    async def get(self, model, pk, **_kwargs):
        return self.db.rows.get((model.__name__, str(pk)))

    async def execute(self, statement, *_args, **_kwargs):
        return self.db.execute(statement)

    def add(self, row):
        self.db._pending.append(row)

    async def flush(self, *_args, **_kwargs):
        self.db.flush()

    async def commit(self):
        self.db.flush()
        self.db.commits += 1

    async def rollback(self):
        self.db._pending.clear()

    async def refresh(self, *_args, **_kwargs):
        return None

    async def delete(self, row):
        if row in self.db.inserted:
            self.db.inserted.remove(row)
        self.db.rows.pop((type(row).__name__, str(getattr(row, "id", None))), None)
        self.db.deleted.append(row)

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_db(monkeypatch):
    """Replace `app.database.async_session_factory` with the in-memory harness.

    Patched on the module rather than on a caller, because every worker task and
    ProgressTracker.update re-imports it from `app.database` at call time.
    """
    import app.database

    db = FakeDB()
    monkeypatch.setattr(app.database, "async_session_factory", db.factory)
    return db

# ---------------------------------------------------------------------------
# An opt-in, real-Postgres test tier.
#
# WHY THIS EXISTS
#
# The main suite runs ~920 tests in about six seconds because it has no database: the
# `FakeDB` in conftest.py understands `col == v` and `col.in_([...])`, and its own docstring
# admits `notin_` "filters nothing". That trade is worth keeping — most tests do not need
# Postgres, and a six-second suite gets run.
#
# But it means a whole class of defect is *structurally* invisible, no matter how many tests
# are added. An independent review built a real database from these migrations and immediately
# found four bugs the green suite could not see:
#
#   - MissingGreenlet on six MCP tools — lazy relationships resolve eagerly against a fake
#     session, so the failure cannot occur (#121)
#   - a lost update on the wiki `_log` page — no real concurrency, no transaction isolation
#     (#123)
#   - `LIMIT -1` aborting a transaction — no real SQL execution (#88)
#   - row-visibility filters — `notin_` silently matches everything
#
# WHAT BELONGS HERE
#
# Only tests the fake cannot express: greenlet/lazy-load behaviour, concurrent
# read-modify-write, CHECK-constraint rejection, `notin_`/scope filters, real LIMIT/OFFSET
# bounds. Anything that can be tested against the fake should stay in the fast suite.
#
# HOW IT SKIPS
#
# `pytest.mark.postgres` plus a session fixture that skips cleanly when no database is
# reachable, so `pytest -q` still runs anywhere with no setup. Point it at a database with:
#
#     ARKON_TEST_DATABASE_URL=postgresql+asyncpg://user:pw@localhost:5432/arkon_test
#
# or let it use the compose Postgres. The fixture runs `alembic upgrade head` itself, so the
# schema under test is the one the migrations actually produce — not a `create_all()`
# approximation, which would not have caught #85's constraints or #41's backfills.
# ---------------------------------------------------------------------------

TEST_DB_ENV = "ARKON_TEST_DATABASE_URL"

# Deliberately NOT the app's own DATABASE_URL. Pointing this tier at a real deployment
# would run `alembic upgrade head` and DELETE rows in the fixtures below.
_DEFAULT = "postgresql+asyncpg://postgres:postgres@localhost:5432/arkon_test"


def _url() -> str:
    return os.environ.get(TEST_DB_ENV, _DEFAULT)


async def _reachable(url: str) -> bool:
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except Exception:
        return False
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """The URL for this tier, or skip the whole tier if nothing is listening.

    Skipping rather than failing is the point: the fast suite must keep running on a laptop
    with no Docker, or nobody will run it.
    """
    import asyncio

    url = _url()
    if not asyncio.run(_reachable(url)):
        pytest.skip(
            f"no Postgres at {url.rsplit('@', 1)[-1]} — set {TEST_DB_ENV} or start the "
            "compose database to run the real-database tier"
        )
    return url


@pytest.fixture(scope="session")
def migrated_postgres(postgres_url: str) -> str:
    """Apply the real migrations once per session.

    `alembic upgrade head`, not `Base.metadata.create_all()`. The difference matters: the
    CHECK constraints from migration 034 and the backfills from 014/018 exist only in the
    migrations, so a `create_all()` schema would silently skip exactly the things worth
    testing.
    """
    import subprocess
    import sys

    env = {**os.environ, "DATABASE_URL": postgres_url, "ARKON_ALLOW_DEFAULT_SECRET": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        pytest.fail(
            "alembic upgrade head failed against the test database:\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return postgres_url


@pytest_asyncio.fixture
async def pg_sessionmaker(migrated_postgres: str):
    """A real async sessionmaker, configured like the app's.

    `expire_on_commit=False` matches `app/database`, because that setting changes what a
    post-commit attribute read returns — a fixture that differed here would test a database
    the app does not use.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(migrated_postgres)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()
