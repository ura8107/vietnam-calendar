# Phase 3 verification

Date: 2026-07-17. All provider tests were offline through `httpx.MockTransport`; no OpenAI or Ollama request was sent.

## Delivered

- `importance-rubric-v1` deterministic relevance/importance/must-include baseline and versioned prompt/rubric assets.
- Strict shared Pydantic contract for OpenAI Responses and Ollama `/api/chat` structured output.
- OpenAI `store=false`, bounded timeout, refusal/incomplete/schema/error classification, and no stateful response chaining.
- Provider selection defaults to disabled. `AI_AUTO_FALLBACK=false`; adapters never select or fall back to another provider.
- Article analysis durable job routing and `ai_runs` records with source IDs, hashes, versions, status/timestamps, latency, provider request ID and token counts when returned.
- Every provider failure leaves the article in `needs_review`; nothing is auto-published or discarded.
- Canonical 57-case JSONL SHA-256 verification and complete/±1/must/target/out-of-scope/schema metrics.
- Admin-protected provider health/capability and eval endpoints. Mutating endpoints require CSRF.
- Optional internal-only Compose Ollama profile.

## Offline verification

Command:

`UV_CACHE_DIR=/private/tmp/vietnam-calendar-uv-cache uv run pytest -q`

Result after the independent-audit hardening passes: **96 passed, 4 skipped**. The skips require an explicit PostgreSQL test database. The Phase 3 integration test is enabled with `PHASE3_TEST_DATABASE_URL`; it creates and cleans up its own uniquely identified Feed, Article, Job and AI runs, so it has no test-order or pre-existing-row dependency. It verifies durable success, `job_id`/`attempt_number` correlation, retry semantics, and preservation of a recent concurrent `started` run. Offline unit tests also cover both completion-race branches of durable enqueue deduplication.

Rule baseline (`evals/importance-v1.jsonl`, SHA-256 `0dc949488ce98698f77e5b8f2ba6c99652aa64517c5645db7efd775a65da86c6`):

- Exact: 89.47%
- Within one level: 98.25%
- Must-include recall: 100% (gate passed)
- Target recall: 100%
- Out-of-scope recall: 100%
- Schema success: 100% (deterministic rule output)

## Deliberate safety boundaries and remaining work

- Phase 3 stays human-reviewed. Successful and failed analyses both enter `needs_review`.
- No external failover occurs, regardless of fallback configuration, until an explicit future policy implements it.
- Cost remains nullable because pricing is not hard-coded. Raw provider responses are not retained; validated output and safe error metadata are retained.
- Real PostgreSQL integration was not run in this sandbox pass. Existing schema already contains `ai_runs`; a Compose/PostgreSQL verification is required before Phase 3 release acceptance.
- Provider-model quality must be evaluated separately from this deterministic baseline. The 57 development cases are not a blind holdout set.

### Provider health semantics

`GET /api/v1/ai/providers` reports configuration state without making network requests. A configured provider therefore returns `enabled=true`, `healthy=false`, and `detail="configured; reachability unknown"`; it must not be interpreted as an outage. Reachability plus strict-schema capability is established only by the CSRF-protected `POST /api/v1/ai/providers/{name}/test`. Both capability success and safe failure codes are persisted in the audit log. This separation avoids unexpected external traffic from status polling.

### Concurrency and durable jobs

Starting analysis locks the article row and expires only `started` AI runs older than twice the worker lease (minimum five minutes). Recent concurrent attempts are preserved. Eval and event-reanalysis endpoints use active-job deduplication with a completion-race retry and return the actual `created` value with HTTP 202.
