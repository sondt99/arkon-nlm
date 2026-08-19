"""Ownership boundary for the NotebookLM passthrough endpoints (#19).

Arkon talks to NotebookLM through ONE shared Google account, so every notebook in that
account is reachable by every request the backend makes. `Depends(get_current_user)` was
the only barrier: any authenticated employee could list every notebook in the company,
read its sources, chat with it — reaching the documents through NotebookLM's own RAG — and
delete it. Nothing in the suite exercised a passthrough endpoint with a foreign id.

These tests drive the real route handlers against a fake session that answers the two
ownership SELECTs, in the style of test_chat_router.py, so they fail if the guard call is
removed rather than merely if its source text changes. The meta test at the bottom covers
the other half: a *new* passthrough endpoint added without a guard.
"""

import ast
import inspect
import textwrap
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.database.models import NotebookLMPassthroughOwner
from app.routers import notebooklm as nlm_router
from app.routers.notebooklm import NLMChatAsk, NLMNotebookCreate

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
OWNED_ID = "nb-owned-by-a"
FOREIGN_ID = "nb-owned-by-b"
UNOWNED_ID = "nb-that-predates-the-boundary"


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Answers the ownership SELECTs the guard issues and records the writes.

    Both probes select a primary key while the list query selects the notebook id; the
    guard only tests the former for None, so returning the notebook id for every shape is
    sufficient and keeps the fake to one branch.
    """

    def __init__(self, passthrough=(), db_backed=()):
        self.passthrough = set(passthrough)   # (nlm_id, owner_employee_id)
        self.db_backed = set(db_backed)       # (notebook_id, created_by_employee_id)
        self.added: list[object] = []
        self.deletes: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        params = compiled.params

        if sql.lstrip().upper().startswith("DELETE"):
            self.deletes.append((sql, params))
            return _FakeResult([])

        owner = params.get("owner_employee_id_1") or params.get("created_by_employee_id_1")
        wanted = params.get("nlm_id_1") or params.get("notebook_id_1")

        if "notebooklm_passthrough_owners" in sql:
            rows = self.passthrough
        elif "notebooklm_notebooks" in sql:
            rows = self.db_backed
        else:  # pragma: no cover - a new query shape must be taught to the fake
            raise AssertionError(f"unexpected query against the fake session: {sql}")

        return _FakeResult(
            nlm_id
            for nlm_id, row_owner in rows
            if row_owner == owner and (wanted is None or nlm_id == wanted)
        )

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _user(employee_id=OWNER, role="employee") -> SimpleNamespace:
    return SimpleNamespace(id=employee_id, role=role)


def _session_with_owned_notebook() -> _FakeSession:
    return _FakeSession(passthrough=[(OWNED_ID, OWNER), (FOREIGN_ID, STRANGER)])


def _patch_service(monkeypatch, name, impl):
    """Patch a passthrough service function.

    The router imports these inside each handler, so patching the service module is what
    the handler actually resolves at call time.
    """
    monkeypatch.setattr(f"app.services.notebooklm_service.{name}", impl)


def _never_called(what):
    async def _impl(*_args, **_kwargs):
        raise AssertionError(f"{what} reached Google for a notebook the caller does not own")
    return _impl


# --------------------------------------------------------------------------- #
# _resolve_nlm_notebook — the boundary itself
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_owner_resolves_their_own_notebook():
    resolved = await nlm_router._resolve_nlm_notebook(
        _session_with_owned_notebook(), _user(), OWNED_ID
    )
    assert resolved == OWNED_ID


@pytest.mark.asyncio
async def test_foreign_notebook_is_404_not_403():
    """403 confirms the id names a real notebook, which is enough to enumerate them."""
    with pytest.raises(HTTPException) as exc:
        await nlm_router._resolve_nlm_notebook(
            _session_with_owned_notebook(), _user(), FOREIGN_ID
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_notebook_is_404():
    with pytest.raises(HTTPException) as exc:
        await nlm_router._resolve_nlm_notebook(
            _session_with_owned_notebook(), _user(), "nb-does-not-exist"
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unowned_notebook_is_denied_to_employees_and_allowed_to_admins():
    """Notebooks predating the ownership table must be neither public nor orphaned.

    Readable-by-everyone re-creates the original hole; hidden-from-lists-but-deletable
    leaves them destroyable by anyone who guesses an id. Admin-only is the documented
    middle, and it is the half most likely to be quietly dropped in a later refactor.
    """
    session = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await nlm_router._resolve_nlm_notebook(session, _user(), UNOWNED_ID)
    assert exc.value.status_code == 404

    assert await nlm_router._resolve_nlm_notebook(
        session, _user(role="admin"), UNOWNED_ID
    ) == UNOWNED_ID


@pytest.mark.asyncio
async def test_db_backed_creator_is_honoured_by_the_passthrough_guard():
    """"Send to NotebookLM → new notebook" records ownership in notebooklm_notebooks.

    It then redirects the user to the passthrough page. Consulting only the passthrough
    table would make the notebook they just created invisible and un-chattable.
    """
    session = _FakeSession(db_backed=[("nb-from-send-dialog", OWNER)])
    assert await nlm_router._resolve_nlm_notebook(
        session, _user(), "nb-from-send-dialog"
    ) == "nb-from-send-dialog"


# --------------------------------------------------------------------------- #
# List scoping
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_list_returns_only_the_callers_notebooks(monkeypatch):
    async def _list():
        return [{"id": OWNED_ID}, {"id": FOREIGN_ID}, {"id": UNOWNED_ID}]

    _patch_service(monkeypatch, "list_nlm_notebooks", _list)

    got = await nlm_router.nlm_list_notebooks(
        db=_session_with_owned_notebook(), current_user=_user()
    )
    assert [nb["id"] for nb in got] == [OWNED_ID]


@pytest.mark.asyncio
async def test_list_shows_admins_the_whole_shared_account(monkeypatch):
    async def _list():
        return [{"id": OWNED_ID}, {"id": FOREIGN_ID}, {"id": UNOWNED_ID}]

    _patch_service(monkeypatch, "list_nlm_notebooks", _list)

    got = await nlm_router.nlm_list_notebooks(
        db=_FakeSession(), current_user=_user(role="admin")
    )
    assert len(got) == 3


# --------------------------------------------------------------------------- #
# Create claims ownership
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_create_records_the_creator(monkeypatch):
    async def _create(title):
        return {"id": "nb-brand-new", "title": title}

    _patch_service(monkeypatch, "create_nlm_notebook", _create)
    session = _FakeSession()

    await nlm_router.nlm_create_notebook(
        body=NLMNotebookCreate(title="Q3 planning"), db=session, current_user=_user()
    )

    rows = [o for o in session.added if isinstance(o, NotebookLMPassthroughOwner)]
    assert len(rows) == 1
    assert rows[0].nlm_id == "nb-brand-new"
    assert rows[0].owner_employee_id == OWNER
    assert session.commits == 1


# --------------------------------------------------------------------------- #
# Reads and chat must not reach Google for a foreign notebook
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_chat_on_a_foreign_notebook_never_reaches_google(monkeypatch):
    _patch_service(monkeypatch, "nlm_chat_ask", _never_called("chat"))

    with pytest.raises(HTTPException) as exc:
        await nlm_router.nlm_chat(
            nlm_id=FOREIGN_ID,
            body=NLMChatAsk(question="what are the salary bands?"),
            db=_session_with_owned_notebook(),
            current_user=_user(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_source_listing_on_a_foreign_notebook_never_reaches_google(monkeypatch):
    _patch_service(monkeypatch, "list_nlm_sources", _never_called("source listing"))

    with pytest.raises(HTTPException) as exc:
        await nlm_router.nlm_list_sources(
            nlm_id=FOREIGN_ID, db=_session_with_owned_notebook(), current_user=_user()
        )
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# Delete: guarded, and honest about failure
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_delete_of_a_foreign_notebook_never_reaches_google(monkeypatch):
    _patch_service(monkeypatch, "delete_nlm_notebook", _never_called("delete"))
    session = _session_with_owned_notebook()

    with pytest.raises(HTTPException) as exc:
        await nlm_router.nlm_delete_notebook(
            nlm_id=FOREIGN_ID, db=session, current_user=_user()
        )
    assert exc.value.status_code == 404
    assert session.deletes == []


@pytest.mark.asyncio
async def test_delete_drops_the_ownership_row_on_success(monkeypatch):
    async def _delete(_nlm_id):
        return True

    _patch_service(monkeypatch, "delete_nlm_notebook", _delete)
    session = _session_with_owned_notebook()

    await nlm_router.nlm_delete_notebook(
        nlm_id=OWNED_ID, db=session, current_user=_user()
    )

    assert len(session.deletes) == 1
    sql, params = session.deletes[0]
    assert "notebooklm_passthrough_owners" in sql
    assert OWNED_ID in params.values()
    assert session.commits == 1


@pytest.mark.asyncio
async def test_delete_refuses_to_report_success_when_google_refused(monkeypatch):
    """The route answered 204 unconditionally, so the UI dropped notebooks that survived."""
    async def _delete(_nlm_id):
        return False

    _patch_service(monkeypatch, "delete_nlm_notebook", _delete)
    session = _session_with_owned_notebook()

    with pytest.raises(HTTPException) as exc:
        await nlm_router.nlm_delete_notebook(
            nlm_id=OWNED_ID, db=session, current_user=_user()
        )
    assert exc.value.status_code == 502
    # The notebook still exists in Google, so the ownership row must survive with it —
    # dropping it here would make the notebook admin-only and unreachable to its owner.
    assert session.deletes == []


@pytest.mark.asyncio
async def test_delete_maps_an_expired_google_session_to_401(monkeypatch):
    async def _delete(_nlm_id):
        raise RuntimeError("Authentication expired, please re-authenticate")

    _patch_service(monkeypatch, "delete_nlm_notebook", _delete)

    with pytest.raises(HTTPException) as exc:
        await nlm_router.nlm_delete_notebook(
            nlm_id=OWNED_ID, db=_session_with_owned_notebook(), current_user=_user()
        )
    assert exc.value.status_code == 401


def test_the_service_no_longer_swallows_delete_failures():
    """A bare `except Exception: return False` here is what made 204-on-failure possible."""
    from app.services.notebooklm_service import delete_nlm_notebook

    src = inspect.getsource(delete_nlm_notebook)
    assert "except Exception" not in src


# --------------------------------------------------------------------------- #
# Meta: a new passthrough endpoint cannot be added without a guard
# --------------------------------------------------------------------------- #

def _calls_the_guard(endpoint) -> bool:
    """True if the handler body really calls `_resolve_nlm_notebook(..., nlm_id)`.

    Matched on the parsed AST, not on the source text: a substring check also matches the
    call sitting behind a `#`, which is exactly the state a half-finished refactor leaves
    the file in.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "_resolve_nlm_notebook":
            continue
        if any(getattr(a, "id", None) == "nlm_id" for a in node.args):
            return True
    return False


def test_every_passthrough_endpoint_taking_an_nlm_id_resolves_ownership():
    """Per-route tests cannot cover an endpoint that does not exist yet.

    Walks the router's own route table, so an endpoint added to the passthrough surface is
    covered the moment it is registered. Static rather than executed: several of these
    handlers need a live NotebookLM session and a real DB to call.
    """
    from fastapi.routing import APIRoute

    unguarded = []
    checked = 0
    for route in nlm_router.router.routes:
        if not isinstance(route, APIRoute) or "{nlm_id}" not in route.path:
            continue
        checked += 1
        if not _calls_the_guard(route.endpoint):
            methods = ",".join(sorted(route.methods or []))
            unguarded.append(f"{methods} {route.path}")

    assert checked >= 12, f"expected the whole passthrough surface, walked {checked}"
    assert not unguarded, (
        "passthrough endpoint(s) forward a caller-supplied nlm_id to the shared Google "
        "account without resolving ownership:\n  " + "\n  ".join(unguarded)
    )
