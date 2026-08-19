"""Tests that the fake database cannot express (#125).

Every test here corresponds to a bug that shipped while the whole suite was green, because
`FakeDB` cannot model the thing that broke. If a test in this file could pass against the
fake, it belongs in the fast suite instead.

Run with a database reachable:

    ARKON_TEST_DATABASE_URL=postgresql+asyncpg://postgres:pw@localhost:5432/arkon_test \\
      uv run --extra dev python -m pytest tests/test_real_postgres.py -q

Without one, every test here skips and the fast suite is unaffected.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, select, text

from app.database.models import Employee, Source, WikiPage

# Both marks apply to every test here: they are all async, and they all need the
# database. `asyncio_mode = "strict"` means the asyncio mark is not implied.
pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


# --------------------------------------------------------------------------- #
# #85 — CHECK constraints. The fake cannot reject anything.
# --------------------------------------------------------------------------- #

async def test_a_typo_in_a_status_is_rejected_by_the_database(pg_sessionmaker):
    """The bug this closed: an out-of-vocabulary status produced a row that every listing
    filter skipped, so the source vanished from the UI with nothing logged.

    `FakeDB` has no constraints, so it accepts "erorr" happily — this can only be tested
    here.
    """
    from sqlalchemy.exc import IntegrityError

    async with pg_sessionmaker() as s:
        s.add(Source(id=uuid.uuid4(), title="typo", source_type="file",
                     status="erorr", progress=0, scope_type="global"))
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()


async def test_a_legitimate_status_is_still_accepted(pg_sessionmaker):
    """A constraint that rejects valid values would be worse than none."""
    sid = uuid.uuid4()
    async with pg_sessionmaker() as s:
        s.add(Source(id=sid, title="ok", source_type="file",
                     status="plan_ready", progress=0, scope_type="global"))
        await s.commit()
    async with pg_sessionmaker() as s:
        row = await s.get(Source, sid)
        assert row.status == "plan_ready"
        await s.delete(row)
        await s.commit()


# --------------------------------------------------------------------------- #
# #123 — lost update. Needs real concurrency AND real transaction isolation.
# --------------------------------------------------------------------------- #

async def test_concurrent_log_appends_do_not_lose_an_entry(pg_sessionmaker):
    """Two ingests appending to `_log` must both survive.

    The interleaving is FORCED. Two plain concurrent tasks did not reproduce the loss even
    with the lock removed — they were not overlapping in the harmful window, and a test that
    cannot fail is worse than no test. Writer A stalls between its read and its write, so B
    reads the same pre-state and commits in between.

    A barrier would not work: with the advisory lock held, B blocks *before* its read and
    would never reach the barrier. The stall has to be on the writer holding the lock.
    """
    from app.services import wiki_service

    real_get = wiki_service.get_page_by_slug
    stall = {"armed": False}

    async def slow_get(session, slug, **kw):
        page = await real_get(session, slug, **kw)
        if stall["armed"] and slug == "_log":
            stall["armed"] = False
            await asyncio.sleep(1.0)
        return page

    async def writer(tag: str, armed: bool):
        async with pg_sessionmaker() as s:
            stall["armed"] = armed
            await wiki_service.append_log(s, tag)
            await s.commit()

    wiki_service.get_page_by_slug = slow_get
    try:
        async with pg_sessionmaker() as s:
            await s.execute(delete(WikiPage).where(WikiPage.slug == "_log"))
            await s.commit()
        async with pg_sessionmaker() as s:
            await wiki_service.append_log(s, "SEED-ENTRY")
            await s.commit()

        a = asyncio.create_task(writer("DOC-A", armed=True))
        await asyncio.sleep(0.2)
        b = asyncio.create_task(writer("DOC-B", armed=False))
        await asyncio.gather(a, b)

        async with pg_sessionmaker() as s:
            body = (await real_get(s, "_log")).content_md
    finally:
        wiki_service.get_page_by_slug = real_get

    missing = [t for t in ("SEED-ENTRY", "DOC-A", "DOC-B") if t not in body]
    assert not missing, f"the advisory lock is not holding — lost {missing}"


# --------------------------------------------------------------------------- #
# #121 — MissingGreenlet. A lazy relationship resolves eagerly against the fake.
# --------------------------------------------------------------------------- #

async def test_reading_a_lazy_relationship_off_a_bare_get_raises(pg_sessionmaker):
    """Pins the hazard itself, so the class of bug stays visible.

    `session.get(Employee, id)` does not load `custom_role`, and touching it inside a
    coroutine raises MissingGreenlet. Six MCP tools did exactly this and crashed for every
    employee with a custom role assigned. Against `FakeDB` the attribute resolves fine, so
    this assertion is only meaningful here.
    """
    from sqlalchemy.exc import MissingGreenlet

    eid = uuid.uuid4()
    async with pg_sessionmaker() as s:
        dept = (await s.execute(text(
            "INSERT INTO departments (id, name) VALUES (gen_random_uuid(), 'PgTier') "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        ))).scalar_one()
        role = (await s.execute(text(
            "INSERT INTO roles (id, name, permissions, is_system) "
            "VALUES (gen_random_uuid(), 'pg-tier-role', '[\"wiki:read:all\"]'::jsonb, false) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        ))).scalar_one()
        s.add(Employee(id=eid, name="Lazy", email=f"lazy-{eid}@x", password_hash="h",
                       role="employee", department_id=dept, custom_role_id=role))
        await s.commit()

    try:
        async with pg_sessionmaker() as s:
            emp = await s.get(Employee, eid)
            with pytest.raises(MissingGreenlet):
                _ = emp.custom_role.permissions
    finally:
        async with pg_sessionmaker() as s:
            await s.execute(delete(Employee).where(Employee.id == eid))
            await s.commit()


async def test_eager_loading_makes_the_same_read_safe(pg_sessionmaker):
    """The other half: the fix must actually work, not merely be present."""
    from sqlalchemy.orm import selectinload

    async with pg_sessionmaker() as s:
        emp = (await s.execute(
            select(Employee)
            .options(selectinload(Employee.custom_role))
            .where(Employee.custom_role_id.isnot(None))
            .limit(1)
        )).scalar_one_or_none()
        if emp is None:
            pytest.skip("no employee with a custom role in this database")
        assert emp.custom_role is not None
        assert isinstance(emp.custom_role.permissions, list)


# --------------------------------------------------------------------------- #
# #88 — real LIMIT semantics. The fake never executes SQL.
# --------------------------------------------------------------------------- #

async def test_a_negative_limit_is_a_database_error(pg_sessionmaker):
    """Why the MCP page-window guard has to exist.

    An LLM-supplied `limit=-1` reached the database and raised
    InvalidRowCountInLimitClauseError, which aborts the transaction — not a clean 4xx. The
    guard was written, never called, and a mutation to it survived all 891 tests because
    nothing executed it.
    """
    async with pg_sessionmaker() as s:
        with pytest.raises(Exception) as exc:
            await s.execute(select(WikiPage).limit(-1))
        assert "LIMIT" in str(exc.value).upper() or "limit" in str(exc.value)
        await s.rollback()


async def test_the_mcp_guard_rejects_what_the_database_would_choke_on(pg_sessionmaker):
    """The guard and the database must agree about what is out of range."""
    from app.config import settings
    from app.mcp.tools import _page_window_error

    assert _page_window_error(-1) is not None
    assert _page_window_error(0) is not None
    assert _page_window_error(settings.mcp_max_page_size + 1) is not None
    assert _page_window_error(10, settings.mcp_max_offset + 1) is not None
    assert _page_window_error(10, 0) is None


# --------------------------------------------------------------------------- #
# notin_ — the fake's documented blind spot
# --------------------------------------------------------------------------- #

async def test_notin_actually_filters(pg_sessionmaker):
    """`FakeDB`'s own docstring says `notin_` "filters nothing".

    Reserved-page exclusion (`_index`, `_log`) is built on `notin_`, so against the fake a
    test asserting those are hidden passes whether or not the filter is there.
    """
    async with pg_sessionmaker() as s:
        await s.execute(delete(WikiPage).where(WikiPage.slug.in_(["_log", "_index"])))
        for slug in ("_log", "_index", "pg-tier-real-page"):
            s.add(WikiPage(id=uuid.uuid4(), slug=slug, title=slug, page_type="topic",
                           content_md="body", summary="", knowledge_type_slugs=[],
                           source_ids=[], scope_type="global"))
        await s.commit()

        rows = (await s.execute(
            select(WikiPage.slug).where(WikiPage.slug.notin_(["_index", "_log"]))
        )).scalars().all()

        assert "pg-tier-real-page" in rows
        assert "_log" not in rows and "_index" not in rows, (
            "notin_ did not filter — this is the assertion the fake cannot make"
        )

        await s.execute(delete(WikiPage).where(
            WikiPage.slug.in_(["_log", "_index", "pg-tier-real-page"])
        ))
        await s.commit()
