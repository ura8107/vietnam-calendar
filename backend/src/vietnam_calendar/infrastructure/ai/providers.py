"""Production, stateless AI adapters. No provider performs implicit fallback."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from vietnam_calendar.application.ai import EventAnalysisRequest, EventAnalysisResult, ProviderHealth, event_analysis_json_schema, normalize_analysis_result, ollama_event_analysis_json_schema
from vietnam_calendar.application.assets import load_ai_assets


class AIProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, retry_after: int | None = None, request_id: str | None = None):
        super().__init__(message); self.code=code; self.retryable=retryable; self.retry_after=retry_after; self.request_id=request_id


def _retry_after(response: httpx.Response) -> int | None:
    try: return min(3600, max(0, int(response.headers.get("retry-after", ""))))
    except ValueError: return None


def _raise_http(response: httpx.Response) -> None:
    if response.status_code < 400: return
    retryable = response.status_code == 429 or response.status_code >= 500
    raise AIProviderError(f"http_{response.status_code}", "AI provider request failed", retryable=retryable, retry_after=_retry_after(response), request_id=response.headers.get("x-request-id"))


def _prompt(request: EventAnalysisRequest) -> str:
    # Only the normalized RSS title/summary and public metadata enter providers.
    return json.dumps({"articles":[{"article_id":str(a.article_id),"title":a.title,"summary":a.summary,"publisher":a.publisher,"published_at":a.published_at.isoformat() if a.published_at else None} for a in request.articles],"existing_event_candidates":[c.model_dump(mode="json") for c in request.existing_event_candidates],"importance_rubric_version":request.importance_rubric_version,"output_language":"ja"}, ensure_ascii=False, separators=(",",":"))

def _instructions()->str:
    rubric,prompt,_=load_ai_assets(); return prompt+"\n\n"+rubric


@dataclass
class OpenAIProvider:
    client: httpx.AsyncClient; model: str; enabled: bool
    name: str = "openai"
    last_metadata: dict[str, Any] | None = None

    async def analyze_event(self, request: EventAnalysisRequest) -> EventAnalysisResult:
        if not self.enabled: raise AIProviderError("provider_disabled", "OpenAI provider is disabled")
        try:
            response=await self.client.post("/v1/responses",json={"model":self.model,"store":False,"input":[{"role":"developer","content":[{"type":"input_text","text":_instructions()}]},{"role":"user","content":[{"type":"input_text","text":_prompt(request)}]}],"text":{"format":{"type":"json_schema","name":"event_analysis_result","strict":True,"schema":event_analysis_json_schema()}}})
        except httpx.TimeoutException as exc: raise AIProviderError("timeout","AI provider timed out",retryable=True) from exc
        except httpx.RequestError as exc: raise AIProviderError("transport_error","AI provider transport failed",retryable=True) from exc
        _raise_http(response)
        try: body=response.json()
        except (ValueError,json.JSONDecodeError) as exc: raise AIProviderError("invalid_json","AI provider returned invalid JSON") from exc
        if not isinstance(body,dict): raise AIProviderError("schema_invalid","AI provider response must be an object")
        usage=body.get("usage") or {}
        if not isinstance(usage,dict): raise AIProviderError("schema_invalid","AI provider usage must be an object")
        self.last_metadata={"request_id":body.get("id") or response.headers.get("x-request-id"),"input_tokens":usage.get("input_tokens"),"output_tokens":usage.get("output_tokens")}
        if body.get("status") == "incomplete": raise AIProviderError("incomplete","AI provider returned incomplete output",retryable=True,request_id=body.get("id"))
        try:
            outputs=body.get("output")
            if not isinstance(outputs,list) or not all(isinstance(o,dict) for o in outputs): raise AIProviderError("schema_invalid","AI provider output must be a list of objects")
            groups=[o.get("content") for o in outputs]
            if not all(isinstance(g,list) and all(isinstance(c,dict) for c in g) for g in groups): raise AIProviderError("schema_invalid","AI provider content must be lists of objects")
            content=[c for group in groups for c in group]
            if any(c.get("type")=="refusal" for c in content): raise AIProviderError("refusal","AI provider refused the request",request_id=body.get("id"))
            texts=[c["text"] for c in content if c.get("type")=="output_text" and isinstance(c.get("text"),str)]
            if len(texts)!=1: raise AIProviderError("schema_invalid","AI provider must return exactly one output_text")
            return normalize_analysis_result(texts[0],request)
        except AIProviderError: raise
        except (KeyError,IndexError,TypeError,ValueError,ValidationError) as exc: raise AIProviderError("schema_invalid","AI provider output failed validation") from exc

    async def health(self)->ProviderHealth:
        return ProviderHealth(provider=self.name,enabled=self.enabled,healthy=False,model=self.model or None,detail="configured; reachability unknown" if self.enabled else "disabled or model not configured")


@dataclass
class OllamaProvider:
    client: httpx.AsyncClient; model: str; enabled: bool
    name: str = "ollama"
    last_metadata: dict[str, Any] | None = None
    async def analyze_event(self,request:EventAnalysisRequest)->EventAnalysisResult:
        if not self.enabled: raise AIProviderError("provider_disabled","Ollama provider is disabled")
        try: response=await self.client.post("/api/chat",json={"model":self.model,"stream":False,"messages":[{"role":"system","content":_instructions()},{"role":"user","content":_prompt(request)}],"format":ollama_event_analysis_json_schema(),"options":{"temperature":0}})
        except httpx.TimeoutException as exc: raise AIProviderError("timeout","AI provider timed out",retryable=True) from exc
        except httpx.RequestError as exc: raise AIProviderError("transport_error","AI provider transport failed",retryable=True) from exc
        _raise_http(response)
        try: body=response.json()
        except (ValueError,json.JSONDecodeError) as exc: raise AIProviderError("invalid_json","AI provider returned invalid JSON") from exc
        if not isinstance(body,dict): raise AIProviderError("schema_invalid","AI provider response must be an object")
        if body.get("done") is False or body.get("done_reason") in {"load","unload","length"}: raise AIProviderError("incomplete","Ollama returned incomplete output",retryable=True)
        self.last_metadata={"request_id":response.headers.get("x-request-id"),"input_tokens":body.get("prompt_eval_count"),"output_tokens":body.get("eval_count")}
        try: return normalize_analysis_result(body["message"]["content"],request)
        except (KeyError,TypeError,ValueError,ValidationError) as exc: raise AIProviderError("schema_invalid","AI provider output failed validation") from exc
    async def health(self)->ProviderHealth:
        return ProviderHealth(provider=self.name,enabled=self.enabled,healthy=False,model=self.model or None,detail="configured; reachability unknown" if self.enabled else "disabled or model not configured")
