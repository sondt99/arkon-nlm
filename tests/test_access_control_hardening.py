"""Permission boundaries closed for issue #86.

Four gaps, each one a rule that existed but did not bind:

  1. `sources.py` checked *which* departments a write may attach by iterating the submitted
     list. An empty list iterates zero times, and zero `source_departments` rows is how this
     schema spells "global", so `doc:create:own_dept` published org-wide by omitting a field.
  2. `wiki.py`'s delete guard tested `user.role not in ("admin", "super_admin")` — a role
     this codebase never assigns — which made `wiki:delete:*` dead, and resolved the slug
     in the global scope only, so project pages 404'd.
  3. `knowledge_types.py` gated an org-wide taxonomy behind department-scoped document
     permissions, so `:own_dept` was enough to rename and delete org-wide rows.
  4. `export_api.py` reused a conversation's stored workspace scope without re-checking that
     the caller is still a member.

Handlers are driven directly with stub sessions where the denial precedes any I/O; where
the rule now lives in a dependency, the guard is pulled off the registered route (see the
`route_guards` fixture) so a deleted decorator argument fails here.
"""

import io
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from app.database.models import (
    ChatConversation,
    KnowledgeType,
    Source,
    SourceDepartment,
    WikiPage,
)
from app.routers import export_api as export_router
from app.routers import knowledge_types as kt_router
from app.routers import sources as sources_router
from app.routers import wiki as wiki_router

MY_DEPT = uuid.uuid4()
OTHER_DEPT = uuid.uuid4()
WORKSPACE = uuid.uuid4()


def _user(*perms: str, role: str = "employee", dept=MY_DEPT, user_id=None):
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        name="Actor",
        role=role,
        department_id=dept,
        custom_role=SimpleNamespace(permissions=list(perms)) if perms else None,
        custom_role_id=None,
        is_active=True,
    )


class _ExplodingSession:
    """Any use at all is a test failure: the denial must precede every query."""

    def __getattr__(self, name):
        raise AssertionError(f"the handler reached db.{name} after it should have refused")


# --------------------------------------------------------------------------- #
# 1. doc:create:own_dept must not be able to publish org-wide
# --------------------------------------------------------------------------- #

def test_an_empty_department_list_is_refused_for_own_dept_callers():
    """The whole defect in one assertion: zero departments == global visibility."""
    with pytest.raises(HTTPException) as exc:
        sources_router._validate_department_scope(
            _user("doc:create:own_dept"), [], "create"
        )
    assert exc.value.status_code == 403
    assert "doc:create:all" in exc.value.detail


def test_a_foreign_department_is_still_refused():
    with pytest.raises(HTTPException) as exc:
        sources_router._validate_department_scope(
            _user("doc:create:own_dept"), [OTHER_DEPT], "create"
        )
    assert exc.value.status_code == 403


def test_own_department_is_allowed():
    """The allow path, so the denials above cannot be satisfied by refusing everything."""
    sources_router._validate_department_scope(
        _user("doc:create:own_dept"), [MY_DEPT], "create"
    )


def test_the_all_scope_may_still_publish_globally():
    """`doc:create:all` is precisely the grant that authorises a no-department document."""
    sources_router._validate_department_scope(_user("doc:create:all"), [], "create")
    sources_router._validate_department_scope(_user("doc:create:all"), [OTHER_DEPT], "create")
    sources_router._validate_department_scope(_user(role="admin"), [], "create")


def test_the_edit_action_reads_the_edit_permission_not_create():
    """PATCH is gated on doc:edit; a doc:create:all holder must not inherit the exemption."""
    with pytest.raises(HTTPException):
        sources_router._validate_department_scope(
            _user("doc:edit:own_dept", "doc:create:all"), [], "edit"
        )
    sources_router._validate_department_scope(_user("doc:edit:all"), [], "edit")


@pytest.mark.asyncio
async def test_add_url_source_refuses_a_departmentless_url():
    """`add_url_source` had no department check of any kind — not even the loop."""
    body = sources_router.SourceCreateURL(url="https://example.test/doc", department_ids=[])
    with pytest.raises(HTTPException) as exc:
        await sources_router.add_url_source(
            body, db=_ExplodingSession(), user=_user("doc:create:own_dept")
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_add_url_source_refuses_a_foreign_department():
    body = sources_router.SourceCreateURL(
        url="https://example.test/doc", department_ids=[OTHER_DEPT]
    )
    with pytest.raises(HTTPException) as exc:
        await sources_router.add_url_source(
            body, db=_ExplodingSession(), user=_user("doc:create:own_dept")
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_upload_source_refuses_a_departmentless_file():
    upload = UploadFile(file=io.BytesIO(b"# body"), filename="note.md")
    with pytest.raises(HTTPException) as exc:
        await sources_router.upload_source(
            file=upload,
            title=None,
            knowledge_type_id=None,
            department_ids=None,
            scope_type=None,
            scope_id=None,
            db=_ExplodingSession(),
            user=_user("doc:create:own_dept"),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_upload_zip_refuses_a_departmentless_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("note.md", "# body")
    buffer.seek(0)
    upload = UploadFile(file=buffer, filename="bundle.zip")

    with pytest.raises(HTTPException) as exc:
        await sources_router.upload_zip_archive(
            file=upload,
            knowledge_type_id=None,
            department_ids=None,
            scope_type=None,
            scope_id=None,
            db=_ExplodingSession(),
            user=_user("doc:create:own_dept"),
        )
    assert exc.value.status_code == 403


class _SourceSession:
    """Enough session for update_source's pre-write path: get + SourceDepartment lookup."""

    def __init__(self, source, dept_ids=()):
        self.source = source
        self.dept_ids = list(dept_ids)
        self.statements: list[object] = []

    async def get(self, _model, _ident):
        return self.source

    async def execute(self, statement):
        self.statements.append(statement)
        rows = [(d,) for d in self.dept_ids]
        return SimpleNamespace(
            all=lambda: rows,
            scalars=lambda: SimpleNamespace(all=lambda: [], one=lambda: self.source),
            scalar_one=lambda: 0,
            scalar_one_or_none=lambda: None,
        )

    def add(self, _row):
        raise AssertionError("update_source wrote a SourceDepartment row after refusing")

    async def flush(self):
        raise AssertionError("update_source flushed after it should have refused")


@pytest.mark.asyncio
async def test_patch_cannot_clear_a_sources_departments_to_reach_global():
    """PATCH with `department_ids: []` deletes every row, i.e. the second route to global."""
    source = Source(title="Handbook", source_type="file", status="ready")
    source.id = uuid.uuid4()
    source.scope_type = "global"
    source.scope_id = None
    db = _SourceSession(source, dept_ids=[MY_DEPT])

    with pytest.raises(HTTPException) as exc:
        await sources_router.update_source(
            source.id,
            sources_router.SourceUpdate(department_ids=[]),
            db=db,
            _user=_user("doc:edit:own_dept"),
        )
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# 2. wiki:delete is a real permission again
# --------------------------------------------------------------------------- #

def _page(scope_type="global", scope_id=None, slug="runbook"):
    page = WikiPage(
        slug=slug, title="Runbook", page_type="concept",
        content_md="body", summary="", scope_type=scope_type, scope_id=scope_id,
    )
    page.id = uuid.uuid4()
    page.version = 1
    return page


class _WikiSession:
    def __init__(self, member_role=None):
        self.member_role = member_role
        self.deleted: list[object] = []

    async def execute(self, _statement):
        role = self.member_role
        return SimpleNamespace(
            scalar_one_or_none=lambda: role,
            scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None),
            all=lambda: [],
        )

    async def delete(self, row):
        self.deleted.append(row)

    async def flush(self):
        pass

    async def commit(self):
        pass

    def add(self, _row):
        pass


@pytest.fixture
def wiki_pages(monkeypatch):
    """Route wiki_service's slug lookups at a dict, and neuter the cascade side effects."""
    state = {"any_scope": None, "global": None, "cascaded": []}

    async def _any_scope(_db, _slug):
        return state["any_scope"]

    async def _global(_db, _slug, **_kwargs):
        return state["global"]

    async def _cascade(_db, slug):
        state["cascaded"].append(slug)

    async def _noop(*_args, **_kwargs):
        return None

    from app.services import wiki_service

    monkeypatch.setattr(wiki_service, "get_page_by_slug_any_scope", _any_scope)
    monkeypatch.setattr(wiki_service, "get_page_by_slug", _global)
    monkeypatch.setattr(wiki_service, "delete_page_cascade", _cascade)
    monkeypatch.setattr(wiki_service, "regenerate_index", _noop)
    monkeypatch.setattr(wiki_service, "append_log", _noop)
    monkeypatch.setattr("app.routers.wiki.log_audit", _noop)
    return state


@pytest.mark.asyncio
async def test_own_dept_cannot_delete_a_global_wiki_page(wiki_pages):
    """Global wiki is org-wide, so deleting from it needs the :all grant — mirroring how
    direct_edit_wiki_page requires wiki:write:all for the same pages."""
    page = _page()
    wiki_pages["any_scope"] = wiki_pages["global"] = page
    db = _WikiSession()

    with pytest.raises(HTTPException) as exc:
        await wiki_router.delete_wiki_page("runbook", db=db, user=_user("wiki:delete:own_dept"))

    assert exc.value.status_code == 403
    assert "wiki:delete:all" in exc.value.detail
    assert wiki_pages["cascaded"] == [], "the cascade ran on a refused delete"


@pytest.mark.asyncio
async def test_wiki_delete_all_can_now_delete_a_global_page(wiki_pages):
    """This is the permission that did nothing: the old guard rejected every non-admin."""
    page = _page()
    wiki_pages["any_scope"] = wiki_pages["global"] = page
    db = _WikiSession()

    result = await wiki_router.delete_wiki_page(
        "runbook", db=db, user=_user("wiki:delete:all")
    )

    assert result == {"ok": True, "deleted_slug": "runbook"}
    assert wiki_pages["cascaded"] == ["runbook"]


@pytest.mark.asyncio
async def test_a_workspace_non_member_gets_404_not_403(wiki_pages):
    """403 would confirm that a page exists inside a workspace they cannot see. The old
    global-only lookup answered 404 here too, so this must not regress into a leak."""
    page = _page(scope_type="project", scope_id=WORKSPACE)
    wiki_pages["any_scope"] = page
    wiki_pages["global"] = None
    db = _WikiSession(member_role=None)

    with pytest.raises(HTTPException) as exc:
        await wiki_router.delete_wiki_page("runbook", db=db, user=_user("wiki:delete:all"))

    assert exc.value.status_code == 404
    assert wiki_pages["cascaded"] == []


@pytest.mark.asyncio
async def test_a_workspace_viewer_is_refused_but_told_why(wiki_pages):
    """A member already knows the page exists, so 403 leaks nothing here."""
    page = _page(scope_type="project", scope_id=WORKSPACE)
    wiki_pages["any_scope"] = page
    wiki_pages["global"] = None
    db = _WikiSession(member_role="viewer")

    with pytest.raises(HTTPException) as exc:
        await wiki_router.delete_wiki_page(
            "runbook", db=db, user=_user("wiki:delete:own_dept")
        )

    assert exc.value.status_code == 403
    assert wiki_pages["cascaded"] == []


@pytest.mark.asyncio
async def test_a_workspace_editor_can_delete_a_project_page(wiki_pages):
    """Previously 404 for everyone: get_page_by_slug defaulted to scope_type='global'."""
    page = _page(scope_type="project", scope_id=WORKSPACE)
    wiki_pages["any_scope"] = page
    wiki_pages["global"] = None
    db = _WikiSession(member_role="editor")

    result = await wiki_router.delete_wiki_page(
        "runbook", db=db, user=_user("wiki:delete:own_dept")
    )

    assert result["ok"] is True
    assert wiki_pages["cascaded"] == ["runbook"]
    assert db.deleted == [page], (
        "delete_page_cascade resolves step 4 with the global-scoped lookup, so the router "
        "must remove the project-scoped row itself or the page survives its own deletion"
    )


@pytest.mark.asyncio
async def test_a_slug_shared_with_a_global_page_is_refused_rather_than_guessed(wiki_pages):
    """delete_page_cascade would delete the *global* namesake. Refuse instead."""
    project_page = _page(scope_type="project", scope_id=WORKSPACE)
    global_page = _page()
    wiki_pages["any_scope"] = project_page
    wiki_pages["global"] = global_page
    db = _WikiSession(member_role="editor")

    with pytest.raises(HTTPException) as exc:
        await wiki_router.delete_wiki_page("runbook", db=db, user=_user(role="admin"))

    assert exc.value.status_code == 409
    assert wiki_pages["cascaded"] == []
    assert db.deleted == []


# --------------------------------------------------------------------------- #
# 3. The knowledge-type taxonomy is org-wide, so its writes need the :all grant
# --------------------------------------------------------------------------- #

_TAXONOMY_ROUTES = [
    ("POST", "/api/knowledge-types", "doc:create:all", "doc:create:own_dept"),
    ("PUT", "/api/knowledge-types/{kt_id}", "doc:edit:all", "doc:edit:own_dept"),
    ("DELETE", "/api/knowledge-types/{kt_id}", "doc:delete:all", "doc:delete:own_dept"),
    ("PATCH", "/api/knowledge-types/reorder", "doc:edit:all", "doc:edit:own_dept"),
]


@pytest.mark.parametrize("method,path,required,insufficient", _TAXONOMY_ROUTES)
def test_taxonomy_writes_require_the_all_scope(route_guards, method, path, required, insufficient):
    """Read off the live route, not the source text: require_permission closes over the
    string, so a commented-out guard is invisible here rather than passing."""
    guards = route_guards(method, path)
    assert [p for p, _ in guards] == [required], (
        f"{method} {path} is gated on {[p for p, _ in guards]}; `{insufficient}` satisfies "
        "the unscoped `doc:*` form, which is how a Department Admin could delete an "
        "org-wide knowledge type"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,required,insufficient", _TAXONOMY_ROUTES)
async def test_the_own_dept_variant_is_actually_denied(
    route_guards, method, path, required, insufficient
):
    """Awaiting the real guard, so the parametrised wiring test above cannot be vacuous."""
    (_perm, check), = route_guards(method, path)

    with pytest.raises(HTTPException) as exc:
        await check(current_user=_user(insufficient))
    assert exc.value.status_code == 403

    assert await check(current_user=_user(required)) is not None
    assert await check(current_user=_user(role="admin")) is not None


class _KnowledgeTypeSession:
    """Slug-uniqueness lookups answer from a fixed list of taken slugs."""

    def __init__(self, kt=None, taken=()):
        self.kt = kt
        self.taken = dict(taken)
        self.statements: list[object] = []

    async def get(self, _model, _ident):
        return self.kt

    async def execute(self, statement):
        self.statements.append(statement)
        params = statement.compile().params
        clash = next(
            (self.taken[v] for v in params.values() if v in self.taken),
            None,
        )
        return SimpleNamespace(
            scalar_one_or_none=lambda: clash,
            scalar=lambda: 0,
            scalars=lambda: SimpleNamespace(all=lambda: []),
            all=lambda: [],
        )

    async def flush(self):
        pass

    def add(self, _row):
        pass


@pytest.mark.asyncio
async def test_renaming_a_knowledge_type_onto_a_taken_slug_is_a_409_not_a_500():
    """create had this pre-check; update did not, so the unique index raised IntegrityError
    — an uncaught 500 for what is plainly a client error."""
    kt = KnowledgeType(slug="sop", name="SOP", color="#fff", sort_order=1)
    kt.id = uuid.uuid4()
    db = _KnowledgeTypeSession(kt=kt, taken={"hr-policy": uuid.uuid4()})

    with pytest.raises(HTTPException) as exc:
        await kt_router.update_knowledge_type(
            kt.id,
            kt_router.KnowledgeTypeCreate(name="HR Policy", slug="hr-policy"),
            db=db,
            _user=_user("doc:edit:all"),
        )

    assert exc.value.status_code == 409
    assert kt.slug == "sop", "the rename must not land"


@pytest.mark.asyncio
async def test_keeping_your_own_slug_is_not_a_collision():
    """The exclude_id half: re-saving a type without renaming it must still work."""
    kt = KnowledgeType(slug="sop", name="SOP", color="#fff", sort_order=1)
    kt.id = uuid.uuid4()
    db = _KnowledgeTypeSession(kt=kt)

    result = await kt_router.update_knowledge_type(
        kt.id,
        kt_router.KnowledgeTypeCreate(name="SOP v2", slug="sop"),
        db=db,
        _user=_user("doc:edit:all"),
    )
    assert result.name == "SOP v2"


# --------------------------------------------------------------------------- #
# 4. A removed workspace member must not keep querying through their token
# --------------------------------------------------------------------------- #

class _ExportSession:
    def __init__(self, conversation, employee):
        self.conversation = conversation
        self.employee = employee
        self.member = False

    async def get(self, model, _ident):
        return self.employee if model.__name__ == "Employee" else None

    async def execute(self, _statement):
        conv = self.conversation
        member = "editor" if self.member else None
        # Order matters: the conversation lookup comes first, the membership probe second.
        if conv is not None:
            self.conversation = None
            return SimpleNamespace(scalar_one_or_none=lambda: conv)
        return SimpleNamespace(scalar_one_or_none=lambda: member)

    def add(self, _row):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, _row):
        pass


@pytest.mark.asyncio
async def test_export_chat_rechecks_workspace_membership_on_a_stored_conversation(monkeypatch):
    """The conversation's scope_type/scope_id is what generate_reply retrieves against, and
    it was written at creation time. Only ownership was re-checked on reuse."""
    employee = _user()
    conv = ChatConversation(
        employee_id=employee.id, title="Q", scope_type="project", scope_id=WORKSPACE
    )
    conv.id = uuid.uuid4()
    db = _ExportSession(conv, employee)

    async def _enabled(_db):
        return None

    async def _never(*_args, **_kwargs):
        raise AssertionError("generate_reply ran for a caller who left the workspace")

    monkeypatch.setattr(export_router, "_require_enabled", _enabled)
    monkeypatch.setattr(export_router.chat_service, "generate_reply", _never)
    monkeypatch.setattr(export_router.chat_service, "save_message", _never)

    identity = SimpleNamespace(
        employee_id=employee.id, wiki_visibility=lambda: (None, None)
    )
    body = export_router.ExportChatRequest(question="what?", conversation_id=conv.id)

    with pytest.raises(HTTPException) as exc:
        await export_router.export_chat(body, db=db, identity=identity)

    assert exc.value.status_code == 403
    assert "workspace" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_a_global_conversation_is_unaffected(monkeypatch):
    """The re-check must not break the ordinary (non-workspace) Export API conversation."""
    employee = _user()
    conv = ChatConversation(
        employee_id=employee.id, title="Q", scope_type="global", scope_id=None
    )
    conv.id = uuid.uuid4()
    db = _ExportSession(conv, employee)

    async def _enabled(_db):
        return None

    saved: list[str] = []

    async def _save(**kwargs):
        saved.append(kwargs["role"])
        return SimpleNamespace(id=uuid.uuid4())

    async def _reply(**_kwargs):
        return "answer", []

    monkeypatch.setattr(export_router, "_require_enabled", _enabled)
    monkeypatch.setattr(export_router.chat_service, "save_message", _save)
    monkeypatch.setattr(export_router.chat_service, "generate_reply", _reply)
    monkeypatch.setattr(export_router, "_generation_overrides", lambda _db: _empty())

    identity = SimpleNamespace(
        employee_id=employee.id, wiki_visibility=lambda: (None, None)
    )
    body = export_router.ExportChatRequest(question="what?", conversation_id=conv.id)

    result = await export_router.export_chat(body, db=db, identity=identity)
    assert result.answer == "answer"
    assert saved == ["user", "assistant"]


async def _empty() -> dict:
    return {}


@pytest.mark.asyncio
async def test_zero_departments_really_does_mean_visible_to_everyone():
    """Pins the premise the whole of item 1 rests on.

    If a source with no `source_departments` row were *invisible* rather than global, an
    empty department list would be a harmless omission instead of an escalation. It is
    global: `can_access_document` returns True for a stranger's own_dept read.
    """
    from app.services.permission_engine import can_access_document

    source = Source(title="Handbook", source_type="file", status="ready")
    source.id = uuid.uuid4()
    source.scope_type = "global"
    source.scope_id = None

    no_departments = _SourceSession(source, dept_ids=[])
    assert await can_access_document(
        no_departments, _user("doc:read:own_dept", dept=OTHER_DEPT), source, "read"
    ) is True

    other_department = _SourceSession(source, dept_ids=[MY_DEPT])
    assert await can_access_document(
        other_department, _user("doc:read:own_dept", dept=OTHER_DEPT), source, "read"
    ) is False
    assert SourceDepartment.__tablename__ == "source_departments"
