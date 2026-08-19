"""skill and contribution refactor

Revision ID: 018
Revises: 017
Create Date: 2026-05-08 10:00:00.000000

This revision drops skills.description (added by 012) and
skill_contributions.description (added by 017) and originally did so with no archive, so
every skill description written before it was destroyed the moment entrypoint.sh ran
`alembic upgrade head` (issue #41).

The archive added below does NOT recover anything on a database already past this
revision — Alembic will not re-run an applied revision, and the columns are gone there.
It protects only an environment still at or below 017, and fresh restores of pre-018
dumps.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = '018'
down_revision: Union[str, None] = '017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Neither column has a successor. skill_contributions kept `title`, which is a 200-char
# label rather than a body, and a skill's description is now read out of SKILL.md in
# object storage at upload time (SkillService.inspect_zip) and never persisted. So there
# is nothing to copy forward into, and the choice is archive or lose.
#
# app_config is the archive because it is the only durable store that already exists in
# both a migrated database and a fresh one. A dedicated side table would diverge the two
# — and because alembic/env.py's include_object only exempts unmodelled *indexes* and a
# named legacy-table list, an unmodelled archive table would surface in the next
# autogenerate as a proposed DROP TABLE. That is the exact mechanism that made this
# revision drop four live indexes; it should not be reproduced to fix it.
#
# The rows are inert: ConfigService reads app_config only by explicit key, and both
# get_all() and update_many() iterate the fixed ALL_CONFIG_KEYS allowlist, so an archive
# key can never surface in — or be overwritten by — the admin config UI.
_ARCHIVE_SKILLS_DESCRIPTION = """
INSERT INTO app_config (key, value, updated_at)
SELECT
    'archived_018_skills_description',
    jsonb_agg(
        jsonb_build_object(
            'id', s.id,
            'slug', s.slug,
            'name', s.name,
            'description', s.description
        ) ORDER BY s.slug
    )::text,
    now()
FROM skills s
WHERE s.description ~ '[^[:space:]]'
HAVING count(*) > 0
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, updated_at = now()
"""

_ARCHIVE_CONTRIBUTIONS_DESCRIPTION = """
INSERT INTO app_config (key, value, updated_at)
SELECT
    'archived_018_skill_contributions_description',
    jsonb_agg(
        jsonb_build_object(
            'id', c.id,
            'skill_id', c.skill_id,
            'contributor_id', c.contributor_id,
            'title', c.title,
            'description', c.description
        ) ORDER BY c.created_at, c.id
    )::text,
    now()
FROM skill_contributions c
WHERE c.description ~ '[^[:space:]]'
HAVING count(*) > 0
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, updated_at = now()
"""


def _has_column(table: str, column: str) -> bool:
    """Whether `table.column` exists right now.

    The archive and the drops are both gated on this. A database that already lost these
    columns must still be able to reach head: a migration that aborts on a fresh or
    hand-repaired install is a worse failure than the one being fixed.
    """
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    # --- Archive before anything below can drop it ---
    # `HAVING count(*) > 0` keeps this a true no-op: a fresh install writes no row at all
    # rather than an app_config entry holding an empty array.
    if _has_column('skills', 'description'):
        op.execute(_ARCHIVE_SKILLS_DESCRIPTION)
    if _has_column('skill_contributions', 'description'):
        op.execute(_ARCHIVE_CONTRIBUTIONS_DESCRIPTION)

    # --- Logic from 589ac254ec03 ---
    op.alter_column('app_config', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.alter_column('audit_log', 'principal_type',
               existing_type=sa.VARCHAR(length=20),
               comment='human or agent',
               existing_nullable=False,
               existing_server_default=sa.text("'human'::character varying"))
    op.alter_column('audit_log', 'action',
               existing_type=sa.VARCHAR(length=50),
               comment='Action attempted (read, list, delete...)',
               existing_nullable=False)
    op.alter_column('audit_log', 'resource_type',
               existing_type=sa.VARCHAR(length=50),
               comment='Type of resource: source, wiki_page, etc.',
               existing_nullable=False)
    op.alter_column('audit_log', 'resource_id',
               existing_type=sa.VARCHAR(length=100),
               comment='UUID or identifier of the resource',
               existing_nullable=False)
    op.alter_column('audit_log', 'reason',
               existing_type=sa.TEXT(),
               comment='Human-readable reason for the decision',
               existing_nullable=True)
    op.alter_column('audit_log', 'metadata',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               comment='Extra context (IP, user agent, request ID...)',
               existing_nullable=True)
    op.drop_column('audit_log', 'scope_type')
    op.drop_column('audit_log', 'scope_id')
    op.alter_column('departments', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.alter_column('departments', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.alter_column('employees', 'password_hash',
               existing_type=sa.VARCHAR(length=500),
               comment='bcrypt hash of password',
               existing_comment='bcrypt hash',
               existing_nullable=True)
    op.alter_column('employees', 'role',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               comment='admin or employee — system-level role',
               existing_comment='admin or employee',
               existing_server_default=sa.text("'employee'::character varying"))
    op.alter_column('employees', 'mcp_token',
               existing_type=sa.VARCHAR(length=500),
               comment='Bearer token for MCP authentication',
               existing_comment='Bearer token for MCP',
               existing_nullable=True)
    op.alter_column('employees', 'is_active',
               existing_type=sa.BOOLEAN(),
               nullable=False,
               existing_server_default=sa.text('true'))
    op.alter_column('employees', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.alter_column('employees', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.drop_index(op.f('ix_employees_custom_role_id'), table_name='employees')
    op.alter_column('knowledge_types', 'slug',
               existing_type=sa.VARCHAR(length=50),
               comment="URL-safe identifier, e.g. 'sop', 'product', 'hr-policy'",
               existing_comment='URL-safe identifier',
               existing_nullable=False)
    op.alter_column('knowledge_types', 'name',
               existing_type=sa.VARCHAR(length=100),
               comment="Display name, e.g. 'Standard Operating Procedure'",
               existing_comment='Display name',
               existing_nullable=False)
    op.alter_column('knowledge_types', 'color',
               existing_type=sa.VARCHAR(length=20),
               comment='Hex color for UI badge',
               existing_comment='Hex color for UI',
               existing_nullable=True,
               existing_server_default=sa.text("'#6366f1'::character varying"))
    op.alter_column('knowledge_types', 'sort_order',
               existing_type=sa.INTEGER(),
               nullable=False,
               existing_server_default=sa.text('0'))
    op.alter_column('knowledge_types', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.alter_column('notes', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.alter_column('notes', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.alter_column('project_members', 'role',
               existing_type=sa.VARCHAR(length=20),
               comment='viewer, contributor, editor, or admin',
               existing_nullable=False,
               existing_server_default=sa.text("'member'::character varying"))
    op.alter_column('projects', 'status',
               existing_type=sa.VARCHAR(length=20),
               comment='active or archived',
               existing_nullable=False,
               existing_server_default=sa.text("'active'::character varying"))
    # Added nullable, backfilled, then constrained. In its original form — NOT NULL with
    # no server_default — this aborts with NotNullViolationError against any database that
    # already holds skill_contributions rows, which is exactly the population that has
    # descriptions to lose in the drop below. So the archive above was unreachable on
    # every install it was meant to protect. 025 still supplies the server_default; the
    # resulting schema is unchanged.
    op.add_column('skill_contributions', sa.Column('scope_type', sa.String(length=20), nullable=True, comment='Scope type for NEW skills: global or department'))
    op.execute("UPDATE skill_contributions SET scope_type = 'global' WHERE scope_type IS NULL")
    op.alter_column('skill_contributions', 'scope_type', existing_type=sa.String(length=20), nullable=False)
    op.add_column('skill_contributions', sa.Column('scope_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='List of Department IDs if scope_type is department'))
    op.alter_column('skill_contributions', 'storage_path',
               existing_type=sa.VARCHAR(length=1000),
               comment="MinIO prefix for this contribution's files, e.g. 'skill-contributions/{id}/'",
               existing_comment="MinIO prefix for this contribution's files, e.g. 'contributions/{id}/'",
               existing_nullable=True)
    # IF EXISTS, so the guarded archive above is not undone by an unguarded drop.
    op.execute('ALTER TABLE skill_contributions DROP COLUMN IF EXISTS description')
    op.alter_column('skills', 'scope_type',
               existing_type=sa.VARCHAR(length=20),
               comment='Scope type: global, project, department, team',
               existing_nullable=False,
               existing_server_default=sa.text("'global'::character varying"))
    op.alter_column('skills', 'scope_id',
               existing_type=sa.UUID(),
               comment='Scope entity ID. Null for global scope.',
               existing_nullable=True)
    op.drop_constraint(op.f('skills_slug_key'), 'skills', type_='unique')
    op.execute('ALTER TABLE skills DROP COLUMN IF EXISTS description')
    op.alter_column('sources', 'scope_type',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               comment='Scope type: global or project',
               existing_server_default=sa.text("'global'::character varying"))
    op.alter_column('sources', 'scope_id',
               existing_type=sa.UUID(),
               comment='Project/workspace ID when scope_type=project. Null for global.',
               existing_nullable=True)
    op.alter_column('sources', 'status',
               existing_type=sa.VARCHAR(length=50),
               nullable=False,
               existing_server_default=sa.text("'pending'::character varying"))
    op.alter_column('sources', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.alter_column('sources', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))
    op.drop_index(op.f('ix_sources_contributed_by_employee_id'), table_name='sources')
    op.alter_column('wiki_pages', 'scope_type',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               comment='Scope type: global or project',
               existing_server_default=sa.text("'global'::character varying"))
    op.alter_column('wiki_pages', 'scope_id',
               existing_type=sa.UUID(),
               comment='Project/workspace ID. Null for global scope.',
               existing_nullable=True)
    op.drop_index(op.f('ix_wiki_pages_fulltext'), table_name='wiki_pages', postgresql_using='gin')
    op.drop_index(op.f('ix_wiki_pages_kt_slugs'), table_name='wiki_pages', postgresql_using='gin')
    op.drop_index(op.f('ix_wiki_pages_source_ids'), table_name='wiki_pages', postgresql_using='gin')
    op.drop_index(op.f('uq_wiki_pages_slug_scope'), table_name='wiki_pages')

    # --- Logic from 48007958cbcc ---
    op.drop_constraint(op.f('skill_contributions_skill_id_fkey'), 'skill_contributions', type_='foreignkey')
    op.create_foreign_key(None, 'skill_contributions', 'skills', ['skill_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    """Reversing 018 is not supported. See the reasoning below.

    This body was autogenerated against a PRE-015 database even though 018 runs after 015,
    which left three independent defects — every one of which aborts the downgrade
    mid-transaction, after the foreign-key swap has already been attempted:

      1. It re-created ix_wiki_pages_embedding_hnsw on wiki_pages.embedding, a column
         migration 015 had already dropped. Fails with
         `UndefinedColumn: column "embedding" does not exist`.
      2. It called `op.drop_constraint(None, 'skill_contributions', type_='foreignkey')`,
         which renders `ALTER TABLE ... DROP CONSTRAINT NULL` — a syntax error. The upgrade
         had created that FK with `op.create_foreign_key(None, ...)`, letting Postgres name
         it, so there was no name to drop by.
      3. It re-created uq_wiki_pages_slug_scope WITHOUT deduplicating first, and re-added
         skills.description as an empty column — so even a downgrade that ran would not
         restore the pre-018 state.

    Nothing caught any of this because nothing ever ran a downgrade. CI now does, which is
    why this needed to become honest rather than merely quieter.

    Raising is deliberate. A silently-broken downgrade is worse than a refused one: it
    invites an operator to attempt a rollback during an incident, fail halfway through, and
    end up in a state neither revision describes. The recovery path from this revision is a
    restore from backup.

    Migration 028 restores the three indexes 018 dropped that are still meaningful (the
    unique index and two GIN indexes), so an operator wanting the pre-018 *index* state
    should downgrade to 028 rather than past 018.

    The two description columns are a separate matter from the downgrade. On a database
    that ran the *current* upgrade() their contents are in app_config under
    'archived_018_skills_description' and
    'archived_018_skill_contributions_description', so an operator can re-add the columns
    by hand and restore from that JSON by primary key. A database that ran the original
    upgrade() has no archive and no way to produce one.
    """
    raise NotImplementedError(
        "Migration 018 cannot be reversed. Its autogenerated downgrade was written against "
        "a pre-015 schema and fails three separate ways (index on a dropped column, "
        "DROP CONSTRAINT NULL, and re-creating a unique index over un-deduplicated rows). "
        "It also does not restore the dropped description columns: re-add them by hand and "
        "read the values back from app_config keys archived_018_skills_description and "
        "archived_018_skill_contributions_description, or restore from backup."
    )


def _unreachable_original_downgrade() -> None:
    """Kept for reference only — never called. See downgrade() above for why."""
    op.drop_constraint(None, 'skill_contributions', type_='foreignkey')
    op.create_foreign_key(op.f('skill_contributions_skill_id_fkey'), 'skill_contributions', 'skills', ['skill_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('uq_wiki_pages_slug_scope'), 'wiki_pages', ['slug', 'scope_type', sa.literal_column("COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'::uuid)")], unique=True)
    op.create_index(op.f('ix_wiki_pages_source_ids'), 'wiki_pages', ['source_ids'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_wiki_pages_kt_slugs'), 'wiki_pages', ['knowledge_type_slugs'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_wiki_pages_fulltext'), 'wiki_pages', [sa.literal_column("to_tsvector('simple'::regconfig, content_md)")], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_wiki_pages_embedding_hnsw'), 'wiki_pages', ['embedding'], unique=False, postgresql_ops={'embedding': 'vector_cosine_ops'}, postgresql_with={'m': '16', 'ef_construction': '64'}, postgresql_using='hnsw')
    op.alter_column('wiki_pages', 'scope_id',
               existing_type=sa.UUID(),
               comment=None,
               existing_comment='Project/workspace ID. Null for global scope.',
               existing_nullable=True)
    op.alter_column('wiki_pages', 'scope_type',
               existing_type=sa.VARCHAR(length=20),
               nullable=True,
               comment=None,
               existing_comment='Scope type: global or project',
               existing_server_default=sa.text("'global'::character varying"))
    op.create_index(op.f('ix_sources_contributed_by_employee_id'), 'sources', ['contributed_by_employee_id'], unique=False)
    op.alter_column('sources', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('sources', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('sources', 'status',
               existing_type=sa.VARCHAR(length=50),
               nullable=True,
               existing_server_default=sa.text("'pending'::character varying"))
    op.alter_column('sources', 'scope_id',
               existing_type=sa.UUID(),
               comment=None,
               existing_comment='Project/workspace ID when scope_type=project. Null for global.',
               existing_nullable=True)
    op.alter_column('sources', 'scope_type',
               existing_type=sa.VARCHAR(length=20),
               nullable=True,
               comment=None,
               existing_comment='Scope type: global or project',
               existing_server_default=sa.text("'global'::character varying"))
    op.add_column('skills', sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True))
    op.create_unique_constraint(op.f('skills_slug_key'), 'skills', ['slug'], postgresql_nulls_not_distinct=False)
    op.alter_column('skills', 'scope_id',
               existing_type=sa.UUID(),
               comment=None,
               existing_comment='Scope entity ID. Null for global scope.',
               existing_nullable=True)
    op.alter_column('skills', 'scope_type',
               existing_type=sa.VARCHAR(length=20),
               comment=None,
               existing_comment='Scope type: global, project, department, team',
               existing_nullable=False,
               existing_server_default=sa.text("'global'::character varying"))
    op.add_column('skill_contributions', sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True))
    op.alter_column('skill_contributions', 'storage_path',
               existing_type=sa.VARCHAR(length=1000),
               comment="MinIO prefix for this contribution's files, e.g. 'contributions/{id}/'",
               existing_comment="MinIO prefix for this contribution's files, e.g. 'skill-contributions/{id}/'",
               existing_nullable=True)
    op.drop_column('skill_contributions', 'scope_ids')
    op.drop_column('skill_contributions', 'scope_type')
    op.alter_column('projects', 'status',
               existing_type=sa.VARCHAR(length=20),
               comment=None,
               existing_comment='active or archived',
               existing_nullable=False,
               existing_server_default=sa.text("'active'::character varying"))
    op.alter_column('project_members', 'role',
               existing_type=sa.VARCHAR(length=20),
               comment=None,
               existing_comment='viewer, contributor, editor, or admin',
               existing_nullable=False,
               existing_server_default=sa.text("'member'::character varying"))
    op.alter_column('notes', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('notes', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('knowledge_types', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('knowledge_types', 'sort_order',
               existing_type=sa.INTEGER(),
               nullable=True,
               existing_server_default=sa.text('0'))
    op.alter_column('knowledge_types', 'color',
               existing_type=sa.VARCHAR(length=20),
               comment='Hex color for UI',
               existing_comment='Hex color for UI badge',
               existing_nullable=True,
               existing_server_default=sa.text("'#6366f1'::character varying"))
    op.alter_column('knowledge_types', 'name',
               existing_type=sa.VARCHAR(length=100),
               comment='Display name',
               existing_comment="Display name, e.g. 'Standard Operating Procedure'",
               existing_nullable=False)
    op.alter_column('knowledge_types', 'slug',
               existing_type=sa.VARCHAR(length=50),
               comment='URL-safe identifier',
               existing_comment="URL-safe identifier, e.g. 'sop', 'product', 'hr-policy'",
               existing_nullable=False)
    op.create_index(op.f('ix_employees_custom_role_id'), 'employees', ['custom_role_id'], unique=False)
    op.alter_column('employees', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('employees', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('employees', 'is_active',
               existing_type=sa.BOOLEAN(),
               nullable=True,
               existing_server_default=sa.text('true'))
    op.alter_column('employees', 'mcp_token',
               existing_type=sa.VARCHAR(length=500),
               comment='Bearer token for MCP',
               existing_comment='Bearer token for MCP authentication',
               existing_nullable=True)
    op.alter_column('employees', 'role',
               existing_type=sa.VARCHAR(length=20),
               nullable=True,
               comment='admin or employee',
               existing_comment='admin or employee — system-level role',
               existing_server_default=sa.text("'employee'::character varying"))
    op.alter_column('employees', 'password_hash',
               existing_type=sa.VARCHAR(length=500),
               comment='bcrypt hash',
               existing_comment='bcrypt hash of password',
               existing_nullable=True)
    op.alter_column('departments', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('departments', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.add_column('audit_log', sa.Column('scope_id', sa.UUID(), autoincrement=False, nullable=True))
    op.add_column('audit_log', sa.Column('scope_type', sa.VARCHAR(length=20), autoincrement=False, nullable=True))
    op.alter_column('audit_log', 'metadata',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               comment=None,
               existing_comment='Extra context (IP, user agent, request ID...)',
               existing_nullable=True)
    op.alter_column('audit_log', 'reason',
               existing_type=sa.TEXT(),
               comment=None,
               existing_comment='Human-readable reason for the decision',
               existing_nullable=True)
    op.alter_column('audit_log', 'resource_id',
               existing_type=sa.VARCHAR(length=100),
               comment=None,
               existing_comment='UUID or identifier of the resource',
               existing_nullable=False)
    op.alter_column('audit_log', 'resource_type',
               existing_type=sa.VARCHAR(length=50),
               comment=None,
               existing_comment='Type of resource: source, wiki_page, etc.',
               existing_nullable=False)
    op.alter_column('audit_log', 'action',
               existing_type=sa.VARCHAR(length=50),
               comment=None,
               existing_comment='Action attempted (read, list, delete...)',
               existing_nullable=False)
    op.alter_column('audit_log', 'principal_type',
               existing_type=sa.VARCHAR(length=20),
               comment=None,
               existing_comment='human or agent',
               existing_nullable=False,
               existing_server_default=sa.text("'human'::character varying"))
    op.alter_column('app_config', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
