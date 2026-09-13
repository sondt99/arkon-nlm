"""The REST wiki routers read pages with no document-visibility filter.

`_build_wiki_scope_filter` constrained pages by SCOPE only — global, plus project pages in
workspaces the caller belongs to. It never consulted `source_ids` or `knowledge_type_slugs`,
so `wiki:read:own_dept` (held by every employee via EMPLOYEE_DEFAULT_PERMISSIONS) returned
every global page regardless of which department's sources compiled it, and
`/wiki/pages/{slug}` served the full `content_md`.

The filter existed and was already applied by MCP, the Export API and chat. Only these
routers skipped it, which made the REST surface strictly more permissive than the MCP
surface over identical rows.

These tests pin both halves — that the RBAC clause is combined in, and that every router
call site actually goes through the function that does it.
"""

import ast
import inspect
import pathlib
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import app.routers.wiki as wiki_router
from app.database.models import WikiPage

_SRC = pathlib.Path(inspect.getfile(wiki_router)).read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)

#: `_and_rbac` reaches the database only through `wiki_visibility_for`, which every test
#: here monkeypatches — so the session and user are never touched and need not be real.
_UNUSED: Any = None


def _compiled(stmt) -> str:
    return " ".join(
        str(stmt.compile(dialect=postgresql.dialect(),
                         compile_kwargs={"literal_binds": True})).split()
    )


# ---------------------------------------------------------------------------
# The RBAC clause is actually combined into the scope clause
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rbac_clause_is_anded_onto_the_scope_clause(monkeypatch):
    """A restricted identity must narrow the query, not leave it at scope-only."""
    async def fake_visibility(_db: Any = None, _user: Any = None):
        return ["policy"], ["11111111-1111-1111-1111-111111111111"]

    monkeypatch.setattr(
        wiki_router.wiki_service, "wiki_visibility_for", fake_visibility
    )

    scope_only = WikiPage.scope_type == "global"
    combined = await wiki_router._and_rbac(_UNUSED, _UNUSED, scope_only)

    sql = _compiled(select(WikiPage.id).where(combined))
    assert "scope_type" in sql, "the scope half was dropped"
    assert "source_ids" in sql or "knowledge_type_slugs" in sql, (
        "the RBAC half is missing — pages compiled from another department's sources "
        "are readable through the REST wiki API"
    )


@pytest.mark.asyncio
async def test_an_unrestricted_identity_leaves_the_scope_clause_alone(monkeypatch):
    """(None, None) means no document restriction; it must not add a spurious filter."""
    async def fake_visibility(_db: Any = None, _user: Any = None):
        return None, None

    monkeypatch.setattr(
        wiki_router.wiki_service, "wiki_visibility_for", fake_visibility
    )

    scope_only = WikiPage.scope_type == "global"
    combined = await wiki_router._and_rbac(_UNUSED, _UNUSED, scope_only)
    assert combined is scope_only


@pytest.mark.asyncio
async def test_a_denied_identity_cannot_widen_back_to_scope_only(monkeypatch):
    """Empty lists are fail-closed, and must survive being combined."""
    async def fake_visibility(_db: Any = None, _user: Any = None):
        return [], []

    monkeypatch.setattr(
        wiki_router.wiki_service, "wiki_visibility_for", fake_visibility
    )

    combined = await wiki_router._and_rbac(_UNUSED, _UNUSED, WikiPage.scope_type == "global")
    assert combined is not None
    sql = _compiled(select(WikiPage.id).where(combined))
    assert "false" in sql.lower(), (
        "a token with no document grants must match no pages; it resolved to a "
        "scope-only filter instead"
    )


# ---------------------------------------------------------------------------
# Every router call site goes through the function that applies RBAC
# ---------------------------------------------------------------------------

def _functions_calling(target: str) -> set[str]:
    found = set()
    for fn in ast.walk(_TREE):
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == target:
                found.add(fn.name)
    return found


#: Every endpoint that reads WikiPage rows. Each must obtain its filter from
#: `_build_wiki_scope_filter`, which is where the RBAC clause is applied.
_PAGE_READING_ENDPOINTS = {
    "list_wiki_pages",
    "get_wiki_stats",
    "list_wiki_tree",
    "search_wiki_pages",
    "get_wiki_graph",
    "_restrict_graph_to_visible",
}


def test_every_page_reading_endpoint_builds_a_scope_filter():
    callers = _functions_calling("_build_wiki_scope_filter")
    missing = sorted(_PAGE_READING_ENDPOINTS - callers)
    assert not missing, (
        f"these endpoints read wiki pages without building a scope filter: {missing}"
    )


def test_the_scope_filter_applies_rbac():
    """Guards against the filter being reverted to scope-only.

    `_build_wiki_scope_filter` is only safe because it routes its `own_dept` branch
    through `_and_rbac`. If that call disappears the function still returns a perfectly
    valid clause — just one that has stopped enforcing document visibility.
    """
    assert "_build_wiki_scope_filter" in _functions_calling("_and_rbac") or \
        "_and_rbac" in {
            n.func.id
            for fn in ast.walk(_TREE)
            if isinstance(fn, ast.AsyncFunctionDef) and fn.name == "_build_wiki_scope_filter"
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }, (
            "_build_wiki_scope_filter no longer calls _and_rbac — the REST wiki API is "
            "back to filtering on scope alone"
        )


def test_the_shared_visibility_helper_has_one_home():
    """chat.py used to own the only implementation, privately, which caused this bug."""
    from app.services import wiki_service

    assert hasattr(wiki_service, "wiki_visibility_for")
    assert inspect.iscoroutinefunction(wiki_service.wiki_visibility_for)
