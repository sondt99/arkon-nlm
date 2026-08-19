"""Make (skill_id, version_number) unique on skill_versions.

Version numbers are computed as `current_version + 1` from a row read without FOR UPDATE,
and the destination MinIO prefix is derived from that number. Two reviewers approving
different contributions for the same skill both computed version N and both copy_prefix'd
into skills/<id>/versions/N/ — which copy_prefix does not clear — so that directory ended
up holding an interleaved mix of both contributions' files, and two skill_versions rows
both claimed version N. set_latest_version then resolved N via .first(), i.e.
non-deterministically.

An advisory lock in SkillService now prevents the race. This constraint makes the
invariant enforceable rather than merely intended, so a future code path that forgets the
lock fails loudly instead of silently corrupting a version directory.

Revision ID: 030
Revises: 029
"""

from typing import Sequence, Union

from alembic import op

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "uq_skill_versions_skill_version"


def upgrade() -> None:
    # Any duplicates already created during the window where the race was live would make
    # the constraint fail to build. Keep the most recently created row per
    # (skill_id, version_number); the losers are by definition the interleaved ones.
    op.execute(
        """
        DELETE FROM skill_versions sv
        USING (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY skill_id, version_number
                ORDER BY created_at DESC, id
            ) AS rn
            FROM skill_versions
        ) ranked
        WHERE sv.id = ranked.id AND ranked.rn > 1
        """
    )

    # Re-point any skill whose current_version pointed at a row that was just removed is
    # unnecessary: current_version is an integer on skills, not an FK, and the surviving
    # row keeps the same version_number.

    op.execute(
        f"ALTER TABLE skill_versions "
        f"ADD CONSTRAINT {CONSTRAINT} UNIQUE (skill_id, version_number)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE skill_versions DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
