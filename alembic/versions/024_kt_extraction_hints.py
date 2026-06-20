"""Add extraction_hints to knowledge_types for domain-specific LLM extraction rules.

Revision ID: 024
Revises: 023
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_types",
        sa.Column(
            "extraction_hints",
            sa.Text(),
            nullable=True,
            comment=(
                "Domain-specific Markdown instructions injected into the wiki compiler prompt. "
                "Used to override general keep/drop rules for specialized domains (e.g. pentest, redteam)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("knowledge_types", "extraction_hints")
