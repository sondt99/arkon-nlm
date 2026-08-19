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
