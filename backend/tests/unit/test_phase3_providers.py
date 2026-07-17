from __future__ import annotations
import json
from uuid import UUID
import httpx,pytest
from vietnam_calendar.application.ai import ArticleInput,EventAnalysisRequest
from vietnam_calendar.infrastructure.ai.providers import AIProviderError,OpenAIProvider,OllamaProvider
from vietnam_calendar.analysis import build_provider
from vietnam_calendar.config import Settings
from vietnam_calendar.application.assets import load_ai_assets

ID=UUID("11111111-1111-4111-8111-111111111111")
def req(): return EventAnalysisRequest(articles=[ArticleInput(article_id=ID,title="Vietnam central bank changes policy rate",publisher="Fixture")])
def output(): return json.dumps({"relevance":"target","relevance_reason":"Vietnam","event_title_ja":"政策金利変更","summary_ja":"中央銀行が政策金利を変更した。","event_date":"2026-07-17","date_certainty":"confirmed","category":"economy","certainty":"confirmed","importance_level":"high","must_include_candidate":True,"importance_reason":"中核決定","evidence":[{"source_article_id":str(ID),"rationale":"title"}],"confidence":0.9,"same_event_candidate_ids":[]})

@pytest.mark.asyncio
async def test_openai_store_false_and_ollama_exact_schema():
    seen={}
    def handler(r):
        seen[r.url.path]=json.loads(r.content)
        if r.url.path=="/v1/responses": return httpx.Response(200,json={"id":"resp_1","output":[{"content":[{"type":"output_text","text":output()}]}]})
        return httpx.Response(200,json={"message":{"content":output()}})
    t=httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="https://offline",transport=t) as c: assert (await OpenAIProvider(c,"model",True).analyze_event(req())).must_include_candidate
    async with httpx.AsyncClient(base_url="http://offline",transport=t) as c: assert (await OllamaProvider(c,"model",True).analyze_event(req())).must_include_candidate
    assert seen["/v1/responses"]["store"] is False
    assert seen["/api/chat"]["format"]["additionalProperties"] is False

@pytest.mark.asyncio
@pytest.mark.parametrize("provider",["openai","ollama"])
async def test_disabled_never_calls_transport(provider):
    def forbidden(r): raise AssertionError("network called")
    async with httpx.AsyncClient(base_url="http://offline",transport=httpx.MockTransport(forbidden)) as c:
        p=OpenAIProvider(c,"",False) if provider=="openai" else OllamaProvider(c,"",False)
        with pytest.raises(AIProviderError,match="disabled"): await p.analyze_event(req())

@pytest.mark.asyncio
@pytest.mark.parametrize("status",[429,500,503])
async def test_http_transient_is_safe_and_retryable(status):
    async with httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(lambda r:httpx.Response(status,headers={"retry-after":"3"}))) as c:
        with pytest.raises(AIProviderError) as e: await OpenAIProvider(c,"m",True).analyze_event(req())
    assert e.value.retryable and (e.value.retry_after==3 if status==429 else True)

@pytest.mark.asyncio
async def test_refusal_and_malformed_are_invalid():
    replies=[{"id":"r","output":[{"content":[{"type":"refusal","refusal":"no"}]}]},{"output":[]}]
    for body in replies:
        async with httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(lambda r,b=body:httpx.Response(200,json=b))) as c:
            with pytest.raises(AIProviderError) as e: await OpenAIProvider(c,"m",True).analyze_event(req())
            assert e.value.code in {"refusal","schema_invalid"}

@pytest.mark.asyncio
async def test_timeout_is_retryable():
    def timeout(r): raise httpx.ReadTimeout("offline timeout",request=r)
    async with httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(timeout)) as c:
        with pytest.raises(AIProviderError) as e: await OpenAIProvider(c,"m",True).analyze_event(req())
    assert e.value.code=="timeout" and e.value.retryable

def test_provider_selection_has_no_hidden_fallback():
    p=build_provider(Settings(ai_provider="disabled",ai_fallback_provider="openai",ai_auto_fallback=False),transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("network"))))
    assert p.enabled is False and p.name=="ollama"

def test_assets_are_versioned_cached_and_identical_for_both_adapters():
    rubric,prompt,digest=load_ai_assets(); assert "Importance rubric v1" in rubric and "untrusted" in prompt and len(digest)==64
    assert load_ai_assets() is load_ai_assets()

def test_unknown_provider_and_openai_key_host_are_rejected():
    with pytest.raises(ValueError,match="only be sent"): Settings(openai_api_key="redacted",openai_base_url="http://evil.invalid")
    with pytest.raises(ValueError,match="unknown AI provider"): build_provider(Settings(ai_provider="typo"))

@pytest.mark.asyncio
async def test_transport_and_invalid_json_are_normalized():
    def transport(r): raise httpx.ConnectError("offline",request=r)
    async with httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(transport)) as c:
        with pytest.raises(AIProviderError) as e: await OpenAIProvider(c,"m",True).analyze_event(req())
        assert e.value.code=="transport_error" and e.value.retryable
    async with httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(lambda r:httpx.Response(200,text="not-json"))) as c:
        with pytest.raises(AIProviderError) as e: await OpenAIProvider(c,"m",True).analyze_event(req())
        assert e.value.code=="invalid_json"

@pytest.mark.asyncio
async def test_responses_scans_all_output_and_requires_unique_text():
    body={"output":[{"content":[{"type":"output_text","text":output()}]},{"content":[{"type":"refusal","refusal":"no"}]}]}
    async with httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))) as c:
        with pytest.raises(AIProviderError) as e: await OpenAIProvider(c,"m",True).analyze_event(req())
        assert e.value.code=="refusal"
    body={"output":[{"content":[{"type":"output_text","text":output()},{"type":"output_text","text":output()}]}]}
    async with httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))) as c:
        with pytest.raises(AIProviderError) as e: await OpenAIProvider(c,"m",True).analyze_event(req())
        assert e.value.code=="schema_invalid"

@pytest.mark.asyncio
@pytest.mark.parametrize("body",[[],{"output":{}},{"output":[1]},{"output":[{"content":{}}]},{"output":[{"content":[1]}]}])
async def test_all_malformed_response_container_shapes_are_normalized(body):
    async with httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))) as c:
        with pytest.raises(AIProviderError) as e: await OpenAIProvider(c,"m",True).analyze_event(req())
        assert e.value.code=="schema_invalid"

@pytest.mark.asyncio
@pytest.mark.parametrize("body",[{"done":False,"message":{"content":output()}},{"done":True,"done_reason":"length","message":{"content":output()}}])
async def test_ollama_incomplete_is_retryable(body):
    async with httpx.AsyncClient(base_url="http://offline",transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))) as c:
        with pytest.raises(AIProviderError) as e: await OllamaProvider(c,"m",True).analyze_event(req())
        assert e.value.code=="incomplete" and e.value.retryable
