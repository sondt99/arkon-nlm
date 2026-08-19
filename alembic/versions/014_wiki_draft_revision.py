"""Add wiki_page_drafts, wiki_page_revisions; remove old contribution columns.

The four columns 013 added held pending user contributions and their authorship. This
revision replaces them with wiki_page_drafts but originally dropped them without moving
the rows, so every install that had run 013 in production lost all pending contributions
the moment entrypoint.sh ran `alembic upgrade head` (issue #41).

The backfill added below does NOT recover anything on a database that is already past
this revision — Alembic will not re-run an applied revision, and the source columns are
gone there. It protects only the two cases still ahead of it: an environment still at or
below 013, and a fresh restore of a pre-014 dump.

Revision ID: 014
Revises: 013
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

# `~ '[^[:space:]]'` rather than `IS NOT NULL`: 013 let every column be NULL
# independently, so a page could carry a note or a timestamp with no body. content_md is
# NOT NULL on the draft table, and a draft with nothing in it is a review-queue entry an
# editor can neither approve nor understand — worse than no row at all.
_BACKFILL_DRAFTS = """
INSERT INTO wiki_page_drafts (
    id, page_id, author_id, content_md, note, status, source, source_metadata,
    created_at, updated_at
)
SELECT
    gen_random_uuid(),
    p.id,
    p.contributed_by_id,
    p.user_contribution_md,
    p.contribution_note,
    'pending',
    'migrated_013_wiki_pages',
    jsonb_build_object(
        'migrated_by_revision', '014',
        'origin', 'wiki_pages.user_contribution_md',
        'original_contributed_at', p.contributed_at
    ),
    COALESCE(p.contributed_at, p.updated_at),
    COALESCE(p.contributed_at, p.updated_at)
FROM wiki_pages p
WHERE p.user_contribution_md ~ '[^[:space:]]'
  AND NOT EXISTS (
      SELECT 1 FROM wiki_page_drafts d
      WHERE d.page_id = p.id
        AND d.content_md = p.user_contribution_md
  )
"""

# 013's schema holds at most one contribution per page, so anything beyond the newest
# pending draft cannot be represented after the downgrade. The loss belongs to the target
# schema, not to this statement — which is why it is best-effort rather than refused.
_RESTORE_CONTRIBUTION_COLUMNS = """
UPDATE wiki_pages p
SET user_contribution_md = d.content_md,
    contributed_by_id    = d.author_id,
    contributed_at       = d.created_at,
    contribution_note    = d.note
FROM (
    SELECT DISTINCT ON (page_id)
        page_id, content_md, author_id, created_at, note
    FROM wiki_page_drafts
    WHERE status = 'pending'
    ORDER BY page_id, created_at DESC, id
) d
WHERE p.id = d.page_id
"""


def _has_column(table: str, column: str) -> bool:
    """Whether `table.column` exists right now.

    Gates the backfill; the drops use IF EXISTS for the same reason. A hand-repaired
    database that already lost the 013 columns must still be able to reach head: a
    migration that aborts on a fresh or already-patched install is a worse failure than
    the one this revision is fixing.
    """
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    # 1. Add orphaned flag
    op.add_column(
        "wiki_pages",
        sa.Column("orphaned", sa.Boolean, nullable=False, server_default="false"),
    )

    # 2. Draft table — pending contributions awaiting editor review
    op.create_table(
        "wiki_page_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content_md", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(40), nullable=False, server_default="web_ui"),
        sa.Column("source_metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "reviewed_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wiki_drafts_page_id", "wiki_page_drafts", ["page_id"])
    op.create_index("ix_wiki_drafts_status", "wiki_page_drafts", ["status"])
    op.create_index("ix_wiki_drafts_author_id", "wiki_page_drafts", ["author_id"])

    # 3. Revision history — full snapshot on every content change
    op.create_table(
        "wiki_page_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("content_md", sa.Text, nullable=False),
        sa.Column("change_type", sa.String(30), nullable=False),
        # agent_compile | agent_retry | editor_edit | draft_approved | manual_rebuild | rollback
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wiki_page_drafts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "changed_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("change_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wiki_revisions_page_id", "wiki_page_revisions", ["page_id"])
    op.create_index("ix_wiki_revisions_page_version", "wiki_page_revisions", ["page_id", "version"])

    # 4. Move the 013 contributions across while the columns still exist. This has to run
    # after step 2 and before step 5, which is why the drops moved to the end of upgrade().
    if _has_column("wiki_pages", "user_contribution_md"):
        # contributed_at is the authored timestamp and is preserved as created_at.
        # updated_at is the fallback rather than now(): it is the last moment the row
        # could have been written, so it keeps migrated drafts in roughly their original
        # order instead of stamping every one of them with the deploy time.
        op.execute(_BACKFILL_DRAFTS)

    # 5. Retire the 013 single-column contribution approach. IF EXISTS rather than
    # op.drop_column, so the guard on step 4 is not undone by an unguarded drop.
    op.execute("ALTER TABLE wiki_pages DROP COLUMN IF EXISTS user_contribution_md")
    op.execute("ALTER TABLE wiki_pages DROP COLUMN IF EXISTS contributed_by_id")
    op.execute("ALTER TABLE wiki_pages DROP COLUMN IF EXISTS contributed_at")
    op.execute("ALTER TABLE wiki_pages DROP COLUMN IF EXISTS contribution_note")


def downgrade() -> None:
    op.drop_index("ix_wiki_revisions_page_version", table_name="wiki_page_revisions")
    op.drop_index("ix_wiki_revisions_page_id", table_name="wiki_page_revisions")
    op.drop_table("wiki_page_revisions")

    op.add_column("wiki_pages", sa.Column("contribution_note", sa.Text, nullable=True))
    op.add_column("wiki_pages", sa.Column("contributed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "wiki_pages",
        sa.Column(
            "contributed_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("wiki_pages", sa.Column("user_contribution_md", sa.Text, nullable=True))

    # Columns first, then the copy back, then the table — the reverse of upgrade(). The
    # original downgrade re-added the four columns empty and dropped the drafts with them.
    op.execute(_RESTORE_CONTRIBUTION_COLUMNS)

    op.drop_index("ix_wiki_drafts_author_id", table_name="wiki_page_drafts")
    op.drop_index("ix_wiki_drafts_status", table_name="wiki_page_drafts")
    op.drop_index("ix_wiki_drafts_page_id", table_name="wiki_page_drafts")
    op.drop_table("wiki_page_drafts")

    op.drop_column("wiki_pages", "orphaned")
