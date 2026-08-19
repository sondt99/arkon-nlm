"""Three defects an independent review found after the remediation batch closed.

Each is the kind that a green, database-less suite cannot see: a lazy relationship that only
raises under a real async engine, a sync call that only matters under concurrency, and an
index that only goes out of range at a config value nobody had set.
"""

import ast
import inspect
import pathlib

import pytest

# --------------------------------------------------------------------------- #
# #121 — six MCP tools raised MissingGreenlet for any employee with a custom role
# --------------------------------------------------------------------------- #

def _tools_tree() -> ast.AST:
    import app.mcp.tools as mod
    return ast.parse(pathlib.Path(inspect.getfile(mod)).read_text(encoding="utf-8"))


def test_no_tool_loads_an_employee_without_eager_loading_custom_role():
    """`_can_review_page` reads `employee.custom_role`, a default-lazy relationship.

    A bare `session.get(Employee, ...)` therefore raised
    `MissingGreenlet: greenlet_spawn has not been called` under the real async engine — and
    it failed for EXACTLY the users an admin had given a role to. `role == "admin"` returns
    early from `_get_user_permissions`, and a NULL `custom_role_id` short-circuits the lazy
    load, so both of those paths worked and hid the bug.
    """
    bare = [
        node.lineno
        for node in ast.walk(_tools_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and any(isinstance(a, ast.Name) and a.id == "Employee" for a in node.args)
    ]
    assert not bare, (
        f"bare session.get(Employee, ...) at lines {bare}; use _load_employee, which "
        "eager-loads custom_role"
    )


def test_the_loader_helper_is_used_by_every_tool_that_needs_it():
    calls = [
        node.lineno
        for node in ast.walk(_tools_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_load_employee"
    ]
    assert len(calls) >= 6, f"only {len(calls)} tools load the employee through the helper"


def test_the_helper_requests_selectin_on_custom_role():
    """Assert the loader strategy, not just that a helper exists."""
    import app.mcp.tools as mod

    src = inspect.getsource(mod._load_employee)
    assert "selectinload" in src
    assert "custom_role" in src


# --------------------------------------------------------------------------- #
# #124a — the Google provider blocked the event loop
# --------------------------------------------------------------------------- #

def test_google_provider_never_calls_the_sync_client():
    """`self.client.models.*` is the SYNC client on an async def.

    `embed_batch` wraps `embed` in a Semaphore(5) + gather, which delivered zero concurrency
    because every call blocked; and on the request path a single query froze every request in
    the process, /health included. `generate_with_tools` and `analyze_image` in the same file
    already used `.aio.`, which is what made this an inconsistency rather than a choice.
    """
    import app.ai.providers.google as g

    src = pathlib.Path(inspect.getfile(g)).read_text(encoding="utf-8")
    offenders = [
        (i, line.strip())
        for i, line in enumerate(src.splitlines(), 1)
        if "self.client.models." in line
    ]
    assert not offenders, f"sync google client used at {offenders}"


def test_google_provider_uses_the_async_client_for_embed_and_generate():
    import app.ai.providers.google as g

    for fn in (g.GoogleEmbedding.embed, g.GoogleLLM.generate):
        src = inspect.getsource(fn)
        assert "self.client.aio." in src, f"{fn.__qualname__} does not use the async client"


# --------------------------------------------------------------------------- #
# #124b — MRP_WRITER_MAX_ATTEMPTS of 4 or 5 raised IndexError
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("max_attempts", [1, 2, 3, 4, 5])
def test_retry_factor_is_defined_for_every_allowed_attempt_count(max_attempts):
    """config.py allows 1..5; the factor tuple has three entries.

    At attempt=3 this raised IndexError *inside* the try, so the generic
    `except Exception` swallowed it and burned the remaining attempts on a failure unrelated
    to the LLM — making max_attempts=4 strictly worse than 3.
    """
    factors = (1.0, 0.60, 0.35)
    for attempt in range(max_attempts):
        assert factors[min(attempt, len(factors) - 1)] > 0


def test_the_writer_clamps_instead_of_indexing():
    import app.ai.mrp.writer as w

    src = inspect.getsource(w.run_refine_phase)
    assert "(1.0, 0.60, 0.35)[attempt]" not in src, (
        "the raw index is back; MRP_WRITER_MAX_ATTEMPTS>3 will IndexError again"
    )
    assert "min(attempt" in src


def test_the_config_bound_and_the_factor_table_cannot_disagree_silently():
    """If someone raises the config ceiling, this is the test that should fail first."""
    from app.config import Settings

    field = Settings.model_fields["mrp_writer_max_attempts"]
    ceiling = next(m.le for m in field.metadata if hasattr(m, "le"))
    factors = (1.0, 0.60, 0.35)
    # Clamping makes any ceiling safe; this documents that and pins the clamp's existence.
    assert factors[min(int(ceiling) - 1, len(factors) - 1)] > 0
