#!/bin/sh
set -e

# Ensure writable data directories exist (needed for bind-mounted volumes)
mkdir -p /data/notebooklm-session
mkdir -p /app/temp_uploads

echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

echo "Seeding built-in skills..."
python -m app.scripts.seed_skills
echo "Skills seeding complete."

exec "$@"
