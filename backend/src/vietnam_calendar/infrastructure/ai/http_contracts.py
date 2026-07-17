"""Non-production minimum HTTP contracts for Phase 0 capability spikes.

These helpers deliberately contain no retry, authentication, or provider
selection policy. Production adapters are implemented in Phase 3.
"""

from __future__ import annotations

from typing import Any

import httpx

from vietnam_calendar.application.ai import (
    EventAnalysisRequest,
    EventAnalysisResult,
    event_analysis_json_schema,
    normalize_analysis_result,
)


async def openai_responses_spike(
    client: httpx.AsyncClient,
    request: EventAnalysisRequest,
    *,
    model: str,
) -> EventAnalysisResult:
    response = await client.post(
        "/v1/responses",
        json={
            "model": model,
            "store": False,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": request.model_dump_json()}]}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "event_analysis_result",
                    "strict": True,
                    "schema": event_analysis_json_schema(),
                }
            },
        },
    )
    response.raise_for_status()
    body = response.json()
    text = body["output"][0]["content"][0]["text"]
    return normalize_analysis_result(text, request)


async def ollama_chat_spike(
    client: httpx.AsyncClient,
    request: EventAnalysisRequest,
    *,
    model: str,
) -> EventAnalysisResult:
    response = await client.post(
        "/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": request.model_dump_json()}],
            "format": event_analysis_json_schema(),
        },
    )
    response.raise_for_status()
    return normalize_analysis_result(response.json()["message"]["content"], request)
