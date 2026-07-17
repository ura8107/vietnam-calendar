# Phase 2 verification — RSS collection

Date: 2026-07-17 (Asia/Tokyo)

## Delivered

- HTTPX async streaming client with explicit connect/read/write/pool timeouts,
  connection limits, `trust_env=False`, TLS verification, and no automatic redirects.
- Credential-free HTTPS/443 allowlist and public IPv4/IPv6 DNS validation before
  the initial request and every redirect (maximum three redirects). A custom
  httpcore network backend pins the TCP target to one validated IP while the
  original hostname remains the HTTP Host and TLS SNI.
- A 90-second total deadline covers DNS, redirects, and streaming and is
  configuration-validated to remain shorter than the worker lease.
- 5 MiB decoded-body limit, conditional validators, 304 success, bounded
  `Retry-After`, and transient/permanent error classification.
- Bytes-only feedparser integration, Tuoi Tre `GMT+7` compatibility rewrite,
  partial-bozo warning policy, deterministic URL/text/date/hash/identity/image
  normalization, and plain-text extraction from descriptions.
- Atomic PostgreSQL article upsert and FetchRun provenance including compressed
  original response bytes, validators, hashes, counts, and safe errors.
- Durable job enqueue, partial-unique deduplication, `FOR UPDATE SKIP LOCKED`
  claim, per-claim ownership token, committed leases before network I/O,
  ownership-checked heartbeat, lease recovery, capped exponential retry, and
  terminal dead state. Exhausted feed jobs disable their feed.
- Heartbeat ownership loss and heartbeat database unavailability are distinct:
  definite loss leaves the new owner authoritative; database failure cancels
  work and schedules a retry without disabling the feed.
- Parser limits total entries, field lengths, and raw-entry JSON size. FetchRun
  counts distinguish total, accepted, and rejected entries. Content fingerprints
  cover every persisted source field so metadata-only changes are updated.
- Independent APScheduler 3.x UTC scheduler and worker entrypoints plus Compose
  services.
- Administrator/CSRF-protected manual fetch endpoint (HTTP 202), and protected
  job/fetch-run history endpoints.

## Exact verification commands and results

```text
env UV_CACHE_DIR=/tmp/vietnam-calendar-uv-cache uv lock
Resolved 52 packages; added apscheduler 3.11.1 and tzlocal 5.4.4.

PHASE2_TEST_DATABASE_URL=postgresql+psycopg://...@127.0.0.1:55433/vietnam_calendar \
  env UV_CACHE_DIR=/tmp/vietnam-calendar-uv-cache uv run --frozen --offline pytest -q
76 passed, 5 warnings in 0.99s

env UV_CACHE_DIR=/tmp/vietnam-calendar-uv-cache uv run --offline python -m compileall -q src tests
exit 0

PHASE2_TEST_DATABASE_URL=postgresql+psycopg://...@127.0.0.1:55433/vietnam_calendar \
  uv run --frozen --offline pytest tests/integration/test_phase2_postgres.py -q
3 passed, 4 warnings in 0.45s

DATABASE_URL=postgresql+psycopg://...@127.0.0.1:55433/vietnam_calendar \
  uv run --offline alembic check
No new upgrade operations detected.

docker compose ... up -d --build api scheduler worker
API, scheduler, and worker started.

docker compose ... ps
api healthy; db healthy; scheduler up; worker up.

docker compose ... exec -T api python -c <readyz request>
{"status":"ready","migration":"57ef74b846aa"}
```

The PostgreSQL integration test covers 200 insert, unchanged duplicate, content
and metadata-only updates, 304, distinct two-worker claims, heartbeat ownership,
stale-owner rejection, retry/dead lease recovery, unexpected/permanent failure
finalization, and terminal feed suppression. Network tests use HTTPX
`MockTransport`, a fake resolver, and a recording httpcore backend; no live
publisher or AI service is required.

## Phase 2 checklist

- [x] AC-01 foundation: scheduled and manual durable collection
- [x] AC-02 foundation: feed-local identity and idempotent upsert
- [x] ETag / Last-Modified request and response persistence
- [x] 304 recorded as successful not-modified run
- [x] Redirect destination revalidated on every hop
- [x] TCP target pinned to validated IP with original Host and TLS SNI preserved
- [x] Total DNS/redirect/stream deadline shorter than lease
- [x] Private, loopback, link-local, multicast, reserved, and unspecified IP rejected
- [x] Decoded response size bounded
- [x] 429 / Retry-After / timeout / 5xx retry policy
- [x] Invalid and partial-bozo feed policy
- [x] Tuoi Tre GMT+7, missing GUID, enclosure, tracking URL tests
- [x] PostgreSQL-only integration; no SQLite fallback
- [x] Queue lock released before network I/O
- [x] Ownership heartbeat and stale-worker succeed/fail rejection
- [x] Heartbeat DB failure retries without feed disable until max attempts;
      terminal heartbeat failure becomes dead and disables the feed
- [x] Exhausted lease becomes dead/finished and disables the feed
- [x] All post-run exceptions finalize FetchRun with sanitized errors
- [x] Accurate mixed-entry and entry-limit counters
- [x] All-invalid feeds retain total/rejected provenance on failed FetchRun
- [x] Metadata-only changes update content fingerprint and `updated_at`
- [x] Scheduler due-feed enqueue, dedupe, and next-fetch advancement
- [x] Concurrent same-key PostgreSQL enqueue creates one active job
- [x] Manual fetch requires authentication/CSRF and returns idempotent HTTP 202
- [x] Scheduler/worker Compose smoke
- [x] Exact dependency lock updated

## Documented APIs used

- HTTPX 0.28.1: `AsyncClient`, `AsyncBaseTransport`, `Timeout`, `Limits`,
  `stream`, `aiter_bytes`, `follow_redirects=False`, `trust_env=False`
- httpcore 1.0.9: public `AsyncConnectionPool(network_backend=...)`,
  `AsyncNetworkBackend`, `AnyIOBackend`, and `AsyncNetworkStream.start_tls`
  (`httpcore==1.0.9` is a direct locked dependency because these APIs are imported)
- feedparser 6.0.12: `feedparser.parse(bytes)`, `bozo`, `bozo_exception`, entries
- SQLAlchemy 2.0.44: async sessions, 2.0 `select`, PostgreSQL
  `insert().on_conflict_do_update()`, `with_for_update(skip_locked=True)`
- APScheduler 3.11.1: `AsyncIOScheduler`, `add_job`, `start`, `shutdown`
- FastAPI 0.121.3: typed path operations and dependencies

## Known gaps and follow-up

- `detected_language` is currently the source-specific `en` baseline, not a
  statistical detector.
- Live Tuoi Tre smoke was intentionally not performed; automated verification
  is deterministic and offline.
- An older local Phase 1 Docker volume had two enum columns recorded as varchar
  under the same Alembic revision. A fresh isolated volume produced the correct
  enum schema and `alembic check` passed. Operators with that old development
  volume should recreate or explicitly migrate it; production migration history
  must never be edited in place.
