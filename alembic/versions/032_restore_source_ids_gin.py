"""Restore ix_wiki_pages_source_ids, now that the query can actually use it.

Migration 028 restored three of the four indexes that 018 dropped but deliberately left
this one out, because every query against wiki_pages.source_ids used `= ANY(col)` — and
Postgres cannot use a GIN array index for that form. Only `@>`, `<@`, and `&&` qualify, so
restoring the index would have been dead weight.

Those call sites now use `.contains([...])` (`@>`) and `.overlap([...])` (`&&`), so the
index is finally load-bearing:

  app/routers/sources.py   _wiki_page_count, _wiki_page_counts, knowledge-impact
  app/services/wiki_service.py  detach_source_from_wiki

Revision ID: 032
Revises: 031
"""

from typing import Sequence, Union

from alembic import op

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_wiki_pages_source_ids "
        "ON wiki_pages USING GIN (source_ids)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_wiki_pages_source_ids")
