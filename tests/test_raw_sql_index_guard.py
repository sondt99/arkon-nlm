"""The drift gate cannot see a raw-SQL index disappear.

`alembic/env.py`'s `include_object` excludes every reflected index with no metadata
counterpart — in BOTH directions. Autogenerate therefore proposes neither creating nor
dropping them, so the "Fail if models have structurally drifted" step passes after
dropping `uq_wiki_pages_slug_scope`, the only uniqueness guarantee on `wiki_pages.slug`.
41 of the 110 live indexes sit in that blind spot; that is the exact class of regression
migration 018 shipped, and the gate's comment claimed it would have been caught.

A positive existence check is the only thing that catches it. CI runs it against the real
Postgres the migrations job already starts; these tests cover the comparison logic, which
is where a silent hole would open (an empty expectation set passes everything).
"""

import pytest

from app.database.raw_sql_indexes import (
    RAW_SQL_INDEX_PREFIXES,
    RAW_SQL_INDEXES,
    missing_raw_sql_indexes,
)


def _all_present() -> set[str]:
    """A database that has everything, including one member of each prefix family."""
    present = set(RAW_SQL_INDEXES)
    for prefix in RAW_SQL_INDEX_PREFIXES:
        present.add(f"{prefix}1536_hnsw")
    return present


def test_a_complete_database_reports_nothing_missing():
    assert missing_raw_sql_indexes(_all_present()) == []


@pytest.mark.parametrize("dropped", sorted(RAW_SQL_INDEXES))
def test_dropping_any_single_index_is_detected(dropped):
    """One at a time, because that is how 018 did it."""
    present = _all_present() - {dropped}
    assert missing_raw_sql_indexes(present) == [dropped]


def test_the_uniqueness_guarantee_on_wiki_slugs_is_covered():
    """Named explicitly: it is the one whose loss is a correctness bug, not a slow query.

    Without it a global page and a workspace page may share a slug undetected, and
    `scalar_one_or_none()` lookups start raising MultipleResultsFound.
    """
    assert "uq_wiki_pages_slug_scope" in RAW_SQL_INDEXES


def test_a_missing_hnsw_family_is_detected_even_though_names_vary():
    """Per-dimension names depend on configuration, so they are matched by prefix."""
    present = {n for n in _all_present() if not n.startswith("ix_wiki_page_embeddings_")}
    missing = missing_raw_sql_indexes(present)
    assert len(missing) == 1
    assert missing[0].startswith("ix_wiki_page_embeddings_")


def test_an_empty_database_reports_everything():
    missing = missing_raw_sql_indexes(set())
    assert len(missing) == len(RAW_SQL_INDEXES) + len(RAW_SQL_INDEX_PREFIXES)


def test_the_expectation_set_is_not_empty():
    """An empty set would make the CI step pass unconditionally — a silent hole."""
    assert RAW_SQL_INDEXES
    assert RAW_SQL_INDEX_PREFIXES
