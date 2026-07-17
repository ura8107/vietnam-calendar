# Phase 0 API and version verification

Verified on 2026-07-16. This record distinguishes locally executed versions
from design targets that require later infrastructure.

## Executed locally

- CPython 3.13.9; project support range is Python 3.12–3.13.
- uv 0.11.28; `uv.lock` is the reproducible dependency record.
- Pydantic 2.12.4: `model_validate`, `model_validate_json`, and
  `model_json_schema` provide the shared strict result contract.
- feedparser 6.0.12: `feedparser.parse(bytes)` parses the synthetic RSS 2.0
  fixture. It does not parse Tuoi Tre's non-RFC `GMT+7` token directly; the
  adapter contract normalizes it to `+0700` before parsing while retaining raw
  bytes. The test verifies exact UTC, missing guid, and enclosure mapping.
- HTTPX 0.28.1: `MockTransport` verifies offline HTTP request/response shapes.
- pytest 8.4.2, pytest-asyncio 1.2.0, jsonschema 4.25.1.

## Fixed targets, not executed in Phase 0

- PostgreSQL 17+; SQLAlchemy 2 async and psycopg 3 async begin with Phase 1.
- Docker Compose with `depends_on.condition: service_healthy`; no Compose
  topology exists yet, so no Compose version was executed.
- FastAPI lifespan/Depends/path operations begin with the API phase.
- APScheduler 3.11.x `AsyncIOScheduler` begins with scheduler implementation.
- Node.js/UI: no version is selected or executed until the Phase 5 Vite setup.

## Provider contract scope

The OpenAI spike uses the documented Responses API shape: `/v1/responses`,
`text.format.type=json_schema`, `strict=true`, and `store=false`. The Ollama
spike uses native `/api/chat`, `format=<JSON Schema>`, and `stream=false`.
Both use HTTPX `MockTransport`; no credentials, provider SDK, model, server, or
network call was exercised. Live capability, refusal, rate-limit, usage, and
request-ID behavior remain Phase 3 integration work.

## Result semantics

The result first classifies relevance as `target`, `out_of_scope`, or
`uncertain`. Strict structured output keeps every property required, using
nullable target-stage fields. A target requires complete event/importance data
and evidence. Out-of-scope results require those fields to be null, evidence
and same-event candidates to be empty, and must-include to be false. Uncertain
results are either entirely empty at the event stage or contain a complete,
internally coherent tentative event with evidence; partial proposals are
rejected and must-include is always false. Categories and both certainty
dimensions use stable enums defined in the application contract.
