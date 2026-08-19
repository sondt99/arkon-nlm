"""Assert every non-public route is behind an authentication dependency.

Per-route tests will never cover 167 endpoints. This walks the app's own dependency
graph instead, so a new route is covered the moment it is registered and a deleted guard
fails here rather than in production.

This is the test that would have caught the four sources.py plan/knowledge-impact
endpoints reaching production with `require_permission` but no row-level check — and,
more importantly, it catches the whole class rather than those four instances.

Scope note: this proves a route *authenticates*. It cannot prove the route applies the
right row-level scoping — `require_permission`'s own docstring says it performs none. The
pairing tests in test_permission_engine_pairing.py cover that half.
"""

from fastapi.routing import APIRoute

from app.main import app

# FastAPI 0.141 includes routers lazily: app.include_router() appends an _IncludedRouter
# placeholder, and the real APIRoute objects — along with their dependency trees — are not
# built until something forces resolution. That is exactly how a malformed dependency
# (Depends(Depends(...)) in the NotebookLM ingest endpoints) reached main while 172 tests
# and a fully green CI passed: nothing ever materialized the routes.
#
# Building the OpenAPI schema forces it, so this call is load-bearing rather than
# incidental. It raises on a malformed dependency, which makes it a smoke test in its own
# right.
app.openapi()

# Callables that constitute "this route is authenticated".
AUTH_DEPENDENCY_NAMES = {
    "get_current_user",
    "get_current_user_image",
    "require_admin",
    # require_permission(...) returns a nested _check that itself depends on
    # get_current_user, so it is caught transitively. Named here for clarity.
    "_check",
    # Token-based identities for the machine-facing surfaces.
    "get_identity_from_export_token",
    "get_identity_from_gateway_token",
}

# Routes that are intentionally reachable without a credential. Anything not on this list
# must authenticate. Keep this list short and justify each entry.
PUBLIC_PATHS = {
    "/health",              # container healthcheck; must work before auth exists
    "/api/health",
    "/api/auth/login",      # issues the credential
    "/api/auth/status",     # returns only {"auth_required": true}
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/openapi.json",
    "/",
}


def _dependency_callables(dependant, seen=None) -> set[str]:
    """Flatten a FastAPI dependant tree into the set of callable names it invokes."""
    if seen is None:
        seen = set()
    if dependant.call is not None:
        seen.add(getattr(dependant.call, "__name__", repr(dependant.call)))
    for sub in dependant.dependencies:
        _dependency_callables(sub, seen)
    return seen


def _all_api_routes():
    """Yield (full_path, route) for every APIRoute in the app.

    Because inclusion is lazy in FastAPI 0.141, app.routes holds _IncludedRouter
    placeholders rather than the routes themselves. The real APIRoute objects — with their
    resolved dependency trees — hang off each placeholder's original_router, and the
    mounted prefix lives on its include_context. Walking only app.routes finds 3 routes;
    walking this way finds all 167.
    """
    for entry in app.routes:
        if isinstance(entry, APIRoute):
            yield entry.path, entry
            continue
        original = getattr(entry, "original_router", None)
        if original is None:
            continue
        prefix = getattr(getattr(entry, "include_context", None), "prefix", "") or ""
        for route in original.routes:
            if isinstance(route, APIRoute):
                yield f"{prefix}{route.path}", route


def _guarded_routes():
    for path, route in _all_api_routes():
        if path in PUBLIC_PATHS:
            continue
        yield path, route


def test_every_route_is_authenticated():
    unguarded = []
    for path, route in _guarded_routes():
        names = _dependency_callables(route.dependant)
        if not (names & AUTH_DEPENDENCY_NAMES):
            methods = ",".join(sorted(route.methods or []))
            unguarded.append(f"{methods} {path}  (deps: {sorted(names)})")

    assert not unguarded, (
        f"{len(unguarded)} route(s) have no authentication dependency:\n  "
        + "\n  ".join(unguarded)
    )


def test_the_meta_test_actually_covers_the_api():
    """Guard against the check silently passing because it found nothing to check.

    A filter bug that yielded zero routes would make the assertion above vacuous.
    """
    count = sum(1 for _ in _guarded_routes())
    assert count > 100, f"expected the full API surface, only walked {count} routes"


def test_public_allowlist_entries_still_exist():
    """Stop PUBLIC_PATHS from accumulating stale entries that hide real routes."""
    registered = {path for path, _ in _all_api_routes()}
    # /docs, /redoc, /openapi.json, / are added by FastAPI/Starlette itself.
    fastapi_builtin = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json", "/"}
    stale = {p for p in PUBLIC_PATHS - fastapi_builtin if p not in registered}
    assert not stale, f"PUBLIC_PATHS lists routes that no longer exist: {sorted(stale)}"
