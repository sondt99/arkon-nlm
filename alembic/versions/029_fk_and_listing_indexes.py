"""Index the foreign-key columns that deletes scan, plus the sources listing sort key.

Postgres must scan the referencing table for every parent row deleted. Thirteen FK
columns with ondelete behaviour had no covering index; migration 018 removed two that
previously existed.

The worst case was DELETE /employees/{id}: sources.contributed_by_employee_id and
wiki_page_revisions.changed_by_id are both SET NULL, so deleting one employee
sequentially scanned the table holding every document's full_text *and* the table that
gains a full content snapshot on every wiki edit — inside a single transaction also
holding locks on employees.

Two subtleties worth recording:

  * source_departments.department_id and skill_departments.department_id are the
    *trailing* column of a composite primary key, so the PK's B-tree cannot serve a
    lookup on department_id alone. DELETE /departments/{id} scanned both fully.

  * source_chunk_extracts.source_id declares index=True in models.py but no migration
    ever created it. Found by running `alembic revision --autogenerate` against a
    freshly-migrated database — genuine model/schema drift, not part of the original
    audit.

Indexes are created CONCURRENTLY where possible... except that alembic wraps each
migration in a transaction and CREATE INDEX CONCURRENTLY cannot run inside one. These
are plain CREATE INDEX, which takes a brief ACCESS EXCLUSIVE lock per table. On a large
deployment, run this during a maintenance window, or create them concurrently by hand
and then `alembic stamp 029`.

Revision ID: 029
Revises: 028
"""

from typing import Sequence, Union

from alembic import op

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (index name, table, columns)
_INDEXES: list[tuple[str, str, list[str]]] = [
    # --- hot delete paths: SET NULL against the largest tables ---
    ("ix_sources_contributed_by_employee_id", "sources", ["contributed_by_employee_id"]),
    ("ix_wiki_page_revisions_changed_by_id", "wiki_page_revisions", ["changed_by_id"]),
    ("ix_wiki_page_drafts_reviewed_by_id", "wiki_page_drafts", ["reviewed_by_id"]),
    ("ix_source_compilation_plans_reviewed_by", "source_compilation_plans", ["reviewed_by"]),
    ("ix_skill_versions_created_by", "skill_versions", ["created_by"]),
    # --- cascade paths ---
    ("ix_skill_contributions_skill_id", "skill_contributions", ["skill_id"]),
    ("ix_source_departments_department_id", "source_departments", ["department_id"]),
    ("ix_skill_departments_department_id", "skill_departments", ["department_id"]),
    ("ix_wiki_page_revisions_draft_id", "wiki_page_revisions", ["draft_id"]),
    ("ix_projects_created_by_id", "projects", ["created_by_id"]),
    ("ix_notebooklm_artifacts_ingest_source_id", "notebooklm_artifacts", ["ingest_source_id"]),
    ("ix_employees_custom_role_id", "employees", ["custom_role_id"]),
    # --- model/schema drift: declared index=True, never migrated ---
    ("ix_source_chunk_extracts_source_id", "source_chunk_extracts", ["source_id"]),
    # --- listing sort key ---
    # GET /sources orders by created_at DESC on every page. Composite with status so the
    # common filtered listing is served by the same index. Partially closes issue #85.
    ("ix_sources_status_created_at", "sources", ["status", "created_at"]),
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        # IF NOT EXISTS so this is safe on a database where an operator already created
        # some of these by hand (see the concurrency note in the docstring).
        quoted = ", ".join(f'"{c}"' for c in cols)
        op.execute(f'CREATE INDEX IF NOT EXISTS {name} ON "{table}" ({quoted})')


def downgrade() -> None:
    for name, _table, _cols in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
