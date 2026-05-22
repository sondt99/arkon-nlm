"""Add NotebookLM integration tables

Revision ID: 021
Revises: 020
Create Date: 2026-05-21 00:00:00.000000

Adds:
- notebooklm_notebooks: Arkon-managed NLM notebooks (optionally linked to a Source)
- notebooklm_artifacts: AI-generated artifacts per notebook with status polling
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = '021'
down_revision: Union[str, None] = '020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notebooklm_notebooks',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('notebook_id', sa.String(200), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('source_id', UUID(as_uuid=True), sa.ForeignKey('sources.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_by_employee_id', UUID(as_uuid=True), sa.ForeignKey('employees.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    op.create_index('ix_notebooklm_notebooks_source_id', 'notebooklm_notebooks', ['source_id'])
    op.create_index('ix_notebooklm_notebooks_created_by', 'notebooklm_notebooks', ['created_by_employee_id'])

    op.create_table(
        'notebooklm_artifacts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('notebook_ref_id', UUID(as_uuid=True), sa.ForeignKey('notebooklm_notebooks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('artifact_id', sa.String(200), nullable=True),
        sa.Column('task_id', sa.String(200), nullable=True),
        sa.Column('artifact_type', sa.String(50), nullable=False),
        sa.Column('report_format', sa.String(50), nullable=True),
        sa.Column('title', sa.String(500), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('download_url', sa.String(2000), nullable=True),
        sa.Column('minio_key', sa.String(500), nullable=True),
        sa.Column('ingest_source_id', UUID(as_uuid=True), sa.ForeignKey('sources.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    op.create_index('ix_notebooklm_artifacts_notebook_ref_id', 'notebooklm_artifacts', ['notebook_ref_id'])
    op.create_index('ix_notebooklm_artifacts_status', 'notebooklm_artifacts', ['status'])


def downgrade() -> None:
    op.drop_table('notebooklm_artifacts')
    op.drop_table('notebooklm_notebooks')
