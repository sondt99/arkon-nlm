"""
Alembic migration environment.
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

# Ensure the project root is on sys.path so 'app' module can be found
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import settings
from app.database.models import Base

# Alembic Config object
config = context.config

# Override sqlalchemy.url with the app's DATABASE_URL
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Indexes created with raw SQL (op.execute) are invisible to Base.metadata, so
# autogenerate reflects them from the database, finds no counterpart, and emits
# drop_index. That is exactly how migration 018 came to drop four live indexes —
# the unique index on wiki_pages.slug plus three GIN indexes — none of which any
# later migration restored.
#
# Listed explicitly so the set is greppable. The include_object hook below also
# protects any *other* reflected-only index, so adding a raw-SQL index does not
# require remembering to update this list.
RAW_SQL_INDEXES = {
    # created in 006_wiki_pivot.py
    "ix_wiki_pages_fulltext",
    "ix_wiki_pages_kt_slugs",
    "ix_wiki_pages_source_ids",
    # created in 010_workspace_wiki_scope.py, restored in 028
    "uq_wiki_pages_slug_scope",
    # created per-dimension in 015_multi_dim_embeddings.py (HNSW + model)
    # names are ix_wiki_page_embeddings_<dim>_{hnsw,model}
}

# Tables with no ORM model that still exist in every migrated database. Excluded so
# autogenerate does not propose dropping them as a side effect; removing them should
# be a deliberate migration. Tracked in issue #93.
ORPHANED_LEGACY_TABLES = {"knowledge_scopes", "scope_memberships"}


def include_object(object_, name, type_, reflected, compare_to):
    """Keep autogenerate from dropping objects it cannot see in the models."""
    # A reflected index with no metadata counterpart exists in the database but not
    # in the models. Never propose dropping it.
    if type_ == "index" and reflected and compare_to is None:
        return False
    if type_ == "table" and reflected and name in ORPHANED_LEGACY_TABLES:
        return False
    return True


_CONFIGURE_KWARGS = {
    "target_metadata": target_metadata,
    "include_object": include_object,
    # compare_type is deliberately left OFF. Enabling it surfaces ~20 pre-existing
    # type drifts (mostly timestamp tz and the chat_messages.sources JSON-vs-JSONB
    # mismatch in issue #85), which floods every autogenerate diff. A diff nobody can
    # skim is how 018's destructive drops got approved in the first place. Fix the
    # drift first, then turn this on so it stays green — tracked in #85.
}


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_CONFIGURE_KWARGS,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, **_CONFIGURE_KWARGS)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
