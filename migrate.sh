#!/bin/sh
# Deliberate schema migration + built-in skill seeding for the Arkon backend image.
#
# WHY THIS IS NOT IN entrypoint.sh ANY MORE
# -----------------------------------------
# `alembic upgrade head` and `seed_skills` used to sit in the shared entrypoint, which means
# api, worker and worker_skills each ran them on every start AND every restart:
#
#   * three containers racing to migrate the same database at boot, with no lock;
#   * `docker compose restart worker` re-ran migrations against production;
#   * a failing migration plus `set -e` plus `restart: always` was a crash-loop with no
#     circuit breaker and no way to bring the API up read-only to diagnose;
#   * and it is the mechanism by which a destructive migration destroys data before anyone
#     can intervene (issue #41).
#
# Now exactly one thing runs migrations: the one-shot `migrate` service in
# docker-compose.yml, which invokes this script. It has `restart: "no"`, so a failure stops
# once and stays stopped instead of looping, and `docker compose restart <svc>` no longer
# touches the schema.
#
# PRE-FLIGHT REFUSAL ON DESTRUCTIVE STEPS
# ---------------------------------------
# Before applying anything, the pending revision range is scanned for irreversible data
# loss (dropped tables/columns, DELETE, TRUNCATE) in its upgrade() path. If any is found the
# migration REFUSES and names the revision, file and line. Non-destructive migrations — the
# overwhelming majority — still apply automatically, so a routine deploy is unchanged.
#
# To proceed anyway, after taking a backup:
#
#   ALLOW_DESTRUCTIVE_MIGRATIONS=1 docker compose --env-file .env.docker run --rm migrate
#
# Only downgrade() bodies are excluded from the scan, because nearly every downgrade drops
# what its upgrade created; scanning whole files would flag all 34 revisions. The scan is
# textual, so a scratch/temp table dropped inside a data-repair migration reads as
# destructive too. That is the intended bias: this gate exists to force a human to look.
#
# Usage:
#   ./migrate.sh                # pre-flight, then upgrade to head, then seed skills
#   ./migrate.sh --check        # pre-flight only; exit 1 if it would refuse. No writes.
#   ./migrate.sh --skip-seed    # pre-flight + upgrade, no skill seeding
#
# NOT SOLVED HERE: a cross-host advisory lock. The concrete three-container race is gone
# because only one service migrates, but two operators running `up` simultaneously against
# the same database are still unserialised. A real lock needs the live connection inside
# alembic/env.py.

set -e

CHECK_ONLY=0
SKIP_SEED=0
for arg in "$@"; do
    case "$arg" in
        --check)     CHECK_ONLY=1 ;;
        --skip-seed) SKIP_SEED=1 ;;
        *)
            echo "migrate.sh: unknown argument '$arg'" >&2
            echo "usage: migrate.sh [--check] [--skip-seed]" >&2
            exit 2
            ;;
    esac
done

# Anything that drops or deletes committed rows. Anchored at the start of the line for the
# op.* forms so a commented-out call does not match; raw SQL is matched anywhere because it
# lives inside op.execute() strings and heredocs.
DESTRUCTIVE_RE='^[[:space:]]*op\.(drop_table|drop_column)\(|DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN|TRUNCATE[[:space:]]|DELETE[[:space:]]+FROM'

echo "==> Resolving current database revision"
# `alembic current` writes the revision to stdout and its INFO log to stderr. An empty
# stdout means there is no alembic_version table yet, i.e. a brand-new database.
CURRENT=$(alembic current 2>/dev/null | awk 'NF {print $1; exit}')

if [ -z "$CURRENT" ]; then
    echo "    No alembic_version row — this is a fresh database."
    echo "    Skipping the destructive pre-flight: there is no data to lose."
else
    echo "    Current revision: $CURRENT"
    echo "==> Pre-flight: scanning pending migrations for destructive steps"

    # The range is inclusive of both ends, so the already-applied CURRENT revision comes back
    # too. It must be filtered out — otherwise a database sitting on a destructive revision
    # would refuse every subsequent deploy forever.
    PENDING=$(alembic history -r "${CURRENT}:head" --verbose 2>/dev/null | awk -v cur="$CURRENT" '
        /^Rev: /  { rev = $2 }
        /^Path: / { if (rev != cur) print $2 }
    ')

    if [ -z "$PENDING" ]; then
        echo "    Already at head — nothing pending."
    else
        echo "    Pending revisions:"
        for f in $PENDING; do echo "      - $f"; done

        FOUND=0
        for f in $PENDING; do
            # Only the upgrade() body: a downgrade dropping what its upgrade created is
            # normal and never runs during `upgrade head`.
            HITS=$(awk '/^def upgrade\(/ {inup = 1; next} /^def / {inup = 0} inup' "$f" \
                   | grep -nE "$DESTRUCTIVE_RE" || true)
            if [ -n "$HITS" ]; then
                FOUND=1
                echo ""
                echo "    DESTRUCTIVE: $f"
                echo "$HITS" | sed 's/^/        upgrade():/'
            fi
        done

        if [ "$FOUND" = "1" ]; then
            echo ""
            echo "================================================================"
            echo "REFUSING TO MIGRATE: pending revisions destroy data."
            echo ""
            echo "Nothing has been applied. The database is untouched at $CURRENT."
            echo ""
            echo "Back up first:"
            echo "  docker exec arkon_postgres pg_dump -U arkon -d arkon -Fc > arkon-\$(date +%F).dump"
            echo ""
            echo "Then apply deliberately:"
            echo "  ALLOW_DESTRUCTIVE_MIGRATIONS=1 \\"
            echo "    docker compose --env-file .env.docker run --rm migrate"
            echo "================================================================"

            if [ "${ALLOW_DESTRUCTIVE_MIGRATIONS:-0}" != "1" ]; then
                exit 1
            fi
            echo ""
            echo "ALLOW_DESTRUCTIVE_MIGRATIONS=1 is set — proceeding on the operator's"
            echo "explicit instruction."
        else
            echo "    No destructive steps in the pending range."
        fi
    fi
fi

if [ "$CHECK_ONLY" = "1" ]; then
    echo "==> --check: pre-flight passed, nothing applied."
    exit 0
fi

echo "==> Running database migrations"
alembic upgrade head
echo "    Migrations complete."

if [ "$SKIP_SEED" = "1" ]; then
    echo "==> --skip-seed: not seeding built-in skills."
    exit 0
fi

echo "==> Seeding built-in skills"
python -m app.scripts.seed_skills
echo "    Skills seeding complete."
