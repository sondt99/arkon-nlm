"""Wiki RBAC visibility: empty allow-lists fail closed (#11)."""

from types import SimpleNamespace
from uuid import uuid4

from app.services.wiki_service import page_is_visible


def _page(*, sources=None, kts=None):
    return SimpleNamespace(source_ids=sources or [], knowledge_type_slugs=kts or [])


def test_unrestricted_when_both_none():
    assert page_is_visible(_page(kts=["secret"]), None, None) is True


def test_empty_kt_list_denies_everything():
    page = _page(kts=["sales"], sources=[uuid4()])
    assert page_is_visible(page, [], None) is False
    assert page_is_visible(page, [], []) is False


def test_empty_page_kts_are_not_world_readable_when_restricted():
    page = _page(kts=[], sources=[])
    assert page_is_visible(page, ["sales"], None) is False


def test_source_overlap_allows_page_even_without_kts():
    sid = uuid4()
    page = _page(sources=[sid], kts=[])
    assert page_is_visible(page, ["sales"], [str(sid)]) is True


def test_foreign_source_is_hidden():
    page = _page(sources=[uuid4()], kts=["sales"])
    assert page_is_visible(page, ["sales"], [str(uuid4())]) is False


def test_untethered_page_visible_only_via_matching_kt():
    page = _page(sources=[], kts=["sales"])
    assert page_is_visible(page, ["sales"], [str(uuid4())]) is True
    assert page_is_visible(page, ["hr"], [str(uuid4())]) is False
