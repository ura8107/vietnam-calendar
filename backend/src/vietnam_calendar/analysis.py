"""Durable article analysis orchestration; failures always remain reviewable."""
from __future__ import annotations
import hashlib,time,uuid
from datetime import UTC,datetime,timedelta
import httpx
from sqlalchemy import select,update
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker
from .application.ai import ArticleInput,EventAnalysisRequest
from .config import Settings
from .infrastructure.ai.providers import AIProviderError,OllamaProvider,OpenAIProvider
from .models import AIRun,AIRunStatus,Article,Feed,ProcessingStatus

PROMPT_VERSION="event-analysis-v1"; SCHEMA_VERSION="event-analysis-result-v1"; RULE_VERSION="importance-rubric-v1"

def build_provider(settings:Settings,*,transport:httpx.AsyncBaseTransport|None=None):
    timeout=httpx.Timeout(settings.ai_timeout_seconds)
    name=settings.ai_provider.lower()
    if name=="openai":
        client=httpx.AsyncClient(base_url=settings.openai_base_url,headers={"authorization":f"Bearer {settings.openai_api_key}"},timeout=timeout,transport=transport)
        return OpenAIProvider(client,settings.openai_model,bool(settings.openai_api_key and settings.openai_model))
    if name=="ollama":
        client=httpx.AsyncClient(base_url=settings.ollama_base_url,timeout=timeout,transport=transport)
        return OllamaProvider(client,settings.ollama_model,bool(settings.ollama_model))
    if name=="disabled":
        client=httpx.AsyncClient(base_url="http://disabled.invalid",timeout=timeout,transport=transport)
        return OllamaProvider(client,"",False)
    raise ValueError(f"unknown AI provider: {settings.ai_provider}")

async def analyze_article(factory:async_sessionmaker[AsyncSession],settings:Settings,article_id:uuid.UUID,*,job_id:uuid.UUID|None=None,retry_count:int=0)->None:
    async with factory() as session:
        article=(await session.scalars(select(Article).where(Article.id==article_id).with_for_update())).one_or_none()
        if article is None: raise AIProviderError("article_not_found","article not found")
        feed=await session.get(Feed,article.feed_id)
        # Only expire genuinely abandoned attempts. A row lock serializes starts
        # for this article, while the age predicate preserves healthy concurrent work.
        cutoff=datetime.now(UTC)-timedelta(seconds=max(settings.worker_lease_seconds*2,300))
        await session.execute(update(AIRun).where(AIRun.article_id==article.id,AIRun.status==AIRunStatus.started,AIRun.started_at<cutoff).values(status=AIRunStatus.failed,validation_errors=[{"code":"stale_started","message":"attempt exceeded stale deadline"}],finished_at=datetime.now(UTC)))
        request=EventAnalysisRequest(articles=[ArticleInput(article_id=article.id,title=article.title_normalized,summary=article.summary_text or "",publisher=feed.publisher if feed else "unknown",published_at=article.published_at)])
        serialized=request.model_dump_json(); provider=build_provider(settings); started=time.monotonic()
        run=AIRun(article_id=article.id,job_id=job_id,attempt_number=retry_count+1,provider=provider.name,base_url_identifier=provider.name,model=provider.model or "disabled",prompt_version=PROMPT_VERSION,schema_version=SCHEMA_VERSION,rule_version=RULE_VERSION,input_hash=hashlib.sha256(serialized.encode()).hexdigest(),source_article_ids=[str(article.id)],status=AIRunStatus.started,retry_count=retry_count)
        session.add(run); await session.commit()
    try:
        result=await provider.analyze_event(request)
    except Exception as caught:
        exc=caught if isinstance(caught,AIProviderError) else AIProviderError("unexpected_provider_error","unexpected AI provider failure",retryable=False)
        async with factory() as session:
            run=await session.get(AIRun,run.id); article=await session.get(Article,article_id)
            run.status=AIRunStatus.invalid if exc.code in {"schema_invalid","invalid_json","refusal","incomplete"} else AIRunStatus.failed
            run.validation_errors=[{"code":exc.code,"message":str(exc)}]; run.external_request_id=exc.request_id; run.latency_ms=int((time.monotonic()-started)*1000); run.finished_at=datetime.now(UTC)
            if article: article.processing_status=ProcessingStatus.needs_review
            await session.commit()
        raise
    else:
        async with factory() as session:
            run=await session.get(AIRun,run.id); article=await session.get(Article,article_id)
            run.status=AIRunStatus.succeeded; run.parsed_output=result.model_dump(mode="json"); run.latency_ms=int((time.monotonic()-started)*1000); run.finished_at=datetime.now(UTC)
            metadata=provider.last_metadata or {}; run.external_request_id=metadata.get("request_id"); run.input_tokens=metadata.get("input_tokens"); run.output_tokens=metadata.get("output_tokens")
            if article: article.processing_status=ProcessingStatus.needs_review
            await session.commit()
    finally: await provider.client.aclose()
