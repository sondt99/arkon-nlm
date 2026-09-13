"""Raw-SQL index inventory — the one thing standing between these and a silent drop.

Lives here rather than in `alembic/env.py` because both the migrations CI step and the
tests need it, and `alembic/env.py` is not importable: the installed `alembic` package
shadows the name, and importing it would execute Alembic's context setup.
"""

# This set is NOT decorative and NOT merely greppable — it is the only thing standing
# between these indexes and a silent drop.
#
# `include_object` below excludes every reflected index with no metadata counterpart, in
# BOTH directions: autogenerate will not propose creating one, and will not propose
# dropping one. So the drift gate in ci.yml cannot see any of these disappear — including
# `uq_wiki_pages_slug_scope`, the only uniqueness guarantee on wiki_pages.slug. That gate's
# comment claimed it "would have caught migration 018 dropping four live indexes". It would
# not have: dropping them one at a time and re-running the exact CI command passes every
# time. 41 of the 110 live indexes sit in that blind spot.
#
# The check that DOES catch it is a positive assertion that each of these exists after
# `alembic upgrade head` — see `assert_raw_sql_indexes_exist` below, run as its own CI step
# on the migrations job. Add a raw-SQL index, add its name here.
RAW_SQL_INDEXES = {
    # created in 006_wiki_pivot.py
    "ix_wiki_pages_fulltext",
    "ix_wiki_pages_kt_slugs",
    "ix_wiki_pages_source_ids",
    # created in 010_workspace_wiki_scope.py, restored in 028
    "uq_wiki_pages_slug_scope",
    # created in 034 (see its docstring section 2)
    "ix_sources_created_at",
    # created per-dimension in 015_multi_dim_embeddings.py (HNSW + model)
    # names are ix_wiki_page_embeddings_<dim>_{hnsw,model}, so they are matched by
    # prefix rather than listed — see RAW_SQL_INDEX_PREFIXES.
}

#: Families whose member names depend on runtime configuration (embedding dimension), so
#: at least one index matching each prefix must exist rather than an exact name.
RAW_SQL_INDEX_PREFIXES = {
    "ix_wiki_page_embeddings_": "per-dimension HNSW / model indexes from migration 015",
}


def missing_raw_sql_indexes(present: set[str]) -> list[str]:
    """Which raw-SQL indexes are absent from `present`.

    Takes index NAMES rather than a connection so it is driver-agnostic and unit testable
    — CI collects them with `psql`, which the migrations job already uses.

    Autogenerate is blind to these by construction (see the note above), so this is the
    only check that notices one being dropped.
    """
    missing = sorted(RAW_SQL_INDEXES - present)
    for prefix, description in RAW_SQL_INDEX_PREFIXES.items():
        if not any(name.startswith(prefix) for name in present):
            missing.append(f"{prefix}* ({description})")
    return missing
