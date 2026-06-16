# TrackBit API

The backend for **TrackBit** — simple, stress-free task management for small teams.
FastAPI + SQLAlchemy + PostgreSQL.

> New to the project? This guide takes you from a fresh clone to a running API.
> If anything is unclear, ask your team lead — and feel free to improve this file.

---

## What's in here

- **FastAPI** web framework, served by **uvicorn**
- **SQLAlchemy 2** + **Alembic** for the database and migrations
- **PostgreSQL** (we use a managed Aiven database; a local Postgres also works)
- **uv** for Python dependency management
- Auth (JWT), background jobs (APScheduler), email (Resend), web push (VAPID)

---

## Prerequisites

1. **Python 3.12** (the project pins it in `.python-version`).
2. **uv** — the package manager we use. Install it once:
   - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows (PowerShell): `irm https://astral.sh/uv/install.ps1 | iex`
   - Docs: https://docs.astral.sh/uv/
3. **Database access** — your team lead will give you the `DATABASE_URL` and
   `ADMIN_DATABASE_URL` values (and any other keys) to paste into your `.env`.

You do **not** need to install Python or create a virtualenv by hand — `uv` does both.

---

## Setup (first time)

```bash
# 1. Clone and enter the repo
git clone <repo-url> trackbit_api
cd trackbit_api

# 2. Install dependencies (creates a .venv automatically, incl. dev tools)
uv sync --extra dev

# 3. Create your environment file from the template
cp .env.example .env                 # macOS / Linux
# Copy-Item .env.example .env        # Windows PowerShell

# 4. Open .env and fill in the values your team lead shared:
#    - DATABASE_URL and ADMIN_DATABASE_URL (required)
#    - JWT_SECRET_KEY (generate your own — see the comment in .env.example)
#    Optional keys (email/push/billing/storage) can stay empty for local dev.

# 5. Apply database migrations (creates/updates all tables)
uv run alembic upgrade head

# 6. (Optional) load demo data — a sample org, board, and tasks
uv run python -m scripts.seed
```

> The seed creates a demo admin you can log in with from the web app:
> **kc@demo.trackbit.app** / **demo1234**

---

## Running the API

```bash
uv run uvicorn app.main:app --port 8000
```

- API base URL: `http://localhost:8000/api/v1`
- Interactive docs (Swagger): `http://localhost:8000/docs`

> ⚠️ We run **without** `--reload`, so the server does **not** auto-restart when
> you change code. After editing backend files, stop the server (Ctrl+C) and
> start it again. If you ever see "address already in use", a stale server is
> still on port 8000 — stop it first:
> - macOS / Linux: `lsof -ti:8000 | xargs kill`
> - Windows (PowerShell): `Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`

The frontend lives in a separate repo (**trackbit_web**) — start this API first,
then start the web app and point its `NEXT_PUBLIC_API_BASE_URL` at this server.

---

## Everyday commands

```bash
uv run pytest -q                         # run the test suite
uv run ruff check app tests              # lint
uv run ruff check app tests --fix        # lint + auto-fix
uv run alembic upgrade head              # apply migrations
uv run alembic revision -m "describe it" # create a new (empty) migration
```

> Migrations run against a **shared** database. Don't invent your own schema
> changes against the team DB without coordinating — ask first.

---

## Project layout

```
app/
  main.py            # FastAPI app entry point
  api/v1/endpoints/  # HTTP routes (auth, boards, tasks, me, ...)
  services/          # business logic (the real work lives here)
  models/            # SQLAlchemy tables
  schemas/           # Pydantic request/response shapes
  core/              # config, database, security, RLS, scheduler
alembic/             # database migrations
scripts/seed.py      # demo data loader
tests/               # pytest suite
```

---

## Troubleshooting

- **`column ... does not exist` / table errors** → run `uv run alembic upgrade head`.
- **Can't connect to the database** → check `DATABASE_URL` in `.env` (host,
  password, and `?sslmode=require` for Aiven). Ask your lead for fresh creds.
- **Login works but emails never arrive** → expected in dev (`RESEND_API_KEY`
  empty). Emails are printed to the server console instead.
- **Changes not taking effect** → restart the server (no auto-reload; see above).
