from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from vietnam_calendar.application.ai import (
    AIProvider,
    ArticleInput,
    EventCandidateInput,
    EventAnalysisRequest,
    EventAnalysisResult,
    ProviderHealth,
    event_analysis_json_schema,
    normalize_analysis_result,
)


ARTICLE_ID = UUID("11111111-1111-4111-8111-111111111111")
CANDIDATE_ID = UUID("22222222-2222-4222-8222-222222222222")


def request() -> EventAnalysisRequest:
    return EventAnalysisRequest(
        articles=[
            ArticleInput(
                article_id=ARTICLE_ID,
                title="Vietnam central bank changes its policy rate",
                publisher="Example publisher",
            )
        ]
    )


def valid_payload() -> dict[str, object]:
    return {
        "relevance": "target",
        "relevance_reason": "ベトナムの金融政策に直接関係する。",
        "event_title_ja": "中央銀行が政策金利を変更",
        "summary_ja": "中央銀行が政策金利の変更を正式決定した。",
        "event_date": "2026-07-16",
        "date_certainty": "confirmed",
        "category": "economy",
        "certainty": "confirmed",
        "importance_level": "high",
        "must_include_candidate": True,
        "importance_reason": "金融政策の中核決定である。",
        "evidence": [
            {"source_article_id": str(ARTICLE_ID), "rationale": "記事タイトルに明記"}
        ],
        "confidence": 0.9,
        "same_event_candidate_ids": [],
    }


class FakeProvider:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def analyze_event(self, analysis_request: EventAnalysisRequest) -> EventAnalysisResult:
        return normalize_analysis_result(self.payload, analysis_request)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(provider="fake", enabled=True, healthy=True, model="fixture")


@pytest.mark.asyncio
async def test_provider_protocol_and_mapping_normalization() -> None:
    provider = FakeProvider(valid_payload())
    assert isinstance(provider, AIProvider)
    result = await provider.analyze_event(request())
    assert result.importance_level.value == "high"
    assert result.evidence[0].source_article_id == ARTICLE_ID


def test_json_string_normalizes_to_same_domain_result() -> None:
    mapping_result = normalize_analysis_result(valid_payload(), request())
    json_result = normalize_analysis_result(json.dumps(valid_payload()), request())
    assert mapping_result == json_result


def test_schema_is_strict_and_contains_required_contract_fields() -> None:
    schema = event_analysis_json_schema()
    assert schema["additionalProperties"] is False
    assert {"relevance", "importance_level", "must_include_candidate", "evidence"} <= set(
        schema["required"]
    )
    assert set(schema["properties"]) == set(schema["required"])


def test_unknown_provider_field_is_rejected() -> None:
    payload = valid_payload() | {"provider_commentary": "should not be persisted"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        normalize_analysis_result(payload, request())


def test_invalid_enum_and_confidence_are_rejected() -> None:
    payload = valid_payload() | {"importance_level": "critical", "confidence": 1.1}
    with pytest.raises(ValidationError):
        normalize_analysis_result(payload, request())


def test_evidence_must_reference_request_article() -> None:
    payload = valid_payload()
    payload["evidence"] = [
        {"source_article_id": str(CANDIDATE_ID), "rationale": "invented reference"}
    ]
    with pytest.raises(ValueError, match="unknown article IDs"):
        normalize_analysis_result(payload, request())


def test_out_of_scope_cannot_be_must_include() -> None:
    payload = valid_payload() | {"relevance": "out_of_scope"}
    with pytest.raises(ValidationError, match="out_of_scope"):
        normalize_analysis_result(payload, request())


def test_out_of_scope_requires_null_event_stage_and_empty_evidence() -> None:
    payload = valid_payload() | {
        "relevance": "out_of_scope", "event_title_ja": None, "summary_ja": None,
        "event_date": None, "date_certainty": None, "category": None, "certainty": None,
        "importance_level": None, "must_include_candidate": False,
        "importance_reason": None, "evidence": [],
    }
    result = normalize_analysis_result(payload, request())
    assert result.importance_level is None

    with pytest.raises(ValidationError, match="same-event IDs"):
        normalize_analysis_result(
            payload | {"same_event_candidate_ids": [str(CANDIDATE_ID)]}, request()
        )


def test_uncertain_allows_empty_or_complete_stage_but_rejects_partial() -> None:
    null_stage = valid_payload() | {
        "relevance": "uncertain", "event_title_ja": None, "summary_ja": None,
        "event_date": None, "date_certainty": None, "category": None, "certainty": None,
        "importance_level": None, "must_include_candidate": False,
        "importance_reason": None, "evidence": [],
    }
    assert normalize_analysis_result(null_stage, request()).relevance.value == "uncertain"
    complete_stage = valid_payload() | {"relevance": "uncertain", "must_include_candidate": False}
    assert normalize_analysis_result(complete_stage, request()).importance_level.value == "high"
    with pytest.raises(ValidationError, match="empty or complete"):
        normalize_analysis_result(
            null_stage | {"importance_level": "middle", "importance_reason": "暫定"},
            request(),
        )
    with pytest.raises(ValidationError, match="uncertain"):
        normalize_analysis_result(null_stage | {"must_include_candidate": True}, request())


@pytest.mark.parametrize("field,value", [("confidence", "0.9"), ("confidence", True), ("must_include_candidate", 1)])
def test_provider_scalar_coercions_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        normalize_analysis_result(valid_payload() | {field: value}, request())


def test_duplicate_request_and_result_references_are_rejected() -> None:
    article = request().articles[0]
    with pytest.raises(ValidationError, match="article IDs must be unique"):
        EventAnalysisRequest(articles=[article, article])
    candidate = EventCandidateInput(
        event_id=CANDIDATE_ID, title_ja="候補", event_date="2026-07-16"
    )
    with pytest.raises(ValidationError, match="candidate IDs must be unique"):
        EventAnalysisRequest(articles=[article], existing_event_candidates=[candidate, candidate])
    duplicate_evidence = valid_payload()
    duplicate_evidence["evidence"] = duplicate_evidence["evidence"] * 2
    with pytest.raises(ValidationError, match="evidence article IDs must be unique"):
        normalize_analysis_result(duplicate_evidence, request())
    candidate_request = EventAnalysisRequest(
        articles=[article], existing_event_candidates=[candidate]
    )
    with pytest.raises(ValidationError, match="same-event candidate IDs must be unique"):
        normalize_analysis_result(
            valid_payload() | {"same_event_candidate_ids": [str(CANDIDATE_ID)] * 2},
            candidate_request,
        )
