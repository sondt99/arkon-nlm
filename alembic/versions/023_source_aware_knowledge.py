"""Add source-aware wiki contributions.

Revision ID: 023
Revises: 022
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "wiki_pages",
        sa.Column(
            "provenance_complete", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "wiki_page_contributions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("page_id", UUID(as_uuid=True), sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_title", sa.String(500), nullable=True),
        sa.Column("knowledge_type_slug", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("page_id", "source_id", name="uq_wpc_page_source"),
    )
    op.create_index("ix_wpc_source_id", "wiki_page_contributions", ["source_id"])
    op.create_index("ix_wpc_page_id", "wiki_page_contributions", ["page_id"])

    # Existing single-source pages have unambiguous ownership and can be
    # migrated losslessly. Multi-source legacy pages stay marked incomplete.
    op.execute(sa.text("""
        INSERT INTO wiki_page_contributions
            (page_id, source_id, content_md, summary, source_title, knowledge_type_slug)
        SELECT p.id, p.source_ids[1], p.content_md, p.summary, s.title,
               CASE WHEN cardinality(p.knowledge_type_slugs) > 0
                    THEN p.knowledge_type_slugs[1] ELSE NULL END
        FROM wiki_pages p
        JOIN sources s ON s.id = p.source_ids[1]
        WHERE cardinality(p.source_ids) = 1
          AND p.slug NOT IN ('_index', '_log')
    """))
    op.execute(sa.text("""
        UPDATE wiki_pages
        SET provenance_complete = true
        WHERE cardinality(source_ids) <= 1
          AND slug NOT IN ('_index', '_log')
    """))


def downgrade() -> None:
    op.drop_table("wiki_page_contributions")
    op.drop_column("wiki_pages", "provenance_complete")
