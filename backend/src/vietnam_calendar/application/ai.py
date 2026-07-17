"""Provider-neutral contracts for event analysis.

Provider adapters must turn their SDK response into JSON-compatible data and
pass it through :func:`normalize_analysis_result` before returning it.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Relevance(StrEnum):
    TARGET = "target"
    OUT_OF_SCOPE = "out_of_scope"
    UNCERTAIN = "uncertain"


class ImportanceLevel(StrEnum):
    LOW = "low"
    MIDDLE = "middle"
    MIDDLE_HIGH = "middle_high"
    HIGH = "high"


class Category(StrEnum):
    POLITICS = "politics"
    ECONOMY = "economy"
    DIPLOMACY = "diplomacy"
    BUSINESS = "business"
    DISASTER = "disaster"
    SOCIETY = "society"
    HEALTH = "health"
    CULTURE = "culture"
    SCIENCE = "science"
    SPORTS = "sports"
    INFRASTRUCTURE = "infrastructure"
    ENVIRONMENT = "environment"
    TRANSPORT = "transport"
    OTHER = "other"


class DateCertainty(StrEnum):
    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    PUBLISHED_FALLBACK = "published_fallback"


class EventCertainty(StrEnum):
    CONFIRMED = "confirmed"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    PLANNED = "planned"
    SPECULATIVE = "speculative"


class ArticleInput(StrictModel):
    article_id: UUID
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=5_000)
    publisher: str = Field(min_length=1, max_length=200)
    published_at: datetime | None = None


class EventCandidateInput(StrictModel):
    event_id: UUID
    title_ja: str = Field(min_length=1, max_length=300)
    event_date: date


class EventAnalysisRequest(StrictModel):
    articles: list[ArticleInput] = Field(min_length=1, max_length=20)
    existing_event_candidates: list[EventCandidateInput] = Field(
        default_factory=list, max_length=20
    )
    importance_rubric_version: str = Field(
        default="importance-rubric-v1", pattern=r"^[a-z0-9-]+$"
    )
    output_language: str = Field(default="ja", pattern="^ja$")

    @model_validator(mode="after")
    def ids_are_unique(self) -> EventAnalysisRequest:
        article_ids = [item.article_id for item in self.articles]
        if len(article_ids) != len(set(article_ids)):
            raise ValueError("article IDs must be unique")
        candidate_ids = [item.event_id for item in self.existing_event_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique")
        return self


class Evidence(StrictModel):
    source_article_id: UUID
    rationale: str = Field(min_length=1, max_length=500)


class EventAnalysisResult(StrictModel):
    relevance: Relevance
    relevance_reason: str = Field(min_length=1, max_length=1_000)
    event_title_ja: str | None = Field(min_length=1, max_length=300)
    summary_ja: str | None = Field(min_length=1, max_length=2_000)
    event_date: date | None
    date_certainty: DateCertainty | None
    category: Category | None
    certainty: EventCertainty | None
    importance_level: ImportanceLevel | None
    must_include_candidate: StrictBool
    importance_reason: str | None = Field(min_length=1, max_length=1_000)
    evidence: list[Evidence] = Field(max_length=20)
    confidence: StrictFloat = Field(ge=0, le=1)
    # Required even when empty: strict structured-output schemas require every
    # property in `required` and represent optionality through nullable types.
    same_event_candidate_ids: list[UUID] = Field(max_length=20)

    @model_validator(mode="after")
    def enforce_two_stage_semantics(self) -> EventAnalysisResult:
        target_fields = {
            "event_title_ja": self.event_title_ja,
            "summary_ja": self.summary_ja,
            "event_date": self.event_date,
            "date_certainty": self.date_certainty,
            "category": self.category,
            "certainty": self.certainty,
            "importance_level": self.importance_level,
            "importance_reason": self.importance_reason,
        }
        if self.relevance is Relevance.TARGET:
            missing = [name for name, value in target_fields.items() if value is None]
            if missing or not self.evidence:
                raise ValueError(f"target result requires event fields and evidence: {missing}")
        elif self.relevance is Relevance.OUT_OF_SCOPE:
            populated = [name for name, value in target_fields.items() if value is not None]
            if populated or self.evidence or self.same_event_candidate_ids or self.must_include_candidate:
                raise ValueError(
                    "out_of_scope result requires null event fields, empty evidence/"
                    "same-event IDs, and must_include=false"
                )
        else:
            if self.must_include_candidate:
                raise ValueError("uncertain result cannot be a must-include candidate")
            populated = [name for name, value in target_fields.items() if value is not None]
            empty_stage = not populated and not self.evidence and not self.same_event_candidate_ids
            complete_stage = len(populated) == len(target_fields) and bool(self.evidence)
            if not (empty_stage or complete_stage):
                raise ValueError(
                    "uncertain result must have either an empty or complete tentative event stage"
                )
        return self

    @field_validator("evidence")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[Evidence]) -> list[Evidence]:
        ids = [item.source_article_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence article IDs must be unique")
        return value

    @field_validator("same_event_candidate_ids")
    @classmethod
    def same_event_ids_are_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("same-event candidate IDs must be unique")
        return value

    def validate_references(self, request: EventAnalysisRequest) -> EventAnalysisResult:
        article_ids = {article.article_id for article in request.articles}
        evidence_ids = {item.source_article_id for item in self.evidence}
        unknown_articles = evidence_ids - article_ids
        if unknown_articles:
            raise ValueError(f"evidence references unknown article IDs: {unknown_articles}")

        candidate_ids = {item.event_id for item in request.existing_event_candidates}
        unknown_candidates = set(self.same_event_candidate_ids) - candidate_ids
        if unknown_candidates:
            raise ValueError(
                f"same-event references unknown candidate IDs: {unknown_candidates}"
            )
        return self


class ProviderHealth(StrictModel):
    provider: str = Field(min_length=1, max_length=50)
    enabled: bool
    healthy: bool
    model: str | None = Field(default=None, max_length=200)
    detail: str | None = Field(default=None, max_length=500)


@runtime_checkable
class AIProvider(Protocol):
    async def analyze_event(
        self, request: EventAnalysisRequest
    ) -> EventAnalysisResult: ...

    async def health(self) -> ProviderHealth: ...


def normalize_analysis_result(
    payload: str | bytes | Mapping[str, Any], request: EventAnalysisRequest
) -> EventAnalysisResult:
    """Strictly normalize a provider payload and verify input-bound references."""

    if isinstance(payload, (str, bytes)):
        result = EventAnalysisResult.model_validate_json(payload)
    else:
        result = EventAnalysisResult.model_validate(payload)
    return result.validate_references(request)


def event_analysis_json_schema() -> dict[str, Any]:
    """Return the single JSON Schema used by all structured-output adapters."""

    return EventAnalysisResult.model_json_schema()
