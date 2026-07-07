#!/bin/sh
set -e

# Ensure writable data directories exist (needed for bind-mounted volumes),
# then hand off to the unprivileged appuser for everything else — the
# container only needs root for this one-time ownership fix.
mkdir -p /data/notebooklm-session
mkdir -p /app/temp_uploads
chown -R appuser:appuser /data/notebooklm-session /app/temp_uploads

echo "Running database migrations..."
gosu appuser alembic upgrade head
echo "Migrations complete."

echo "Seeding built-in skills..."
gosu appuser python -m app.scripts.seed_skills
echo "Skills seeding complete."

exec gosu appuser "$@"
