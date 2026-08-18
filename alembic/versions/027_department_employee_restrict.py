"""Refuse deleting a department that still has employees.

The previous ON DELETE CASCADE (plus ORM delete-orphan) wiped every
employee row in the department, including admin accounts. The API now
returns 409 first; this migration makes the database match.

Revision ID: 027
Revises: 026
"""

from typing import Sequence, Union

from alembic import op

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("employees_department_id_fkey", "employees", type_="foreignkey")
    op.create_foreign_key(
        "employees_department_id_fkey",
        "employees",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("employees_department_id_fkey", "employees", type_="foreignkey")
    op.create_foreign_key(
        "employees_department_id_fkey",
        "employees",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="CASCADE",
    )
