"""Record who owns each NotebookLM passthrough notebook.

The `/api/notebooklm/nlm/*` endpoints take a raw Google notebook id and forward it to a
client built from one process-wide `storage_state.json` — a single shared Google account.
No ownership was recorded anywhere for those notebooks, so `Depends(get_current_user)` was
the whole access-control story: any authenticated employee could list every notebook in the
company, read its sources, chat with it (reaching the documents through NotebookLM's own
RAG), and DELETE it. This table is the missing boundary.

No backfill is possible or attempted. Notebooks already sitting in the shared account
cannot have an owner reconstructed — Google's per-notebook creator is the service account
for every one of them. They are therefore left without a row, which the router treats as
admin-only (see `_resolve_nlm_notebook` in app/routers/notebooklm.py for the reasoning).
Notebooks created through the DB-backed `/notebooklm/notebooks` endpoints are already
attributed by notebooklm_notebooks.created_by_employee_id and need no row here; the router
consults both tables, which is also why notebook_id gains an index.

Revision ID: 033
Revises: 032
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notebooklm_passthrough_owners",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nlm_id", sa.String(200), nullable=False),
        sa.Column("owner_employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        # nullable=False, unlike the created_at in migration 021: models.py declares these
        # as non-Optional, so omitting it is the model/schema drift that migration 029 had
        # to go back and clean up. Safe here because of the server default.
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # One owner per notebook: a duplicate row would hand a second employee full access to
    # the first employee's notebook, which is precisely the failure being closed.
    op.create_unique_constraint(
        "uq_notebooklm_passthrough_owners_nlm_id",
        "notebooklm_passthrough_owners",
        ["nlm_id"],
    )
    # ON DELETE CASCADE makes Postgres scan this table for every employee deleted.
    op.create_index(
        "ix_notebooklm_passthrough_owners_owner",
        "notebooklm_passthrough_owners",
        ["owner_employee_id"],
    )

    # Model/schema drift avoided up front: the passthrough guard resolves ownership by the
    # Google-side id on every request, and notebooklm_notebooks.notebook_id had no index.
    op.create_index(
        "ix_notebooklm_notebooks_notebook_id",
        "notebooklm_notebooks",
        ["notebook_id"],
    )


def downgrade() -> None:
    """Reversible in shape, and destructive in effect — say so rather than pretend.

    Dropping this table deletes every ownership claim. The passthrough endpoints on the
    pre-033 code have no ownership check at all, so a downgrade reopens the original
    cross-employee read/delete hole; on the post-033 code every notebook becomes
    admin-only. Neither is a quiet no-op.
    """
    op.drop_index("ix_notebooklm_notebooks_notebook_id", table_name="notebooklm_notebooks")
    op.drop_index(
        "ix_notebooklm_passthrough_owners_owner",
        table_name="notebooklm_passthrough_owners",
    )
    op.drop_constraint(
        "uq_notebooklm_passthrough_owners_nlm_id",
        "notebooklm_passthrough_owners",
        type_="unique",
    )
    op.drop_table("notebooklm_passthrough_owners")
