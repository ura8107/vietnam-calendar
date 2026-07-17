from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest

from vietnam_calendar.application.ai import ArticleInput, EventAnalysisRequest
from vietnam_calendar.infrastructure.ai.http_contracts import (
    ollama_chat_spike,
    openai_responses_spike,
)


ARTICLE_ID = UUID("11111111-1111-4111-8111-111111111111")


def analysis_request() -> EventAnalysisRequest:
    return EventAnalysisRequest(
        articles=[ArticleInput(article_id=ARTICLE_ID, title="Policy rate changed", publisher="Fixture")]
    )


def result_json() -> str:
    return json.dumps({
        "relevance": "target", "relevance_reason": "Vietnam policy", "event_title_ja": "政策金利変更",
        "summary_ja": "中央銀行が政策金利を変更した。", "event_date": "2026-07-16",
        "date_certainty": "confirmed", "category": "economy", "certainty": "confirmed",
        "importance_level": "high", "must_include_candidate": True,
        "importance_reason": "金融政策の中核決定。",
        "evidence": [{"source_article_id": str(ARTICLE_ID), "rationale": "title"}],
        "confidence": 0.9, "same_event_candidate_ids": []
    })


@pytest.mark.asyncio
async def test_openai_and_ollama_documented_shapes_normalize_identically() -> None:
    seen: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen[request.url.path] = payload
        if request.url.path == "/v1/responses":
            return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": result_json()}]}]})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": result_json()}, "done": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://offline.test") as client:
        openai = await openai_responses_spike(client, analysis_request(), model="configured-openai")
        ollama = await ollama_chat_spike(client, analysis_request(), model="configured-ollama")

    assert openai == ollama
    assert seen["/v1/responses"]["store"] is False
    assert seen["/v1/responses"]["text"]["format"]["strict"] is True
    assert seen["/api/chat"]["stream"] is False
    assert seen["/api/chat"]["format"]["type"] == "object"
