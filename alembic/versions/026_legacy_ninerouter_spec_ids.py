"""Rewrite legacy 9Router embedding spec IDs to the canonical form.

The catalog IDs 'ninerouter/text-embedding-3-small|-large' were renamed to
'ninerouter/openai/text-embedding-3-small|-large' so every spec honors the
id == "<provider>/<model_id>" convention. Installs that had selected a
9Router model persisted the old ID in:

  - app_config.active_embedding_model_spec_id (value column)
  - embedding_jobs.model_spec_id
  - wiki_page_embeddings_<dim>.model_spec_id (1536 and 3072 are the only
    dimensions the 9Router specs use, but all four tables are swept for
    safety)

app/ai/embedding_catalog.py also keeps LEGACY_SPEC_ID_ALIASES as a runtime
fallback; this migration makes the stored state canonical.

Revision ID: 026
Revises: 025
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RENAMES = {
    "ninerouter/text-embedding-3-small": "ninerouter/openai/text-embedding-3-small",
    "ninerouter/text-embedding-3-large": "ninerouter/openai/text-embedding-3-large",
}

_EMBEDDING_TABLES = [f"wiki_page_embeddings_{d}" for d in (768, 1024, 1536, 3072)]


def upgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES.items():
        conn.execute(
            sa.text(
                "UPDATE app_config SET value = :new "
                "WHERE key = 'active_embedding_model_spec_id' AND value = :old"
            ),
            {"old": old, "new": new},
        )
        conn.execute(
            sa.text(
                "UPDATE embedding_jobs SET model_spec_id = :new "
                "WHERE model_spec_id = :old"
            ),
            {"old": old, "new": new},
        )
        for table in _EMBEDDING_TABLES:
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET model_spec_id = :new "  # noqa: S608 — table names from a fixed list
                    "WHERE model_spec_id = :old"
                ),
                {"old": old, "new": new},
            )


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES.items():
        conn.execute(
            sa.text(
                "UPDATE app_config SET value = :old "
                "WHERE key = 'active_embedding_model_spec_id' AND value = :new"
            ),
            {"old": old, "new": new},
        )
        conn.execute(
            sa.text(
                "UPDATE embedding_jobs SET model_spec_id = :old "
                "WHERE model_spec_id = :new"
            ),
            {"old": old, "new": new},
        )
        for table in _EMBEDDING_TABLES:
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET model_spec_id = :old "  # noqa: S608
                    "WHERE model_spec_id = :new"
                ),
                {"old": old, "new": new},
            )
