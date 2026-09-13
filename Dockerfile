# Base images are pinned by DIGEST, not by tag. `python:3.12-slim` is a floating tag that
# upstream re-publishes on every patch and CVE rebuild, so two builds a week apart produced
# different interpreters and different system libraries from identical source. The tag is
# kept alongside the digest purely for readability — Docker resolves the digest and ignores
# the tag when both are given, so this cannot drift.
#
# Bumps are meant to arrive as reviewable pull requests, not as a side effect of a rebuild:
# see .github/dependabot.yml, which watches every Dockerfile and docker-compose.yml image
# in this repo.
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip --no-cache-dir

# Install dependencies in a separate layer so they're cached on code-only changes.
#
# uv export --locked writes the exact graph recorded in uv.lock and FAILS if the lockfile
# is out of date with pyproject.toml. This used to pass `--frozen`, which only skips
# re-locking and does NOT check, so a stale lock shipped into the image silently.
#
# Before that it was `pip install .` against pyproject alone, and since all 30 runtime
# deps are open-ended >= floors, two builds a week apart produced different closures from
# identical source — with no way to reproduce the last-known-good image. The security
# floors pinned in pyproject.toml (cryptography>=50.0 for PYSEC-2026-3552,
# aiohttp>=3.14.3, pyasn1>=0.6.4) were enforced at the bottom only; nothing above them was.
COPY --from=ghcr.io/astral-sh/uv:0.9.21@sha256:15f68a476b768083505fe1dbfcc998344d0135f0ca1b8465c4760b323904f05a /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
#
# `--no-deps` is load-bearing, not an optimisation. Without it pip RE-RESOLVES the exported
# file, so it re-applies every transitive constraint and can pick versions the lockfile does
# not contain — which made the claim above only partly true. It also loses `[tool.uv]
# override-dependencies`, which pip does not implement: the pillow security upgrade failed
# here with ResolutionImpossible against moviepy's `pillow<12.0` even though uv.lock pins
# pillow 12.3.0.
#
# An exported lockfile is already a complete, fully-pinned graph. Re-resolving it is the
# thing `--no-deps` exists to prevent.
RUN uv export --locked --no-dev --no-emit-project -o /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps -r /tmp/requirements.txt

# --- Runtime stage: no build tools ---
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-vie \
    gosu \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source and migration files
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY skills/ ./skills/

COPY entrypoint.sh migrate.sh ./
RUN sed -i 's/\r$//' entrypoint.sh migrate.sh && chmod +x entrypoint.sh migrate.sh

# uid/gid are pinned explicitly and MUST NOT CHANGE. `useradd -r` picks the highest free
# system id (999 on this base image), which is a build-time accident: a base-image rebuild
# that adds a system user would silently shift it. The named volumes of every existing
# deployment (temp_uploads, notebooklm_data) are already owned by 999:999, and since the
# container no longer starts as root it can no longer chown them back — a changed id here
# means an immediate permission failure on upgrade, not a self-healing one.
RUN groupadd -r --gid 999 appuser \
    && useradd -r --uid 999 --gid 999 appuser \
    && chown -R appuser:appuser /app

# Create the two data directories in the image, owned by appuser. Docker seeds a freshly
# created named volume from the image's content *and ownership*, so the default path needs
# no runtime chown and therefore no root. Previously these were mkdir'd and chown'd by
# entrypoint.sh on every boot, which is why the container had to start as root.
RUN mkdir -p /data/notebooklm-session /app/temp_uploads \
    && chown -R appuser:appuser /data /app/temp_uploads

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# The in-container identity is appuser, not root. Before this directive, privilege
# separation depended entirely on entrypoint.sh reaching its `gosu appuser` line, so
# anything that bypassed the entrypoint — `docker compose run --entrypoint sh api`, a
# Kubernetes manifest that sets its own command, a future compose override — ran as root,
# and `no-new-privileges` buys nothing when root is the starting point.
#
# entrypoint.sh still handles a root start (for bind-mounted volumes whose host ownership
# must be corrected), so `user: root` remains a working escape hatch; it is simply no
# longer the default.
USER appuser

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5055", "--proxy-headers", "--forwarded-allow-ips", "*"]
