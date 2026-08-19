"""delete_page_cascade must stay inside one scope (issue #89).

Steps 1-3 used to run with no scope predicate while the page itself was fetched with
get_page_by_slug's `scope_type="global"` default. Deleting a global `budget` therefore
rewrote workspace W's `overview.content_md` and dropped W's internal `[[budget]]` edge —
stripping a link to a page that still existed — and passing a workspace-scoped slug found no
page to delete at all, yet still wrecked the graph and logged success.

FakeDB only understands `col == v` and `col.in_(...)` at the top level of a WHERE clause, so
the scope predicate (a nested `and_`) and `content_md.contains(...)` are invisible to it. The
`_matches` evaluator below reads the clause the service actually built instead, which is what
makes "is this query scoped?" observable at all. Two consequences, both deliberate:

  * `pg_insert(...)` is not modelled, so the *re-derived* edges cannot be asserted. Only the
    destructive half — which is the bug — is checked.
  * a `from_slug == x OR to_slug == x` DELETE is mis-read by FakeDB as an AND and removes
    nothing, so the wiki_links assertions also check the compiled SQL. That is the emitted
    statement, not the source text: a commented-out predicate changes it.
"""

import uuid

import pytest
from sqlalchemy.sql import Delete, operators

from app.database.models import WikiLink, WikiPage
from app.services import wiki_service

WORKSPACE = uuid.uuid4()


def _matches(clause, row) -> bool:
    """Evaluate the operators delete_page_cascade emits against one in-memory row."""
    if clause is None:
        return True
    op = getattr(clause, "operator", None)
    if op is operators.and_:
        return all(_matches(c, row) for c in clause.clauses)
    if op is operators.or_:
        return any(_matches(c, row) for c in clause.clauses)

    actual = getattr(row, clause.left.name, None)
    if op is operators.eq:
        return actual == clause.right.value
    if op is operators.is_:
        return actual is None
    if op is operators.contains_op:
        return clause.right.value in (actual or "")
    raise AssertionError(
        f"this test evaluates WHERE clauses itself and does not understand {op!r} — "
        "extend it rather than letting the term be ignored"
    )


def _page(slug: str, content: str, scope_type: str = "global", scope_id=None) -> WikiPage:
    return WikiPage(
        id=uuid.uuid4(),
        slug=slug,
        title=slug.title(),
        page_type="concept",
        content_md=content,
        summary="",
        knowledge_type_slugs=[],
        source_ids=[],
        scope_type=scope_type,
        scope_id=scope_id,
        version=1,
    )


@pytest.fixture
def wiki(fake_db):
    """FakeDB with real WHERE evaluation for wiki_pages."""
    fake_db.select_rows["wiki_pages"] = lambda stmt: [
        row for row in fake_db.table_rows("wiki_pages")
        if _matches(stmt.whereclause, row)
    ]
    return fake_db


def _link_deletes(db) -> list:
    return [
        s for s in db.statements
        if isinstance(s, Delete) and s.table.name == "wiki_links"
    ]


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def _slugs(rows) -> set[str]:
    return {r.slug for r in rows if isinstance(r, WikiPage)}


def _edges(rows) -> set[tuple[str, str]]:
    return {(r.from_slug, r.to_slug) for r in rows if isinstance(r, WikiLink)}


@pytest.mark.asyncio
async def test_deleting_a_global_page_leaves_another_scope_untouched(wiki):
    global_budget = wiki.insert(_page("budget", "# Budget"))
    global_overview = wiki.insert(_page("overview", "Global plan: see [[budget]] first."))
    wksp_budget = wiki.insert(_page("budget", "# W Budget", "workspace", WORKSPACE))
    wksp_overview = wiki.insert(
        _page("wksp-overview", "W plan: see [[budget]] first.", "workspace", WORKSPACE)
    )

    await wiki_service.delete_page_cascade(wiki.factory(), "budget")

    assert "[[budget]]" not in global_overview.content_md
    assert wksp_overview.content_md == "W plan: see [[budget]] first.", (
        "a workspace page was rewritten to strip a link to a page that still exists"
    )
    assert global_budget in wiki.deleted
    assert wksp_budget not in wiki.deleted
    assert _slugs(wiki.deleted) == {"budget"}
    assert wksp_budget in wiki.table_rows("wiki_pages")


@pytest.mark.asyncio
async def test_edges_owned_by_another_scope_survive(wiki):
    wiki.insert(_page("budget", "# Budget"))
    wiki.insert(_page("overview", "Global: [[budget]]"))
    wiki.insert(_page("budget", "# W Budget", "workspace", WORKSPACE))
    wiki.insert(_page("wksp-overview", "W: [[budget]]", "workspace", WORKSPACE))
    wiki.insert(WikiLink(from_slug="overview", to_slug="budget"))
    wiki.insert(WikiLink(from_slug="wksp-overview", to_slug="budget"))
    wiki.insert(WikiLink(from_slug="budget", to_slug="overview"))

    await wiki_service.delete_page_cascade(wiki.factory(), "budget")

    gone = _edges(wiki.deleted)
    assert ("overview", "budget") in gone, "the rewritten page kept a stale edge"
    assert ("budget", "overview") in gone
    assert ("wksp-overview", "budget") not in gone, (
        "the workspace's own [[budget]] edge was deleted even though both endpoints survive"
    )

    deletes = _link_deletes(wiki)
    assert deletes, "no wiki_links maintenance was issued at all"
    for stmt in deletes:
        assert "to_slug" not in _sql(stmt), (
            "edges are still being deleted by target slug, which cannot tell one scope's "
            f"page from another's: {_sql(stmt)}"
        )


@pytest.mark.asyncio
async def test_a_slug_from_another_scope_refuses_to_cascade(wiki):
    """Nothing may be touched when the page is not in the scope that was asked for."""
    wksp_budget = wiki.insert(_page("budget", "# W Budget", "workspace", WORKSPACE))
    wksp_overview = wiki.insert(
        _page("wksp-overview", "W: [[budget]]", "workspace", WORKSPACE)
    )

    with pytest.raises(ValueError, match="refusing to cascade"):
        await wiki_service.delete_page_cascade(wiki.factory(), "budget")

    assert wksp_overview.content_md == "W: [[budget]]"
    assert wksp_budget not in wiki.deleted
    assert wiki.deleted == []
    assert _link_deletes(wiki) == [], (
        "the graph was rewritten for a deletion that deleted nothing"
    )


@pytest.mark.asyncio
async def test_the_workspace_scope_can_be_deleted_on_purpose(wiki):
    global_budget = wiki.insert(_page("budget", "# Budget"))
    global_overview = wiki.insert(_page("overview", "Global: [[budget]]"))
    wksp_budget = wiki.insert(_page("budget", "# W Budget", "workspace", WORKSPACE))
    wksp_overview = wiki.insert(
        _page("wksp-overview", "W: [[budget]]", "workspace", WORKSPACE)
    )

    await wiki_service.delete_page_cascade(
        wiki.factory(), "budget", scope_type="workspace", scope_id=WORKSPACE,
    )

    assert wksp_budget in wiki.deleted
    assert global_budget not in wiki.deleted
    assert "[[budget]]" not in wksp_overview.content_md
    assert global_overview.content_md == "Global: [[budget]]"


@pytest.mark.asyncio
async def test_a_piped_wikilink_keeps_its_display_text(wiki):
    wiki.insert(_page("budget", "# Budget"))
    overview = wiki.insert(_page("overview", "See [[budget|the numbers]] and [[budget]]."))

    await wiki_service.delete_page_cascade(wiki.factory(), "budget")

    assert overview.content_md == "See the numbers and budget."
