# syntax=docker/dockerfile:1
#
# TrackBit API — production image.
# Build context: the repo root (trackbit_api/). Built by Dokploy from this
# Dockerfile. Runtime configuration (DATABASE_URL, JWT_SECRET_KEY, ...) is
# injected as environment variables by the platform — never baked into the image.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# uv: fast, reproducible installs straight from the committed lock file.
RUN pip install --no-cache-dir uv

# 1) Dependencies only. Resolved from uv.lock (no dev tools, project excluded)
#    into the system Python so console scripts (alembic, gunicorn, uvicorn)
#    land on PATH. This layer is cached until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt

# 2) Application source. We run the app from /app directly (uvicorn imports
#    app.main:app), so the project itself does not need to be pip-installed.
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

# Apply migrations, then serve. With multiple replicas, run migrations once
# (or accept that "alembic upgrade head" is a no-op on already-migrated DBs).
CMD ["sh", "-c", "alembic upgrade head && gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000"]
