"""Constrain the free-text status columns, index the sources listing, fix two defaults.

The medium-severity half of issue #85. Four schema-level changes, each of which fixes a
failure that is silent today:

  1. CHECK constraints on six status/scope columns. They were String(n) free text compared
     against bare literals in roughly thirty places. A typo on any write — "erorr", or the
     `plan_review`/`plan_ready` confusion that the docs and the code disagree about —
     produced a row that no listing filter matched, so the source disappeared from the UI
     with nothing logged anywhere. `Skill.status` already used a PgEnum, so the pattern
     existed; the other six never got it.

  2. ix_sources_created_at, plus trigram GIN indexes on sources.title / file_name.
     Migration 029 added (status, created_at), which serves GET /sources?status=... but
     cannot supply the ordering for the unfiltered listing — the leading column is not in
     the query, so Postgres falls back to a full scan plus a sort of the whole table on
     every page. ?search=... is `ILIKE '%x%'`, which no B-tree can serve at all.

  3. wiki_pages.provenance_complete: repair the rows that the ORM/DDL default mismatch
     mislabelled. models.py said `default=True`, migration 023 said `server_default=false`,
     so the same page got opposite values depending on who inserted it, and
     wiki_service.detach_source_from_wiki branches on this exact flag to choose between
     `session.delete(page)` and a non-destructive detach.

  4. chat_messages.sources: json → jsonb. Migration 022 created `sa.JSON` while models.py
     declared JSONB. asyncpg cast between them silently, so nothing broke, but the column
     could never be indexed for containment and `--autogenerate` had a standing
     alter_column to propose on every run. A diff that is never empty is how 018's four
     destructive drop_index calls got approved.

Nothing here drops a column, so there is no DROP_DISPOSITIONS entry to add. The one
statement that changes user-visible data is the provenance_complete repair, and the set of
rows it touches is archived to app_config first (see below) because the predicate cannot
be re-derived afterwards: a row that was already false is indistinguishable from one this
migration flipped.

`entrypoint.sh` runs `alembic upgrade head` unattended on container start, so every step
below is written not to abort:

  * Constraints go on NOT VALID and are then validated inside a plpgsql block that traps
    check_violation. A database holding a legacy bad value ends up with the constraint
    still enforcing every future write, plus a WARNING naming the offending values, rather
    than a crash-looping container.
  * pg_trgm has been a *trusted* extension since Postgres 13, so a plain database owner can
    create it — but a locked-down managed instance can still refuse. That CREATE EXTENSION
    is trapped too, and the two trigram indexes are created only if the extension is
    actually present. Losing them costs performance, not correctness.
  * Every CREATE INDEX uses IF NOT EXISTS, and every ADD CONSTRAINT checks pg_constraint
    first, so an operator who applied part of this by hand is not punished for it.

Deliberately NOT done here:

  * wiki_pages.scope_type and chat_conversations.scope_type hold the same two-value
    vocabulary and have the same lack of a constraint. They are outside the six columns
    issue #85 enumerates, and both are written only through helpers that normalise the
    value (wiki_service.apply_create's default, chat.py's _validated_chat_scope), so the
    exposure is smaller. tests/test_migration_backfill.py::
    test_unconstrained_status_columns_are_a_known_list keeps the gap visible instead of
    letting it be forgotten.
  * compare_type stays off in env.py. Item 4 above removes one of the ~20 type drifts it
    would surface; the other ~19 (mostly timestamp tz) are untouched, so turning it on
    would still flood the diff.

Revision ID: 034
Revises: 033
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# 1. Status / scope vocabularies
# ---------------------------------------------------------------------------
# Spelled out rather than imported from app.database.models. A revision has to keep
# applying to the database it was written for; importing the live models would make this
# file's behaviour change every time somebody edits a tuple in models.py, which is the
# opposite of what a migration is for. The two lists are kept honest by
# tests/test_migration_backfill.py::test_check_constraints_match_the_model_vocabularies.
#
# (constraint name, table, column, allowed values)
_CHECKS: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "ck_sources_status", "sources", "status",
        ("pending", "processing", "plan_ready", "ready", "error"),
    ),
    (
        "ck_sources_scope_type", "sources", "scope_type",
        ("global", "project"),
    ),
    (
        "ck_source_compilation_plans_status", "source_compilation_plans", "status",
        ("pending_review", "approved", "in_progress", "done", "rejected"),
    ),
    (
        "ck_wiki_page_drafts_status", "wiki_page_drafts", "status",
        ("pending", "approved", "rejected"),
    ),
    (
        "ck_embedding_jobs_status", "embedding_jobs", "status",
        ("pending", "running", "completed", "failed", "cancelled"),
    ),
    (
        "ck_notebooklm_artifacts_status", "notebooklm_artifacts", "status",
        ("pending", "processing", "completed", "failed"),
    ),
]


def _value_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _add_check_not_valid(name: str, table: str, column: str, values: tuple[str, ...]) -> str:
    """ADD CONSTRAINT ... NOT VALID, skipped if a constraint of that name already exists.

    NOT VALID is not a weaker constraint for anything written from now on — Postgres
    enforces it on every INSERT and UPDATE. It only declines to scan the rows that are
    already there, which is what keeps this statement from aborting on a database holding a
    legacy value. `ADD CONSTRAINT` has no IF NOT EXISTS, hence the pg_constraint probe.

    The inner DDL is dollar-quoted ($ddl$). The value list is full of single-quoted string
    literals, and nesting those inside an ordinary single-quoted EXECUTE argument would mean
    doubling every one of them by hand — exactly the kind of quoting that looks right and
    silently truncates the IN list.
    """
    return f"""
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = '{name}'
    ) THEN
        EXECUTE $ddl$
            ALTER TABLE {table} ADD CONSTRAINT {name}
            CHECK ({column} IN ({_value_list(values)})) NOT VALID
        $ddl$;
    END IF;
END $do$;
"""


def _offending_values_query(table: str, column: str, values: tuple[str, ...]) -> str:
    """Distinct values already in the column that the constraint would reject."""
    return f"""
SELECT DISTINCT coalesce({column}, '(null)') AS bad
FROM {table}
WHERE {column} IS NULL OR {column} NOT IN ({_value_list(values)})
ORDER BY bad
LIMIT 20
"""


def _validate_check(name: str, table: str, column: str, values: tuple[str, ...]) -> None:
    """Promote the constraint to VALID, or log the offending values and leave it NOT VALID.

    Deliberately look before leaping instead of trapping check_violation in plpgsql. Both
    keep `alembic upgrade head` from aborting, but a plpgsql RAISE WARNING travels back as a
    Postgres notice, and neither asyncpg nor Alembic surfaces notices — the operator would
    get a silently unvalidated constraint and no way to know. Logging from Python puts the
    message on the same console handler as Alembic's own "Running upgrade" lines.

    There is no race: the constraint is already in place NOT VALID by the time this runs, so
    no concurrent session can add a violating row between the SELECT and the VALIDATE.
    """
    bind = op.get_bind()
    offending = [
        row.bad for row in bind.execute(sa.text(_offending_values_query(table, column, values)))
    ]
    if not offending:
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
        return
    logging.getLogger("alembic.runtime.migration").warning(
        "%s left NOT VALID: %s.%s already holds %s. Every new INSERT and UPDATE is "
        "constrained; the existing rows are not. Those rows are invisible to the status "
        "filters in the UI — correct them, then run: "
        "ALTER TABLE %s VALIDATE CONSTRAINT %s;",
        name, table, column, ", ".join(repr(v) for v in offending), table, name,
    )


# ---------------------------------------------------------------------------
# 2. sources listing indexes
# ---------------------------------------------------------------------------
# ORDER BY created_at DESC with no status filter is what GET /sources issues for the first
# page of the document list. Plain ASC: Postgres walks a B-tree backwards for the same
# cost, so this serves DESC ordering as an Index Scan Backward.
_CREATED_AT_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_sources_created_at ON sources (created_at)"
)

# CREATE EXTENSION is a utility statement, so plpgsql has to reach it through EXECUTE.
# undefined_file is what Postgres raises when the contrib package is not installed on the
# host at all; insufficient_privilege is a managed instance refusing.
_CREATE_TRGM = """
DO $do$
BEGIN
    EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_trgm';
EXCEPTION WHEN insufficient_privilege OR undefined_file THEN
    RAISE WARNING
        'pg_trgm could not be created (%). GET /sources?search=... will keep sequentially '
        'scanning sources. Install the extension, then create ix_sources_title_trgm and '
        'ix_sources_file_name_trgm from migration 034 by hand.', SQLERRM;
END $do$;
"""

# GIN + gin_trgm_ops is the only index shape that can serve a leading-wildcard ILIKE.
# Guarded on the extension actually being present so the previous block's fallback is a
# real fallback and not just a deferred crash.
_TRGM_INDEXES = """
DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS ix_sources_title_trgm '
                'ON sources USING GIN (title gin_trgm_ops)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS ix_sources_file_name_trgm '
                'ON sources USING GIN (file_name gin_trgm_ops)';
    END IF;
END $do$;
"""


# ---------------------------------------------------------------------------
# 3. provenance_complete repair
# ---------------------------------------------------------------------------
# A page with no wiki_page_contributions rows cannot, by the column's own definition, be
# rebuilt from source contributions. Every code path that genuinely establishes provenance
# writes True explicitly next to upsert_source_contribution (app/ai/mrp/pipeline.py,
# wiki_service.rebuild_page_from_contributions), so the only rows this predicate can match
# are ones that inherited the wrong ORM default: `_index`, `_log`, chat "save to wiki", and
# MCP-created pages. For those, detach_source_from_wiki took the
# `if page.provenance_complete: ... session.delete(page)` branch and destroyed the page.
_ARCHIVE_KEY = "archived_034_provenance_complete_repair"

_ARCHIVE_PROVENANCE_REPAIR = f"""
INSERT INTO app_config (key, value, updated_at)
SELECT
    '{_ARCHIVE_KEY}',
    jsonb_agg(jsonb_build_object('id', p.id, 'slug', p.slug) ORDER BY p.slug)::text,
    now()
FROM wiki_pages p
WHERE p.provenance_complete
  AND NOT EXISTS (
    SELECT 1 FROM wiki_page_contributions c WHERE c.page_id = p.id
  )
HAVING count(*) > 0
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, updated_at = now()
"""

_REPAIR_PROVENANCE = """
UPDATE wiki_pages p
SET provenance_complete = false
WHERE p.provenance_complete
  AND NOT EXISTS (
    SELECT 1 FROM wiki_page_contributions c WHERE c.page_id = p.id
  )
"""

# Restores exactly the rows the upgrade flipped, by id, from the archive. Not derivable
# from a predicate: after the UPDATE, a repaired row looks identical to one that was false
# to begin with.
_UNREPAIR_PROVENANCE = f"""
UPDATE wiki_pages p
SET provenance_complete = true
WHERE p.id IN (
    SELECT (elem ->> 'id')::uuid
    FROM app_config a,
         LATERAL jsonb_array_elements(a.value::jsonb) AS elem
    WHERE a.key = '{_ARCHIVE_KEY}'
)
"""


# ---------------------------------------------------------------------------
# 4. chat_messages.sources json -> jsonb
# ---------------------------------------------------------------------------
# Guarded on the current type so this is a no-op against a database built by
# Base.metadata.create_all(), which already produces jsonb. json -> jsonb is not a
# binary-coercible cast, so an unguarded ALTER would rewrite the table for nothing.
def _alter_json_type(table: str, column: str, target: str) -> str:
    return f"""
DO $do$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = '{table}'
          AND column_name = '{column}'
          AND data_type <> '{target}'
    ) THEN
        EXECUTE $ddl$
            ALTER TABLE {table} ALTER COLUMN {column}
            TYPE {target} USING {column}::{target}
        $ddl$;
    END IF;
END $do$;
"""


def upgrade() -> None:
    # --- 1. Constrain the vocabularies ----------------------------------------------
    for name, table, column, values in _CHECKS:
        op.execute(_add_check_not_valid(name, table, column, values))
        _validate_check(name, table, column, values)

    # --- 2. Index the sources listing -----------------------------------------------
    op.execute(_CREATED_AT_INDEX)
    op.execute(_CREATE_TRGM)
    op.execute(_TRGM_INDEXES)

    # --- 3. Repair provenance_complete ----------------------------------------------
    # Archive before the UPDATE, in that order, for the same reason 014 and 018 backfill
    # before they drop: afterwards there is nothing left to read.
    op.execute(_ARCHIVE_PROVENANCE_REPAIR)
    op.execute(_REPAIR_PROVENANCE)
    # The column comment exists in models.py but was never in any migration, so
    # --autogenerate proposed adding it on every single run. Item 4's whole point is that a
    # permanently non-empty diff is what let 018's four destructive drop_index calls slip
    # through review; leaving a known drift behind while fixing another one would be odd.
    op.execute(
        "COMMENT ON COLUMN wiki_pages.provenance_complete IS "
        "'True when page content can be rebuilt from source contributions'"
    )

    # --- 4. json -> jsonb ------------------------------------------------------------
    op.execute(_alter_json_type("chat_messages", "sources", "jsonb"))


def downgrade() -> None:
    """Fully reversible, including the data step — which is why the archive exists.

    The one thing that does not come back is any WARNING this migration raised about rows
    that failed validation; those rows were never modified, so there is nothing to undo.
    """
    op.execute(_alter_json_type("chat_messages", "sources", "json"))

    op.execute(_UNREPAIR_PROVENANCE)
    op.execute(f"DELETE FROM app_config WHERE key = '{_ARCHIVE_KEY}'")

    op.execute("DROP INDEX IF EXISTS ix_sources_file_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_sources_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_sources_created_at")
    # pg_trgm is deliberately left installed. Dropping an extension another migration,
    # another application, or an operator may have come to depend on is not a rollback of
    # anything this revision did.

    for name, table, _column, _values in reversed(_CHECKS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
