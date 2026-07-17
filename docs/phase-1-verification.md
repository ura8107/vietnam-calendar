# Phase 1 verification

Phase 1 implements the backend and database foundation only. RSS fetching,
scheduler, worker, event classification, and UI are explicitly deferred.

Because Phase 1 remains uncommitted and local-only, its initial migration was
regenerated in place when the §6 schema was expanded. No deployed migration
history exists to preserve; future schema changes must use additive revisions.

## Security choices

- Administrator passwords use Argon2id (`argon2-cffi==25.1.0`).
- The database stores only a SHA-256 digest of each 256-bit random session and
  CSRF token. The raw session token exists only in an HttpOnly,
  SameSite=Strict cookie.
- Logout requires the separately returned CSRF token.
- Compose exposes only the API and binds it to loopback.
- Alembic exclusively owns schema creation and changes.

## Commands

Offline suite:

```sh
cd backend
UV_CACHE_DIR=/tmp/vc-uv-cache uv sync --frozen --dev
UV_CACHE_DIR=/tmp/vc-uv-cache uv run --frozen pytest
```

Real PostgreSQL migration round trip:

```sh
POSTGRES_PASSWORD=phase1-test-password ADMIN_PASSWORD_HASH='<argon2id hash>' docker compose up -d db
POSTGRES_PASSWORD=phase1-test-password ADMIN_PASSWORD_HASH='<argon2id hash>' docker compose run --rm migrate alembic upgrade head
POSTGRES_PASSWORD=phase1-test-password ADMIN_PASSWORD_HASH='<argon2id hash>' docker compose run --rm migrate alembic downgrade base
POSTGRES_PASSWORD=phase1-test-password ADMIN_PASSWORD_HASH='<argon2id hash>' docker compose run --rm migrate alembic upgrade head
```

Use disposable credentials and a disposable volume for verification.

The final PostgreSQL 17 verification completed successfully on 2026-07-16
using an isolated empty `vc-phase1-final_postgres-data` volume:
revision `57ef74b846aa` upgraded from base, downgraded to base, and upgraded
again; `alembic check` then reported `No new upgrade operations detected`.
The frozen offline suite completed with `35 passed`.
