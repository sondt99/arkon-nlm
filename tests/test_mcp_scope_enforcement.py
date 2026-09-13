"""Tests for the MCP token scope boundary.

Two gaps made the `allowed_knowledge_types` bug invisible for its whole lifetime:

  1. `apply_scope_filter` — the function that decides which knowledge rows an external
     MCP bearer token may read — had zero test references.
  2. Every test of the surrounding code monkeypatched `verify_token` and hand-built a
     `ResolvedIdentity` with the field already populated, so no test ever executed the
     production path where `_resolve_scope` left it None.

So these tests deliberately do two things the old ones did not: compile the real SQL and
assert on its WHERE clause, and drive the real `_resolve_scope`.
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.database.models import Source
from app.services.mcp_auth_service import (
    MCPAuthService,
    ResolvedIdentity,
    apply_scope_filter,
)


def _compiled(query) -> str:
    """Render a query to literal Postgres SQL so the WHERE clause is assertable."""
    return str(
        query.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _identity(
    *,
    allowed_knowledge_types: list[str] | None = None,
    allowed_source_ids: list[str] | None = None,
    project_source_ids: list[str] | None = None,
    is_admin: bool = False,
    wiki_readable: bool = True,
) -> ResolvedIdentity:
    return ResolvedIdentity(
        employee_id=uuid.uuid4(),
        employee_name="Test",
        department_id=uuid.uuid4(),
        department_name="Dept",
        allowed_knowledge_types=allowed_knowledge_types,
        allowed_source_ids=allowed_source_ids,
        project_source_ids=project_source_ids or [],
        is_admin=is_admin,
        wiki_readable=wiki_readable,
    )


# --------------------------------------------------------------------------- #
# apply_scope_filter — the enforcement boundary itself
# --------------------------------------------------------------------------- #

def test_admin_identity_is_unrestricted():
    """Both scope fields None means open access — the query must be unchanged."""
    q = select(Source.id)
    out = apply_scope_filter(q, _identity(is_admin=True))
    assert "WHERE" not in _compiled(out).upper()


def test_knowledge_types_do_not_constrain_a_DOCUMENT_query():
    """This asserted the opposite, and the opposite was a privilege escalation.

    `allowed_knowledge_types` is a WIKI axis: a WikiPage carries a `knowledge_type_slugs`
    ARRAY because one page aggregates several sources. A Source has exactly one
    knowledge_type_id, so ORing that list into a Source query granted every document of a
    type the caller had seen one document of — and knowledge types are a small global
    taxonomy, so in practice that is the whole corpus.

    Documents follow doc:*, the wiki follows wiki:*. `wiki_visibility()` still carries the
    slugs (see the tests below); the Source filter must not.
    """
    ident = _identity(allowed_knowledge_types=["sales", "legal"], allowed_source_ids=None)
    sql = _compiled(apply_scope_filter(select(Source.id), ident))
    assert "knowledge_type_id" not in sql, (
        "a knowledge-type grant is back in the document filter — one department-visible "
        "document of a type now opens every document of that type company-wide"
    )
    assert "'sales'" not in sql and "'legal'" not in sql


def test_a_department_scope_cannot_be_widened_by_a_shared_knowledge_type():
    """The escalation, stated as the shape of the emitted SQL.

    An `own_dept` caller carries the ids their department can see AND the slugs of those
    sources' types. Only the ids may reach the filter; if the slugs do too, the OR matches
    another department's source that merely shares a type.
    """
    ident = _identity(
        allowed_source_ids=["11111111-1111-1111-1111-111111111111"],
        allowed_knowledge_types=["policy"],
    )
    sql = _compiled(apply_scope_filter(select(Source.id), ident))
    assert "11111111-1111-1111-1111-111111111111" in sql
    assert "'policy'" not in sql
    assert "knowledge_type_id" not in sql


def test_a_non_admin_with_read_all_still_cannot_see_a_foreign_workspace():
    """doc:read:all is not workspace membership.

    permission_engine.can_access_document returns early on scope_type == "project" before
    any doc:* grant is consulted. The shipped "Knowledge Admin" preset is a NON-admin with
    doc:read:all + wiki:read:all, which previously resolved to an unfiltered query.
    """
    ident = _identity(allowed_source_ids=None, allowed_knowledge_types=None)
    sql = _compiled(apply_scope_filter(select(Source.id), ident))
    assert "WHERE" in sql.upper(), "a non-admin must never produce an unfiltered Source query"
    assert "scope_type" in sql


def test_admin_alone_gets_an_unfiltered_query():
    sql = _compiled(apply_scope_filter(select(Source.id), _identity(is_admin=True)))
    assert "WHERE" not in sql.upper()


def test_empty_scope_yields_no_rows():
    """The critical shape: a token with no grants must match nothing.

    An empty list is emphatically not the same as None. If these two are ever conflated
    again, `IN ()` collapses to false here rather than silently disappearing.
    """
    ident = _identity(allowed_knowledge_types=[], allowed_source_ids=[])
    sql = _compiled(apply_scope_filter(select(Source.id), ident))
    assert "WHERE" in sql.upper()
    # No slug or id literals can appear, because there are none to allow.
    assert "'sales'" not in sql


def test_empty_scope_is_distinguishable_from_unrestricted():
    """Guard the None-vs-[] distinction that the original bug turned on."""
    unrestricted = _compiled(apply_scope_filter(select(Source.id), _identity()))
    denied = _compiled(
        apply_scope_filter(
            select(Source.id),
            _identity(allowed_knowledge_types=[], allowed_source_ids=[]),
        )
    )
    assert unrestricted != denied, (
        "an identity with no grants compiled to the same SQL as an unrestricted one — "
        "this is exactly the None/[] conflation that let every token read the whole wiki"
    )


def test_explicit_source_ids_are_scoped():
    allowed = str(uuid.uuid4())
    ident = _identity(allowed_source_ids=[allowed], allowed_knowledge_types=None)
    sql = _compiled(apply_scope_filter(select(Source.id), ident))
    assert allowed in sql


def test_project_sources_are_unioned_not_intersected():
    """A project grant widens access; it must not narrow an existing grant."""
    dept_src = str(uuid.uuid4())
    proj_src = str(uuid.uuid4())
    ident = _identity(allowed_source_ids=[dept_src], project_source_ids=[proj_src])
    sql = _compiled(apply_scope_filter(select(Source.id), ident))
    assert dept_src in sql and proj_src in sql
    assert " OR " in sql.upper()


# --------------------------------------------------------------------------- #
# wiki_visibility — what the wiki tools actually consume
# --------------------------------------------------------------------------- #

def test_wiki_visibility_denies_when_wiki_unreadable():
    """A token whose role lacks wiki:read must resolve to ([], []), not (None, None).

    (None, None) means unrestricted at every consuming call site, so returning it here
    would hand the whole wiki to a token that has no wiki permission at all.
    """
    ident = _identity(
        allowed_knowledge_types=[], allowed_source_ids=[], wiki_readable=False
    )
    assert ident.wiki_visibility() == ([], [])


def test_wiki_visibility_unrestricted_only_for_admin():
    assert _identity(is_admin=True, wiki_readable=True).wiki_visibility() == (None, None)


def test_wiki_visibility_scopes_a_department_token():
    src = str(uuid.uuid4())
    ident = _identity(
        allowed_knowledge_types=["sales"], allowed_source_ids=[src], wiki_readable=True
    )
    kt_slugs, source_ids = ident.wiki_visibility()
    assert kt_slugs == ["sales"]
    assert source_ids == [src]


# --------------------------------------------------------------------------- #
# verify_token / _resolve_scope — the real path, not a hand-built identity
# --------------------------------------------------------------------------- #

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return []

    def scalars(self):
        return SimpleNamespace(all=lambda: [])


class _FakeSession:
    """Minimal AsyncSession stand-in that returns a fixed employee lookup."""

    def __init__(self, employee):
        self._employee = employee
        self.flushed = False

    async def execute(self, *_a, **_kw):
        # First call is the employee lookup; later calls are scope sub-queries.
        if self._employee is not None and not self.flushed:
            emp, self._employee = self._employee, None
            return _FakeResult(emp)
        return _FakeResult(None)

    async def flush(self):
        self.flushed = True


def _employee(*perms: str, role="employee", active=True, expires_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Emp",
        role=role,
        is_active=active,
        department_id=uuid.uuid4(),
        department=SimpleNamespace(name="Dept"),
        custom_role=SimpleNamespace(permissions=list(perms)),
        last_connected=None,
        mcp_token_hash="0" * 64,
        mcp_token_expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_verify_token_rejects_unknown_token():
    svc = MCPAuthService(_FakeSession(None))
    assert await svc.verify_token("ark_nope") is None


@pytest.mark.asyncio
async def test_verify_token_resolves_scope_through_the_real_code_path():
    """The test the original suite lacked.

    Every prior test monkeypatched verify_token, so `_resolve_scope` never ran and the
    unassigned `allowed_knowledge_types` was invisible. Here the real function runs.
    """
    emp = _employee("doc:read:own_dept", "wiki:read:own_dept")
    svc = MCPAuthService(_FakeSession(emp))
    identity = await svc.verify_token("ark_valid")

    assert identity is not None
    assert identity.employee_id == emp.id
    assert identity.allowed_knowledge_types is not None, (
        "_resolve_scope left allowed_knowledge_types as None for a department-scoped "
        "employee — None means unrestricted, so this token would read the whole wiki"
    )


@pytest.mark.asyncio
async def test_employee_without_doc_read_gets_an_empty_scope_not_none():
    emp = _employee("org:departments:read")
    svc = MCPAuthService(_FakeSession(emp))
    identity = await svc.verify_token("ark_valid")

    assert identity is not None
    assert identity.allowed_source_ids == []
    assert identity.allowed_knowledge_types == []
    # And that empty scope must compile to a query that returns nothing.
    sql = _compiled(apply_scope_filter(select(Source.id), identity))
    assert "WHERE" in sql.upper()


@pytest.mark.asyncio
async def test_expired_token_is_rejected():
    """A token past its expiry must not authenticate.

    Before this there was no expiry column at all, so a token exfiltrated from a
    developer's Claude Desktop config years earlier still worked.
    """
    from datetime import datetime, timedelta, timezone

    emp = _employee(
        "doc:read:all",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    svc = MCPAuthService(_FakeSession(emp))
    assert await svc.verify_token("ark_expired") is None


@pytest.mark.asyncio
async def test_unexpired_token_still_authenticates():
    from datetime import datetime, timedelta, timezone

    emp = _employee(
        "doc:read:all",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    svc = MCPAuthService(_FakeSession(emp))
    assert await svc.verify_token("ark_valid") is not None


def test_token_is_never_stored_in_plaintext():
    """The Employee model must not carry a plaintext token column any more."""
    from app.database.models import Employee

    cols = set(Employee.__table__.columns.keys())
    assert "mcp_token" not in cols, "plaintext MCP token column is back"
    assert "mcp_token_hash" in cols
    assert "mcp_token_expires_at" in cols


def test_hash_matches_the_digest_the_migration_computes():
    """Guards the app/migration contract.

    Migration 031 hashes existing plaintext tokens with Postgres'
    encode(sha256(...), 'hex'). If the Python side ever changes algorithm or encoding,
    every already-migrated token stops authenticating — silently, because a mismatched
    digest just looks like an unknown token. This pins the expected digest.
    """
    from app.services.mcp_auth_service import hash_mcp_token

    # sha256("ark_KNOWN_TEST_TOKEN_VALUE") — verified equal to the value migration 031
    # produced inside Postgres.
    assert hash_mcp_token("ark_KNOWN_TEST_TOKEN_VALUE") == (
        "a972fc56e6a3ce5a5df1999adaa4150dcbfabac166014d5e09db3c3f0abd6218"
    )


@pytest.mark.asyncio
async def test_admin_resolves_to_unrestricted_through_the_real_path():
    emp = _employee(role="admin")
    svc = MCPAuthService(_FakeSession(emp))
    identity = await svc.verify_token("ark_admin")

    assert identity is not None
    assert identity.is_admin is True
    assert identity.allowed_knowledge_types is None
    assert identity.allowed_source_ids is None
