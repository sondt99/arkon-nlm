"""The wiki draft review flow — propose, list, approve, reject.

Issue #60 lists app/routers/wiki_drafts.py as untested, and this is the router that
decides whose text lands in the knowledge base other people then read as fact. Two
distinct authorization questions live here — *propose* (contributor) and *review*
(editor) — and the whole point of the workflow collapses if either one drifts to the
other's threshold.

Handlers are driven directly with a stub session; the service layer
(app/services/wiki_service.py) runs for real so that "approve writes the page and creates
a revision" is an observation, not a mock assertion.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database.models import (
    AuditLog,
    Employee,
    WikiPage,
    WikiPageDraft,
    WikiPageRevision,
)
from app.routers import wiki_drafts as drafts_router
from app.services import wiki_service

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
WORKSPACE_ID = uuid.uuid4()


class _FakeSession:
    """Primary-key lookups from a dict; statements recorded; ids assigned on add().

    ``add`` assigns the primary key because SQLAlchemy's Python-side ``default=uuid4``
    only fires during a real flush, and the draft response model requires the id.
    """

    def __init__(self, *rows, drafts=(), joined=()):
        self._rows = {(type(r).__name__, r.id): r for r in rows}
        self.query_results = list(drafts)
        # (draft, page) tuples for the joined draft-queue query, which reads its rows with
        # .all() rather than .scalars(). The stub cannot evaluate a WHERE clause, so the
        # queue's scoping is asserted on the emitted statement instead — see
        # test_the_draft_queue_scopes_in_sql_before_the_limit.
        self.joined_results = list(joined)
        self.statements: list[object] = []
        self.added: list[object] = []
        self.commits = 0

    async def get(self, model, ident):
        return self._rows.get((model.__name__, ident))

    async def execute(self, statement):
        self.statements.append(statement)
        rows = self.query_results
        joined = self.joined_results
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: rows),
            all=lambda: joined,
        )

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
            obj.created_at = NOW
            obj.updated_at = NOW
        self.added.append(obj)
        self._rows[(type(obj).__name__, obj.id)] = obj

    async def flush(self):
        pass

    async def refresh(self, _obj):
        pass

    async def commit(self):
        self.commits += 1

    def added_of(self, model) -> list:
        return [o for o in self.added if isinstance(o, model)]


def _user(*perms: str, role: str = "employee", user_id=None):
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        name="Reviewer",
        role=role,
        department_id=uuid.uuid4(),
        custom_role=SimpleNamespace(permissions=list(perms)) if perms else None,
    )


def _employee_row(name: str = "Author") -> Employee:
    emp = Employee(name=name, email=f"{name.lower()}@example.com", role="employee")
    emp.id = uuid.uuid4()
    return emp


def _page(scope_type: str = "global", scope_id=None, slug: str = "runbook") -> WikiPage:
    page = WikiPage(
        slug=slug,
        title="Runbook",
        page_type="concept",
        content_md="original body",
        summary="",
        scope_type=scope_type,
        scope_id=scope_id,
    )
    page.id = uuid.uuid4()
    page.version = 3
    return page


def _draft(page: WikiPage, status: str = "pending", author=None) -> WikiPageDraft:
    draft = WikiPageDraft(
        page_id=page.id,
        author_id=author.id if author else None,
        content_md="proposed body",
        note="please review",
        status=status,
        source="web_ui",
    )
    draft.id = uuid.uuid4()
    draft.reviewed_by_id = None
    draft.reviewed_at = None
    draft.reviewer_note = None
    draft.created_at = NOW
    draft.updated_at = NOW
    return draft


@pytest.fixture(autouse=True)
def _no_link_graph_writes(monkeypatch):
    """approve_draft rebuilds the wikilink graph, which needs real SQL. Not under test."""

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(wiki_service, "refresh_links", _noop)


def _workspace_role(monkeypatch, role):
    async def _role(_db, _user, _wid):
        return role

    monkeypatch.setattr(drafts_router, "get_workspace_role", _role)


# --------------------------------------------------------------------------- #
# _can_propose / _can_review — the two thresholds
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_workspace_viewer_cannot_propose(monkeypatch):
    _workspace_role(monkeypatch, "viewer")
    page = _page("project", WORKSPACE_ID)
    assert await drafts_router._can_propose(None, _user("wiki:write:all"), page) is False


@pytest.mark.asyncio
async def test_a_workspace_contributor_can_propose(monkeypatch):
    _workspace_role(monkeypatch, "contributor")
    page = _page("project", WORKSPACE_ID)
    assert await drafts_router._can_propose(None, _user(), page) is True


@pytest.mark.asyncio
async def test_a_workspace_contributor_cannot_review(monkeypatch):
    """Proposing and approving are separate privileges; collapsing them removes the
    review step entirely and lets a contributor publish their own text."""
    _workspace_role(monkeypatch, "contributor")
    page = _page("project", WORKSPACE_ID)
    assert await drafts_router._can_review(None, _user(), page) is False


@pytest.mark.asyncio
async def test_a_workspace_editor_can_review(monkeypatch):
    _workspace_role(monkeypatch, "editor")
    page = _page("project", WORKSPACE_ID)
    assert await drafts_router._can_review(None, _user(), page) is True


@pytest.mark.asyncio
async def test_a_non_member_cannot_review_a_workspace_page(monkeypatch):
    """get_workspace_role returns None for non-members — that must not read as a role."""
    _workspace_role(monkeypatch, None)
    page = _page("project", WORKSPACE_ID)
    assert await drafts_router._can_review(None, _user("wiki:write:all"), page) is False


@pytest.mark.asyncio
async def test_global_wiki_write_all_does_not_reach_inside_a_workspace(monkeypatch):
    """A workspace page is private to its members. Letting the org-wide grant satisfy the
    workspace check would make every private workspace wiki editable company-wide."""
    _workspace_role(monkeypatch, "viewer")
    page = _page("project", WORKSPACE_ID)
    assert await drafts_router._can_review(None, _user("wiki:write:all"), page) is False


@pytest.mark.asyncio
async def test_own_dept_wiki_write_can_propose_but_not_review_a_global_page():
    """The default employee permission set includes wiki:write:own_dept. If that also
    satisfied review, every employee could approve their own drafts on global pages."""
    page = _page()
    proposer = _user("wiki:write:own_dept")
    assert await drafts_router._can_propose(None, proposer, page) is True
    assert await drafts_router._can_review(None, proposer, page) is False


@pytest.mark.asyncio
async def test_wiki_write_all_can_review_a_global_page():
    page = _page()
    assert await drafts_router._can_review(None, _user("wiki:write:all"), page) is True


@pytest.mark.asyncio
async def test_a_reader_cannot_propose_at_all():
    page = _page()
    assert await drafts_router._can_propose(None, _user("wiki:read:all"), page) is False


# --------------------------------------------------------------------------- #
# POST /api/wiki/pages/{slug}/drafts
# --------------------------------------------------------------------------- #

def _propose_body(content: str = "proposed body", note: str | None = "why"):
    return drafts_router.ProposeDraftRequest(content_md=content, note=note)


@pytest.mark.asyncio
async def test_propose_refuses_the_reserved_pages():
    """_index and _log are machine-generated; a draft against them would be applied by
    the compiler's next run and silently lost, or corrupt the catalog."""
    for slug in (wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG):
        with pytest.raises(HTTPException) as exc:
            await drafts_router.propose_draft(
                slug, _propose_body(), db=_FakeSession(), user=_user(role="admin")
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_propose_404s_for_an_unknown_page(monkeypatch):
    async def _missing(_db, _slug):
        return None

    monkeypatch.setattr(wiki_service, "get_page_by_slug_any_scope", _missing)
    with pytest.raises(HTTPException) as exc:
        await drafts_router.propose_draft(
            "nope", _propose_body(), db=_FakeSession(), user=_user("wiki:write:all")
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_propose_is_denied_without_write_rights(monkeypatch):
    page = _page()

    async def _found(_db, _slug):
        return page

    monkeypatch.setattr(wiki_service, "get_page_by_slug_any_scope", _found)
    db = _FakeSession(page)
    with pytest.raises(HTTPException) as exc:
        await drafts_router.propose_draft(
            page.slug, _propose_body(), db=db, user=_user("wiki:read:all")
        )
    assert exc.value.status_code == 403
    assert db.added_of(WikiPageDraft) == []


@pytest.mark.asyncio
async def test_propose_creates_a_pending_draft_and_leaves_the_page_alone(monkeypatch):
    """A proposal must not be a write. If the draft body reached content_md here, the
    review step would be decorative."""
    page = _page()
    author = _employee_row()

    async def _found(_db, _slug):
        return page

    monkeypatch.setattr(wiki_service, "get_page_by_slug_any_scope", _found)
    db = _FakeSession(page, author)

    result = await drafts_router.propose_draft(
        page.slug,
        _propose_body("a better body"),
        db=db,
        user=_user("wiki:write:own_dept", user_id=author.id),
    )

    assert page.content_md == "original body"
    assert page.version == 3
    assert result.status == "pending"
    assert result.content_md == "a better body"
    assert result.source == "web_ui"
    assert result.author_id == author.id
    assert db.commits == 1


@pytest.mark.asyncio
async def test_propose_writes_an_audit_entry(monkeypatch):
    page = _page()

    async def _found(_db, _slug):
        return page

    monkeypatch.setattr(wiki_service, "get_page_by_slug_any_scope", _found)
    db = _FakeSession(page)
    author = _user("wiki:write:all")

    await drafts_router.propose_draft(page.slug, _propose_body(), db=db, user=author)

    entries = db.added_of(AuditLog)
    assert len(entries) == 1
    assert (entries[0].action, entries[0].resource_type) == ("create", "wiki_draft")
    assert entries[0].principal_id == author.id


@pytest.mark.asyncio
async def test_propose_rejects_an_empty_body():
    """Validated on the request model, so an empty draft never reaches the queue."""
    with pytest.raises(ValueError):
        drafts_router.ProposeDraftRequest(content_md="   ")


@pytest.mark.asyncio
async def test_propose_rejects_an_oversized_body():
    with pytest.raises(ValueError):
        drafts_router.ProposeDraftRequest(content_md="x" * 50_001)


# --------------------------------------------------------------------------- #
# POST /api/wiki/drafts/{id}/approve
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_approve_is_denied_without_review_rights(monkeypatch):
    """The draft's own author holding wiki:write:own_dept must not be able to merge it."""
    page = _page()
    author = _employee_row()
    draft = _draft(page, author=author)
    db = _FakeSession(page, author, draft)

    with pytest.raises(HTTPException) as exc:
        await drafts_router.approve_draft(
            str(draft.id),
            drafts_router.ApproveDraftRequest(),
            db=db,
            user=_user("wiki:write:own_dept", user_id=author.id),
        )

    assert exc.value.status_code == 403
    assert draft.status == "pending"
    assert page.content_md == "original body"


@pytest.mark.asyncio
async def test_approve_refuses_a_draft_that_is_already_resolved():
    """Double-approve would create a second revision from stale content and re-stamp the
    reviewer fields, overwriting who actually made the decision."""
    page = _page()
    draft = _draft(page, status="approved")
    db = _FakeSession(page, draft)

    with pytest.raises(HTTPException) as exc:
        await drafts_router.approve_draft(
            str(draft.id),
            drafts_router.ApproveDraftRequest(),
            db=db,
            user=_user(role="admin"),
        )

    assert exc.value.status_code == 400
    assert page.version == 3


@pytest.mark.asyncio
async def test_approve_refuses_an_already_rejected_draft():
    page = _page()
    draft = _draft(page, status="rejected")
    db = _FakeSession(page, draft)
    with pytest.raises(HTTPException) as exc:
        await drafts_router.approve_draft(
            str(draft.id),
            drafts_router.ApproveDraftRequest(),
            db=db,
            user=_user(role="admin"),
        )
    assert exc.value.status_code == 400
    assert page.content_md == "original body"


@pytest.mark.asyncio
async def test_approve_publishes_the_draft_and_records_a_revision():
    page = _page()
    author = _employee_row()
    draft = _draft(page, author=author)
    reviewer = _user(role="admin")
    db = _FakeSession(page, author, draft)

    result = await drafts_router.approve_draft(
        str(draft.id),
        drafts_router.ApproveDraftRequest(reviewer_note="looks right"),
        db=db,
        user=reviewer,
    )

    assert page.content_md == "proposed body"
    assert page.version == 4, "the page version must advance so caches and diffs notice"
    assert draft.status == "approved"
    assert draft.reviewed_by_id == reviewer.id
    assert draft.reviewer_note == "looks right"
    assert result.status == "approved"
    assert db.commits == 1

    revisions = db.added_of(WikiPageRevision)
    assert len(revisions) == 1
    assert revisions[0].version == 4
    assert revisions[0].content_md == "proposed body"
    assert revisions[0].change_type == "draft_approved"
    assert revisions[0].changed_by_id == reviewer.id
    assert revisions[0].draft_id == draft.id


@pytest.mark.asyncio
async def test_approve_publishes_the_reviewers_edit_instead_of_the_proposal():
    """The reviewer's edited text is what they read and signed off on. Publishing the
    original draft body instead would ship content nobody approved."""
    page = _page()
    draft = _draft(page)
    db = _FakeSession(page, draft)

    await drafts_router.approve_draft(
        str(draft.id),
        drafts_router.ApproveDraftRequest(edited_content_md="  corrected body  "),
        db=db,
        user=_user(role="admin"),
    )

    assert page.content_md == "corrected body"
    assert db.added_of(WikiPageRevision)[0].content_md == "corrected body"
    assert draft.content_md == "proposed body", "the proposal itself must stay on record"


@pytest.mark.asyncio
async def test_approve_writes_an_audit_entry_naming_the_reviewer():
    page = _page()
    draft = _draft(page)
    reviewer = _user("wiki:write:all")
    db = _FakeSession(page, draft)

    await drafts_router.approve_draft(
        str(draft.id), drafts_router.ApproveDraftRequest(), db=db, user=reviewer
    )

    entries = db.added_of(AuditLog)
    assert len(entries) == 1
    assert (entries[0].action, entries[0].resource_type) == ("update", "wiki_draft")
    assert entries[0].principal_id == reviewer.id
    assert page.slug in entries[0].reason


# --------------------------------------------------------------------------- #
# POST /api/wiki/drafts/{id}/reject
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_reject_requires_a_reason():
    """A rejection with no note is unactionable for the author, so it is refused at the
    model boundary rather than stored empty."""
    with pytest.raises(ValueError):
        drafts_router.RejectDraftRequest(reviewer_note="   ")


@pytest.mark.asyncio
async def test_reject_is_denied_without_review_rights():
    page = _page()
    draft = _draft(page)
    db = _FakeSession(page, draft)
    with pytest.raises(HTTPException) as exc:
        await drafts_router.reject_draft(
            str(draft.id),
            drafts_router.RejectDraftRequest(reviewer_note="no"),
            db=db,
            user=_user("wiki:write:own_dept"),
        )
    assert exc.value.status_code == 403
    assert draft.status == "pending"


@pytest.mark.asyncio
async def test_reject_never_touches_the_page():
    """The failure this prevents: a rejected draft that still published its content."""
    page = _page()
    draft = _draft(page)
    db = _FakeSession(page, draft)

    result = await drafts_router.reject_draft(
        str(draft.id),
        drafts_router.RejectDraftRequest(reviewer_note="wrong runbook"),
        db=db,
        user=_user(role="admin"),
    )

    assert page.content_md == "original body"
    assert page.version == 3
    assert db.added_of(WikiPageRevision) == []
    assert result.status == "rejected"
    assert result.reviewer_note == "wrong runbook"


@pytest.mark.asyncio
async def test_reject_refuses_a_draft_that_is_already_resolved():
    page = _page()
    draft = _draft(page, status="approved")
    db = _FakeSession(page, draft)
    with pytest.raises(HTTPException) as exc:
        await drafts_router.reject_draft(
            str(draft.id),
            drafts_router.RejectDraftRequest(reviewer_note="too late"),
            db=db,
            user=_user(role="admin"),
        )
    assert exc.value.status_code == 400
    assert draft.status == "approved"
    assert draft.reviewer_note is None


# --------------------------------------------------------------------------- #
# Reads: GET /api/wiki/drafts, /api/wiki/drafts/{id}, /api/wiki/pages/{slug}/drafts
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_malformed_draft_id_is_400_not_500():
    with pytest.raises(HTTPException) as exc:
        await drafts_router._load_draft(_FakeSession(), "not-a-uuid")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_an_unknown_draft_id_is_404():
    with pytest.raises(HTTPException) as exc:
        await drafts_router._load_draft(_FakeSession(), str(uuid.uuid4()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reading_a_draft_requires_review_rights():
    """Drafts can contain unreviewed and possibly wrong or sensitive text; the read is
    gated on the same threshold as the decision."""
    page = _page()
    draft = _draft(page)
    db = _FakeSession(page, draft)
    with pytest.raises(HTTPException) as exc:
        await drafts_router.get_draft(
            str(draft.id), db=db, user=_user("wiki:read:all", "wiki:write:own_dept")
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reading_a_draft_returns_the_documented_shape():
    page = _page()
    author = _employee_row("Mai")
    draft = _draft(page, author=author)
    db = _FakeSession(page, author, draft)

    result = await drafts_router.get_draft(
        str(draft.id), db=db, user=_user("wiki:write:all")
    )

    assert result.id == draft.id
    assert result.page_slug == page.slug
    assert result.page_title == page.title
    assert result.author_name == "Mai"
    assert result.status == "pending"
    assert result.reviewed_at is None


@pytest.mark.asyncio
async def test_the_draft_queue_hides_drafts_the_caller_cannot_review():
    """wiki:read is enough to call this route, so the reviewability rule is the only thing
    keeping another workspace's pending text out of the queue.

    The rule now lives in `_build_reviewable_filter` (it used to be a per-row loop that ran
    *after* LIMIT), so this asserts the clause itself. A caller holding only wiki:read:all
    gets a predicate that no global page can satisfy — it demands editor+ membership of the
    page's workspace — while wiki:write:all admits global pages as well.
    """
    read_only = drafts_router._build_reviewable_filter

    reader_clause = read_only(_user("wiki:read:all"))
    assert reader_clause is not None, "wiki:read:all must not see the whole queue"
    reader_sql = str(reader_clause.compile(compile_kwargs={"literal_binds": True}))
    assert "project_members" in reader_sql
    assert "scope_type" in reader_sql
    # No disjunct admits a global page: every branch requires the project scope.
    assert reader_sql.count("scope_type") == reader_sql.count("wiki_pages.scope_type")
    assert " != " not in reader_sql, "a `scope_type != project` branch would let globals in"

    writer_clause = read_only(_user("wiki:write:all"))
    writer_sql = str(writer_clause.compile(compile_kwargs={"literal_binds": True}))
    assert "!=" in writer_sql, "wiki:write:all must also admit non-project (global) pages"

    admin_clause = read_only(_user(role="admin"))
    assert admin_clause is None, "admins review everything"


@pytest.mark.asyncio
async def test_the_draft_queue_scopes_in_sql_before_the_limit():
    """LIMIT used to be applied before the per-row review check, so a scoped reviewer got an
    arbitrarily truncated page — anything from all 50 rows to none — with no signal that the
    rest of their queue existed. The predicate must reach the same statement as the LIMIT.
    """
    page = _page()
    draft = _draft(page)
    db = _FakeSession(page, draft, joined=[(draft, page)])

    await drafts_router.list_all_drafts(
        status="pending", limit=50, db=db, user=_user("wiki:read:all")
    )

    statement = db.statements[-1]
    assert statement._limit_clause is not None, "the query must still be bounded"
    where_sql = str(statement.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert "project_members" in where_sql, (
        "the reviewability predicate is not in this statement's WHERE clause, so LIMIT is "
        "again being applied to rows the caller cannot review"
    )


@pytest.mark.asyncio
async def test_listing_a_pages_drafts_requires_review_rights(monkeypatch):
    page = _page()

    async def _found(_db, _slug):
        return page

    monkeypatch.setattr(wiki_service, "get_page_by_slug_any_scope", _found)
    db = _FakeSession(page)

    with pytest.raises(HTTPException) as exc:
        await drafts_router.list_page_drafts(
            page.slug, status=None, db=db, user=_user("wiki:write:own_dept")
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_listing_an_unknown_pages_drafts_is_404(monkeypatch):
    async def _missing(_db, _slug):
        return None

    monkeypatch.setattr(wiki_service, "get_page_by_slug_any_scope", _missing)
    with pytest.raises(HTTPException) as exc:
        await drafts_router.list_page_drafts(
            "nope", status=None, db=_FakeSession(), user=_user(role="admin")
        )
    assert exc.value.status_code == 404


def test_the_draft_queue_route_requires_wiki_read(route_guards):
    """Deleting this guard would expose pending drafts to any authenticated account."""
    assert [p for p, _ in route_guards("GET", "/api/wiki/drafts")] == ["wiki:read"]


@pytest.mark.asyncio
async def test_the_draft_queue_guard_denies_a_user_without_wiki_read(route_guards):
    (_, guard), = route_guards("GET", "/api/wiki/drafts")
    with pytest.raises(HTTPException) as exc:
        await guard(_user("doc:read:all"))
    assert exc.value.status_code == 403
    assert await guard(_user("wiki:read:own_dept")) is not None
