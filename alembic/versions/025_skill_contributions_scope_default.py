"""Add server_default to skill_contributions.scope_type.

Migration 018 added skill_contributions.scope_type as NOT NULL with no
server_default — a fresh install applies 018 before the table ever holds a
row, so this hasn't broken any actual deployment, but it's a landmine for
any raw INSERT that omits the column and a real inconsistency with every
other NOT NULL column added elsewhere in this migration history, which all
use server_default. 'global' matches the value already assumed at the
application layer (SkillContributionCreate.scope_type default, and the ORM
mapped_column default in app/database/models.py).

Revision ID: 025
Revises: 024
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "skill_contributions",
        "scope_type",
        existing_type=sa.String(length=20),
        server_default="global",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "skill_contributions",
        "scope_type",
        existing_type=sa.String(length=20),
        server_default=None,
        existing_nullable=False,
    )
