FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip --no-cache-dir

# Install dependencies in a separate layer so they're cached on code-only changes.
#
# uv sync --frozen installs the exact graph recorded in uv.lock and FAILS if the lockfile
# is out of date with pyproject.toml. Previously this was `pip install .` against
# pyproject alone, and since all 30 runtime deps are open-ended >= floors, two builds a
# week apart produced different closures from identical source — with no way to reproduce
# the last-known-good image. The security floors pinned in pyproject.toml
# (cryptography>=50.0 for PYSEC-2026-3552, aiohttp>=3.14.3, pyasn1>=0.6.4) were enforced
# at the bottom only; nothing above them was.
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# --- Runtime stage: no build tools ---
FROM python:3.12-slim AS runtime

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

COPY entrypoint.sh ./
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app

# Container starts as root so entrypoint.sh can chown bind-mounted volumes,
# then drops to appuser via gosu before running migrations/seeding/the app.
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5055", "--proxy-headers", "--forwarded-allow-ips", "*"]
