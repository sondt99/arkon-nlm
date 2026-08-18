"""Page-range parsing must reject unbounded expansions (issue #12)."""

import pytest

from app.services.source_outline import (
    MAX_PAGE_SPAN,
    PageRangeError,
    parse_page_range,
)


def test_parse_simple_ranges():
    assert parse_page_range("5-7") == [5, 6, 7]
    assert parse_page_range("3,8") == [3, 8]
    assert parse_page_range("12") == [12]
    assert parse_page_range("1-3,9") == [1, 2, 3, 9]


def test_huge_span_is_rejected():
    with pytest.raises(PageRangeError, match="200-page cap"):
        parse_page_range("1-40000000")


def test_span_at_cap_is_accepted():
    pages = parse_page_range(f"1-{MAX_PAGE_SPAN}")
    assert pages[0] == 1
    assert pages[-1] == MAX_PAGE_SPAN
    assert len(pages) == MAX_PAGE_SPAN
