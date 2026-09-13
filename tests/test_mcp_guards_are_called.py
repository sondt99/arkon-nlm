"""The two #88 guards existed and were never called.

I closed #88 claiming MCP listing tools were capped and MCP edits reindexed. Both helpers
were written correctly and neither was invoked — `grep -rn` returned exactly one hit each,
the definition. My verification checked that the settings existed and the functions existed;
it never checked that anything called them.

So these tests assert the CALL, by AST, not the definition. A string search would be
satisfied by a commented-out call, which is the same failure mode again one level up.
"""

import ast
import inspect
import pathlib

import app.mcp.tools as tools_mod

_SRC = pathlib.Path(inspect.getfile(tools_mod)).read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)


def _called_names() -> set[str]:
    """Every function name that is actually invoked somewhere in the module."""
    return {
        node.func.id
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _enclosing_tool(target: str) -> set[str]:
    """Names of the `async def`s that contain a call to `target`."""
    found = set()
    for fn in ast.walk(_TREE):
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == target:
                found.add(fn.name)
    return found


def _enclosing_tool_method(target: str) -> set[str]:
    """Same, for a method call like `identity.wiki_visibility()`.

    The scoping guards are split across both call shapes — `apply_scope_filter(...)` is a
    bare name, `identity.wiki_visibility()` is an attribute — so pinning only the first
    leaves the wiki half unguarded.
    """
    found = set()
    for fn in ast.walk(_TREE):
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == target:
                found.add(fn.name)
    return found


def test_the_pagination_guard_is_actually_called():
    assert "_page_window_error" in _called_names(), (
        "_page_window_error is defined and never invoked, so LLM-supplied limit/offset "
        "still reach the database untouched — which is how #88 was closed on a false claim"
    )


def test_every_paginated_tool_calls_the_guard():
    """A cap on three of four tools is not a cap."""
    expected = {
        "list_wiki_pages",
        "list_sources",
        "list_pending_drafts",
        "get_knowledge_type_docs",
    }
    guarded = _enclosing_tool("_page_window_error")
    missing = sorted(expected - guarded)
    assert not missing, f"these tools accept limit/offset with no bound: {missing}"


def test_the_reindex_helper_is_actually_called():
    assert "_reindex_edited_page" in _called_names(), (
        "_reindex_edited_page is defined and never invoked, so search_wiki keeps ranking "
        "an edited page by its pre-edit body"
    )


def test_both_mcp_write_tools_reindex():
    """`edit_wiki_page` and `approve_draft` both rewrite content_md."""
    guarded = _enclosing_tool("_reindex_edited_page")
    missing = sorted({"edit_wiki_page", "approve_draft"} - guarded)
    assert not missing, f"these rewrite content_md without reindexing: {missing}"


def test_the_settings_are_reachable_from_a_call_path():
    """The caps were referenced only INSIDE the dead function, which made them decorative."""
    from app.config import settings

    assert settings.mcp_max_page_size > 0
    assert settings.mcp_max_offset > 0
    assert tools_mod._page_window_error(settings.mcp_max_page_size + 1) is not None
    assert tools_mod._page_window_error(0) is not None
    assert tools_mod._page_window_error(-1) is not None
    assert tools_mod._page_window_error(10, settings.mcp_max_offset + 1) is not None
    assert tools_mod._page_window_error(10, 0) is None


# ---------------------------------------------------------------------------
# Authorization guards — the same #88 mistake, one level more serious.
#
# The #88 tests above pin the PAGINATION and REINDEX helpers. The authorization helpers
# had no equivalent: `tests/test_mcp_scope_enforcement.py` proves `apply_scope_filter`
# compiles correct SQL and `ResolvedIdentity.wiki_visibility()` returns correct tuples, as
# pure functions — but nothing asserted a tool invokes either one.
#
# Measured: deleting `apply_scope_filter(...)` from `list_sources`, deleting it from
# `get_knowledge_type_docs`, and replacing `identity.wiki_visibility()` with an
# unrestricted `None, None` each passed the full 937-test suite. Every one of those is an
# MCP token reading the entire knowledge base.
#
# These are structural assertions, so a tool added later that forgets to scope fails here
# rather than shipping. If you add a tool that legitimately needs no scoping, add it to the
# exemption set below with the reason — don't delete the assertion.
# ---------------------------------------------------------------------------

#: Tools that read Source rows and must therefore constrain the query to the caller's scope,
#: either directly or through `_can_read_source`.
_DOCUMENT_READING_TOOLS = {
    "list_sources",
    "get_knowledge_type_docs",
    "get_source",
    "get_source_outline",
    "get_source_pages",
}

#: Tools that read wiki pages. Each needs BOTH: the `wiki:read` permission gate, and the
#: per-identity visibility filter. Passing the gate says the caller may read *some* wiki;
#: it says nothing about *which* pages.
_WIKI_READING_TOOLS = {
    "search_wiki",
    "read_wiki_index",
    "read_wiki_page",
    "list_wiki_pages",
}


def test_every_document_reading_tool_constrains_its_query():
    scoped = _enclosing_tool("apply_scope_filter") | _enclosing_tool("_can_read_source")
    missing = sorted(_DOCUMENT_READING_TOOLS - scoped)
    assert not missing, (
        f"these tools read Source rows with no scope filter: {missing} — an MCP token "
        "would read every document in the knowledge base regardless of department, "
        "knowledge type or workspace membership"
    )


def test_the_source_scope_filter_is_actually_called():
    assert "apply_scope_filter" in _called_names(), (
        "apply_scope_filter is imported and never invoked, so per-token document scoping "
        "does not exist — the helper's own unit tests would still pass"
    )


def test_every_wiki_reading_tool_checks_the_read_permission():
    gated = _enclosing_tool("_require_wiki_read")
    missing = sorted(_WIKI_READING_TOOLS - gated)
    assert not missing, f"these wiki tools skip the wiki:read permission entirely: {missing}"


def test_every_wiki_reading_tool_applies_the_visibility_filter():
    """The gate and the filter are separate guards; passing one is not passing the other."""
    filtered = _enclosing_tool_method("wiki_visibility")
    missing = sorted(_WIKI_READING_TOOLS - filtered)
    assert not missing, (
        f"these wiki tools never call identity.wiki_visibility(): {missing} — they hold "
        "the wiki:read gate open and then read every page behind it"
    )


def test_the_two_scoping_guards_are_not_the_same_guard():
    """Cheap structural check that the sets above did not collapse into one another.

    If a refactor folds `wiki_visibility` into `_require_wiki_read`, the two assertions
    above stop being independent and one of them silently stops testing anything.
    """
    assert _enclosing_tool("_require_wiki_read") and _enclosing_tool_method("wiki_visibility")
    assert _DOCUMENT_READING_TOOLS.isdisjoint(_WIKI_READING_TOOLS)
