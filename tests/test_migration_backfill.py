"""Guard the data migrations that 014 and 018 were missing, and lock the drop inventory.

Issue #41: 014 dropped the four wiki-contribution columns 013 had added without moving the
rows into the replacement wiki_page_drafts table, and 018 dropped skills.description and
skill_contributions.description outright. entrypoint.sh runs `alembic upgrade head`
unattended on container start, so both losses landed before an operator could intervene.

Neither fix is retroactive — Alembic never re-runs an applied revision, so a database
already past 014/018 has nothing left to recover. These tests protect the cases that are
still ahead of those revisions: an environment at or below 013/017, and fresh restores of
pre-014 dumps.

What is asserted here is structural, because the suite has no database. The end-to-end
proof is the one the issue asks for and has to be run by hand against a scratch Postgres:
seed at 013, `alembic upgrade head`, and read wiki_page_drafts back.
"""

import importlib.util
import inspect
import pathlib
import re

import app.database.models as models
from app.database.models import AppConfig, WikiPageDraft
from app.services.config_service import ALL_CONFIG_KEYS, _is_sensitive

VERSIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

# `drop_column("t", "c")` and `ALTER TABLE t DROP COLUMN [IF EXISTS] c`.
_DROP_COLUMN = re.compile(
    r"""drop_column\(\s*['"](?P<t1>\w+)['"]\s*,\s*['"](?P<c1>\w+)['"]"""
    r"""|ALTER\s+TABLE\s+(?P<t2>\w+)\s+DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?(?P<c2>\w+)""",
    re.IGNORECASE,
)

_ADD_COLUMN = re.compile(
    r"""add_column\(\s*['"](?P<table>\w+)['"]\s*,\s*sa\.Column\(\s*['"](?P<column>\w+)['"]"""
)


def _load_path(path: pathlib.Path):
    """Import a migration module from its file.

    Migration filenames are not importable module paths and the package has no __init__,
    so this goes through importlib rather than a plain import.
    """
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(stem_prefix: str):
    """Import the one migration whose filename starts with `stem_prefix`.

    Two revisions share the 002 prefix, so callers of this helper must use a prefix that
    resolves uniquely; the inventory scan below goes by path instead.
    """
    matches = sorted(VERSIONS_DIR.glob(f"{stem_prefix}_*.py"))
    assert len(matches) == 1, f"expected one migration for {stem_prefix}, got {matches}"
    return _load_path(matches[0])


def _dropped_columns(module) -> set[tuple[str, str]]:
    """(table, column) pairs the module's upgrade() drops.

    Scoped to upgrade() on purpose: a drop in downgrade() is a deliberate rollback of
    something the same revision added, not an unplanned loss on the forward path.
    """
    src = inspect.getsource(module.upgrade)
    return {
        (m.group("t1") or m.group("t2"), m.group("c1") or m.group("c2"))
        for m in _DROP_COLUMN.finditer(src)
    }


M013 = _load("013")
M014 = _load("014")
M018 = _load("018")


# ---------------------------------------------------------------------------
# 014 — wiki contributions into wiki_page_drafts
# ---------------------------------------------------------------------------

def test_backfill_reads_every_column_013_added():
    """Derived from 013 rather than hardcoded, so a fifth column could not slip past.

    This is the assertion the original 014 failed: all four columns were dropped and none
    of them was read first.
    """
    added = {
        m.group("column")
        for m in _ADD_COLUMN.finditer(inspect.getsource(M013.upgrade))
        if m.group("table") == "wiki_pages"
    }
    assert added, "regex no longer matches 013's add_column calls — the check is vacuous"

    unread = {c for c in added if c not in M014._BACKFILL_DRAFTS}
    assert not unread, (
        f"014 drops {sorted(added)} but the backfill never reads {sorted(unread)} — "
        "those rows are destroyed on upgrade"
    )


def test_backfill_runs_before_the_drops():
    """Ordering is the whole fix. Reversed, the backfill selects from dropped columns."""
    src = inspect.getsource(M014.upgrade)
    assert src.index("_BACKFILL_DRAFTS") < src.index("DROP COLUMN IF EXISTS")


def test_backfill_and_drops_are_both_guarded():
    """A migration that aborts on a fresh or hand-repaired install is worse than the bug.

    Guarding only the backfill would not help: an unguarded op.drop_column raises
    UndefinedColumn on the same databases the guard exists for.
    """
    src = inspect.getsource(M014.upgrade)
    assert "_has_column(" in src, "the backfill no longer checks the column exists"
    # Anchored to the start of a statement so the surrounding comments do not match.
    assert not re.search(r"^\s*op\.drop_column\(", src, re.MULTILINE), (
        "an unguarded drop defeats the guard on the backfill"
    )
    assert src.count("DROP COLUMN IF EXISTS") == 4


def test_backfill_skips_contributions_with_no_body():
    """content_md is NOT NULL, and 013 let the four columns be NULL independently.

    A page carrying only a note or only a timestamp must not become an empty draft that an
    editor can neither read nor approve.
    """
    assert "~ '[^[:space:]]'" in M014._BACKFILL_DRAFTS


def test_migrated_drafts_land_in_the_review_queue():
    """'pending' is what the reviewer UI lists. Any other status hides the rows."""
    assert "'pending'" in M014._BACKFILL_DRAFTS
    assert WikiPageDraft.__table__.c.status.default.arg == "pending"


def test_backfill_preserves_authorship():
    """Attribution was half the loss: an anonymous draft cannot be reviewed with the author."""
    assert "p.contributed_by_id" in M014._BACKFILL_DRAFTS
    assert "COALESCE(p.contributed_at" in M014._BACKFILL_DRAFTS


def test_backfill_targets_columns_that_exist_on_the_model():
    """Catches the migration drifting away from a renamed WikiPageDraft field."""
    inserted = _inserted_columns(M014._BACKFILL_DRAFTS, "wiki_page_drafts")
    model_columns = set(WikiPageDraft.__table__.columns.keys())

    assert inserted, "could not parse the INSERT column list"
    assert inserted <= model_columns, (
        f"backfill writes columns wiki_page_drafts does not have: "
        f"{sorted(inserted - model_columns)}"
    )

    # Every column the table requires and cannot default must be supplied explicitly.
    required = {
        c.name
        for c in WikiPageDraft.__table__.columns
        if not c.nullable and c.server_default is None and c.default is None
    }
    assert required <= inserted, f"backfill omits required columns: {sorted(required - inserted)}"


def test_backfill_literals_fit_their_columns():
    """A 41-character source value would abort the migration on a populated database."""
    for column, literal in (("status", "pending"), ("source", "migrated_013_wiki_pages")):
        limit = WikiPageDraft.__table__.c[column].type.length
        assert f"'{literal}'" in M014._BACKFILL_DRAFTS
        assert len(literal) <= limit, f"{column} literal {literal!r} exceeds {limit} chars"


def test_downgrade_copies_drafts_back_before_dropping_the_table():
    """The original downgrade handed back four empty columns and called it reversible."""
    src = inspect.getsource(M014.downgrade)
    assert src.index("_RESTORE_CONTRIBUTION_COLUMNS") < src.index('drop_table("wiki_page_drafts")')
    assert src.index("user_contribution_md") < src.index("_RESTORE_CONTRIBUTION_COLUMNS"), (
        "the columns must be re-added before the copy back can write to them"
    )


# ---------------------------------------------------------------------------
# 018 — skill descriptions into app_config
# ---------------------------------------------------------------------------

def test_both_description_columns_are_archived_before_being_dropped():
    src = inspect.getsource(M018.upgrade)
    first_drop = src.index("DROP COLUMN IF EXISTS description")
    for name in ("_ARCHIVE_SKILLS_DESCRIPTION", "_ARCHIVE_CONTRIBUTIONS_DESCRIPTION"):
        assert src.index(name) < first_drop, f"{name} runs after the column is gone"
    assert "op.drop_column('skills', 'description')" not in src
    assert "op.drop_column('skill_contributions', 'description')" not in src


def test_archives_are_guarded_and_re_runnable():
    src = inspect.getsource(M018.upgrade)
    assert src.count("_has_column(") == 2
    for sql in (M018._ARCHIVE_SKILLS_DESCRIPTION, M018._ARCHIVE_CONTRIBUTIONS_DESCRIPTION):
        assert "ON CONFLICT (key) DO UPDATE" in sql, "a re-run would raise on the primary key"


def test_archives_write_nothing_on_a_fresh_install():
    """Without the HAVING an empty install gains two app_config rows holding `null`."""
    for sql in (M018._ARCHIVE_SKILLS_DESCRIPTION, M018._ARCHIVE_CONTRIBUTIONS_DESCRIPTION):
        assert "HAVING count(*) > 0" in sql
        assert "~ '[^[:space:]]'" in sql


def test_archives_are_restorable_by_primary_key():
    """A blob of loose description text is not a recovery path; ids make it one."""
    for sql in (M018._ARCHIVE_SKILLS_DESCRIPTION, M018._ARCHIVE_CONTRIBUTIONS_DESCRIPTION):
        assert "'id'," in sql
        assert "'description'," in sql


def test_archive_target_columns_exist():
    inserted = _inserted_columns(M018._ARCHIVE_SKILLS_DESCRIPTION, "app_config")
    assert inserted and inserted <= set(AppConfig.__table__.columns.keys())


def test_archive_keys_are_inert_config_rows():
    """app_config is shared with the admin config UI, which must not see these.

    ConfigService.get_all() and update_many() iterate ALL_CONFIG_KEYS, so a key outside it
    can neither be displayed nor overwritten. _is_sensitive would additionally try to
    Fernet-decrypt the value on read.
    """
    keys = set(re.findall(r"'(archived_018_[a-z_]+)'", inspect.getsource(M018)))
    assert len(keys) == 2, f"expected two archive keys, found {sorted(keys)}"
    for key in keys:
        assert key not in ALL_CONFIG_KEYS
        assert not _is_sensitive(key)
        assert len(key) <= AppConfig.__table__.c.key.type.length


def test_scope_type_add_cannot_abort_on_a_populated_table():
    """NOT NULL with no server_default aborts wherever skill_contributions has rows.

    That is precisely the population with descriptions to lose, so the archive above was
    unreachable on every install it was written for. 025 still supplies the default.
    """
    src = inspect.getsource(M018.upgrade)
    add = src[src.index("'scope_type'"):]
    add = add[:add.index("scope_ids")]
    assert "nullable=True" in add
    assert "UPDATE skill_contributions SET scope_type = 'global'" in add
    assert "nullable=False" in add, "the column must still end up NOT NULL"


# ---------------------------------------------------------------------------
# Inventory lock — the durable half of the fix
# ---------------------------------------------------------------------------

# Every (table, column) an upgrade() drops, with what happens to the data. A new entry has
# to be added deliberately, which is the review gate issue #41 asks for: "treat *drop
# populated column* as requiring an accompanying data migration (or an explicit archive)
# before review approval".
DROP_DISPOSITIONS = {
    # revision: {(table, column): why the data is not lost}
    "002_rbac": {
        ("sources", "knowledge_type"): (
            "Legacy free-text column superseded by knowledge_type_id. Dropped with no "
            "migration — pre-002 values are gone. Recorded here rather than fixed: the "
            "revision predates any deployment this repo can still reach."
        ),
    },
    "006": {
        ("notes", "embedding"): "Derived vector, regenerable from content.",
    },
    "011_permission_v2": {
        ("sources", "department_id"): "Copied into source_departments first.",
    },
    "014": {
        ("wiki_pages", "user_contribution_md"): "Backfilled into wiki_page_drafts.",
        ("wiki_pages", "contributed_by_id"): "Backfilled into wiki_page_drafts.author_id.",
        ("wiki_pages", "contributed_at"): "Backfilled into wiki_page_drafts.created_at.",
        ("wiki_pages", "contribution_note"): "Backfilled into wiki_page_drafts.note.",
    },
    "015": {
        ("wiki_pages", "embedding"): (
            "Derived vector. Superseded by the per-dimension wiki_page_embeddings_<dim> "
            "tables and regenerable by reembed_all_pages_task."
        ),
    },
    "018": {
        ("audit_log", "scope_type"): (
            "Dropped with no archive. Loses the scope of every pre-018 audit entry; the "
            "surviving resource_type/resource_id still identify the object. Not fixed "
            "here — flagged so it is a known gap rather than an invisible one."
        ),
        ("audit_log", "scope_id"): "See audit_log.scope_type above.",
        ("skills", "description"): "Archived to app_config.archived_018_skills_description.",
        ("skill_contributions", "description"): (
            "Archived to app_config.archived_018_skill_contributions_description."
        ),
    },
    "031": {
        ("employees", "mcp_token"): "Hashed into mcp_token_hash before the drop.",
    },
}


def _all_upgrade_drops() -> dict[str, set[tuple[str, str]]]:
    found = {}
    for path in sorted(VERSIONS_DIR.glob("[0-9][0-9][0-9]_*.py")):
        module = _load_path(path)
        drops = _dropped_columns(module)
        if drops:
            found[module.revision] = drops
    return found


def test_every_dropped_column_has_a_recorded_disposition():
    """Fails on any new column drop until its data migration is stated.

    A silent `op.drop_column` on a user-authored column is the exact shape of issue #41,
    and it will keep being introduced by autogenerate. This makes it fail in CI instead of
    in production.
    """
    undeclared = []
    for revision, drops in _all_upgrade_drops().items():
        declared = DROP_DISPOSITIONS.get(revision, {})
        for table, column in sorted(drops):
            if (table, column) not in declared:
                undeclared.append(f"{revision}: {table}.{column}")

    assert not undeclared, (
        "these upgrades drop columns with no recorded disposition — add a data migration "
        "or an explicit archive, then document it in DROP_DISPOSITIONS:\n  "
        + "\n  ".join(undeclared)
    )


def test_the_inventory_has_no_stale_entries():
    """A disposition for a drop that no longer happens hides the next real one."""
    actual = _all_upgrade_drops()
    stale = [
        f"{revision}: {table}.{column}"
        for revision, declared in DROP_DISPOSITIONS.items()
        for (table, column) in declared
        if (table, column) not in actual.get(revision, set())
    ]
    assert not stale, f"DROP_DISPOSITIONS lists drops that no longer exist: {stale}"


def test_the_inventory_actually_scanned_the_migrations():
    """Guard against a glob or regex bug making both tests above vacuous."""
    found = _all_upgrade_drops()
    assert len(found) >= 7, f"only found column drops in {len(found)} migrations"
    assert ("wiki_pages", "user_contribution_md") in found["014"]


# ---------------------------------------------------------------------------
# Revision chain
# ---------------------------------------------------------------------------

def test_revision_ids_are_unchanged():
    """Renumbering either revision orphans every existing install's alembic_version."""
    assert (M013.revision, M013.down_revision) == ("013", "012")
    assert (M014.revision, M014.down_revision) == ("014", "013")
    assert (M018.revision, M018.down_revision) == ("018", "017")


def _inserted_columns(sql: str, table: str) -> set[str]:
    """Column names from `INSERT INTO <table> ( ... )`."""
    match = re.search(
        rf"INSERT\s+INTO\s+{table}\s*\((?P<cols>[^)]*)\)", sql, re.IGNORECASE
    )
    if match is None:
        return set()
    return {c.strip() for c in match.group("cols").split(",") if c.strip()}


# --------------------------------------------------------------------------- #
# Migration 034 — status vocabularies (issue #85)
#
# Six columns were free-text `String(n)` compared against bare literals in ~30 places, so a
# typo on either side produced a row that every listing filter skipped: the source vanished
# from the UI with no error logged anywhere. 034 adds CHECK constraints built from the
# vocabularies in models.py.
#
# That fix introduces a new failure mode of its own, which is what these tests guard: if
# somebody adds a status value in code without adding it to the vocabulary, the write stops
# being a silently-invisible row and becomes a production IntegrityError. The last test
# below is the ratchet that catches it in CI instead.
# --------------------------------------------------------------------------- #

_M034 = _load("034")

# Vocabulary tuple in models.py -> constraint name in 034.
_VOCAB_BY_CONSTRAINT = {
    "ck_sources_status": "SOURCE_STATUSES",
    "ck_sources_scope_type": "SCOPE_TYPES",
    "ck_source_compilation_plans_status": "COMPILATION_PLAN_STATUSES",
    "ck_wiki_page_drafts_status": "WIKI_DRAFT_STATUSES",
    "ck_embedding_jobs_status": "EMBEDDING_JOB_STATUSES",
    "ck_notebooklm_artifacts_status": "NLM_ARTIFACT_STATUSES",
}


def test_check_constraints_match_the_model_vocabularies():
    """034 spells its vocabularies out instead of importing them, deliberately — a revision
    has to keep applying to the database it was written for. This is the test that keeps the
    two copies honest, and 034's own comment names it.
    """
    checks = {name: values for name, _t, _c, values in _M034._CHECKS}
    assert set(checks) == set(_VOCAB_BY_CONSTRAINT), (
        "034._CHECKS and this test's mapping have diverged"
    )

    for constraint, attr in _VOCAB_BY_CONSTRAINT.items():
        model_values = getattr(models, attr)
        assert tuple(checks[constraint]) == tuple(model_values), (
            f"{constraint} allows {checks[constraint]} but models.{attr} is {model_values}. "
            "A value the model permits but the constraint rejects is an IntegrityError in "
            "production; the reverse is a row the UI cannot see."
        )


def test_every_constrained_column_is_actually_constrained_in_the_model():
    """A CHECK in the migration with no matching constraint on the model would be dropped by
    the next autogenerate — the mechanism that made 018 drop four live indexes.
    """
    declared = {
        c.name
        for table in models.Base.metadata.tables.values()
        for c in table.constraints
        if getattr(c, "name", None) and str(c.name).startswith("ck_")
    }
    for name, _t, _c, _v in _M034._CHECKS:
        assert name in declared, (
            f"{name} exists in migration 034 but not in models.py, so autogenerate will "
            "propose dropping it"
        )


def test_every_status_the_code_writes_is_in_its_vocabulary():
    """The ratchet. Adding a status value in code without adding it here now FAILS the write.

    Before 034 an unknown value produced a row that every filter skipped — bad, but silent.
    After 034 it is an IntegrityError. That is the better failure, but only if this test
    catches it first.
    """
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    # `x.status = "value"` and `status="value"` as a constructor kwarg.
    pattern = re.compile(r'\bstatus\s*=\s*"([a-z_]+)"')

    written: set[str] = set()
    for path in app_dir.rglob("*.py"):
        written |= set(pattern.findall(path.read_text(encoding="utf-8")))

    assert written, "the scan found no status writes at all — the pattern has rotted"

    # Every vocabulary that a CHECK enforces, plus the columns deliberately left
    # unconstrained (see test_unconstrained_status_columns_are_a_known_list).
    allowed = set()
    for attr in _VOCAB_BY_CONSTRAINT.values():
        allowed |= set(getattr(models, attr))
    # Skill.status is a PgEnum, not a CHECK, and legitimately carries these.
    allowed |= {"active", "processing", "deleting", "deprecated", "archived"}

    unknown = sorted(written - allowed)
    assert not unknown, (
        f"app/ writes status values that no vocabulary permits: {unknown}. If these belong "
        "to a constrained column the write will now raise IntegrityError — add them to the "
        "tuple in models.py AND ship a migration replacing the constraint. If they belong "
        "to an unconstrained column, add them to this test's allow-list with a comment."
    )


def test_unconstrained_status_columns_are_a_known_list():
    """Two status-ish columns are deliberately NOT constrained. Keep the gap visible.

    `wiki_pages.status` and the chat scope column are written only through helpers that
    normalise the value, so the exposure is smaller — but "smaller" is not "none", and an
    undocumented omission reads as an oversight. 034's docstring points here.
    """
    constrained = {(t, c) for _n, t, c, _v in _M034._CHECKS}
    assert ("sources", "status") in constrained
    assert ("wiki_pages", "status") not in constrained, (
        "wiki_pages.status is now constrained — remove it from this known-gap list"
    )


def test_034_creates_no_index_it_does_not_also_document():
    """Every index 034 adds must be guarded with IF NOT EXISTS.

    034 runs on databases that may already carry a hand-made index of the same name; an
    unguarded CREATE INDEX aborts the whole upgrade on those.
    """
    # Scan the code, not the docstring — the docstring says "Every CREATE INDEX uses IF NOT
    # EXISTS", which a naive negative lookahead matches as an offender.
    src = inspect.getsource(_M034)
    body = src.replace(_M034.__doc__ or "", "", 1)
    creates = re.findall(r"CREATE INDEX(?: CONCURRENTLY)?(?! IF NOT EXISTS)", body)
    assert not creates, f"{len(creates)} unguarded CREATE INDEX in 034"

    # And the scan must not be vacuous.
    assert body.count("CREATE INDEX IF NOT EXISTS") >= 3
