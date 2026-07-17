# AGENTS.md

## Layout
- Root `Dockerfile` only serves `index.html`; the real backend lives under `backend/` and the Compose stack is defined in `compose.yaml`.
- `frontend/` is documentation-only for now; do not assume there is a frontend package or build pipeline.
- Backend entrypoints are `python -m vietnam_calendar.bootstrap`, `python -m vietnam_calendar.scheduler`, `python -m vietnam_calendar.worker`, and `uvicorn vietnam_calendar.api:app`.

## Commands
- Backend setup and test run from `backend/`: `uv sync --frozen --dev` then `uv run --frozen pytest`.
- Run a focused backend test from `backend/`, for example `uv run --frozen pytest tests/unit/test_health.py`.
- Local stack: `docker compose up --build`.
- Static root image: `docker build -t vietnam-calendar:local .` then `docker run --rm -p 8080:80 vietnam-calendar:local`.

## Workflow
- Compose starts `migrate` before `api`, `scheduler`, and `worker`; `migrate` runs `alembic upgrade head` and then bootstraps the admin user.
- Schema changes go through Alembic. Do not add application-startup `create_all()` paths.
- `readyz` checks both PostgreSQL connectivity and Alembic head; if it fails, check migrations first.

## Tests
- Backend tests are offline by default and use fixtures/mock transports.
- `backend/tests/integration/test_phase3_postgres.py` is skipped unless `PHASE3_TEST_DATABASE_URL` points at an explicit PostgreSQL test database.
- RSS tests rely on `backend/tests/fixtures/feeds/tuoitre-home.xml`; do not replace them with live network calls.

## Config Gotchas
- `Settings` reads `.env` and `.env.local` from the repo root.
- `ADMIN_PASSWORD_HASH` must be an Argon2id hash. If you place it in a Compose env file, escape literal `$` as `$$`.
- The default RSS allowlist is `news.tuoitre.vn`.
- `OPENAI_API_KEY` is only sent to `https://api.openai.com` unless `OPENAI_ALLOW_UNSAFE_BASE_URL` is set explicitly.
- Scheduler and worker are separate processes; the worker claims one durable PostgreSQL job at a time with `FOR UPDATE SKIP LOCKED`.
