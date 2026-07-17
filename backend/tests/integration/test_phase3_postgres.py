import os,json,uuid
from datetime import UTC,datetime
import pytest,httpx
from sqlalchemy import delete,select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
import vietnam_calendar.analysis as analysis
from vietnam_calendar.analysis import analyze_article
from vietnam_calendar.config import Settings
from vietnam_calendar.infrastructure.ai.providers import OpenAIProvider
from vietnam_calendar.models import AIRun,AIRunStatus,Article,DateSource,Feed,Job,JobStatus,JobType,ProcessingStatus

pytestmark=pytest.mark.skipif(not os.getenv("PHASE3_TEST_DATABASE_URL"),reason="requires explicit PostgreSQL test database")

@pytest.mark.asyncio
async def test_phase3_analysis_is_order_independent_and_job_correlated(monkeypatch):
    engine=create_async_engine(os.environ["PHASE3_TEST_DATABASE_URL"]); factory=async_sessionmaker(engine,expire_on_commit=False)
    marker=uuid.uuid4().hex; feed_id=uuid.uuid4(); article_id=uuid.uuid4(); job_id=uuid.uuid4()
    try:
        async with factory() as s:
            s.add(Feed(id=feed_id,name=marker,url=f"https://news.tuoitre.vn/{marker}.rss",normalized_url=f"https://news.tuoitre.vn/{marker}.rss",publisher="Fixture",declared_language="en",default_category=None,trust_score=80,enabled=True,fetch_interval_minutes=30,next_fetch_at=datetime.now(UTC),consecutive_failures=0))
            s.add(Job(id=job_id,job_type=JobType.analyze_article,dedupe_key=f"phase3:{marker}",payload={"article_id":str(article_id)},status=JobStatus.running,priority=0,attempts=2,max_attempts=3,run_after=datetime.now(UTC),locked_by="test",locked_at=datetime.now(UTC),lease_expires_at=datetime.now(UTC),started_at=datetime.now(UTC)))
            s.add(Article(id=article_id,feed_id=feed_id,source_guid=marker,source_url=f"https://news.tuoitre.vn/{marker}",normalized_url=f"https://news.tuoitre.vn/{marker}",identity_key=marker,title_raw="Vietnam central bank changes policy rate",summary_raw="Confirmed",title_normalized="Vietnam central bank changes policy rate",summary_text="Confirmed",published_at=datetime.now(UTC),fetched_at=datetime.now(UTC),date_source=DateSource.published,content_hash="a"*64,raw_entry={},processing_status=ProcessingStatus.normalized))
            recent=AIRun(article_id=article_id,job_id=job_id,attempt_number=1,provider="fixture",model="m",prompt_version="p",schema_version="s",rule_version="r",input_hash="1"*64,source_article_ids=[str(article_id)],status=AIRunStatus.started,retry_count=0,started_at=datetime.now(UTC)); s.add(recent); await s.commit(); recent_id=recent.id
        payload={"relevance":"target","relevance_reason":"Vietnam","event_title_ja":"政策金利変更","summary_ja":"正式決定","event_date":"2026-07-17","date_certainty":"confirmed","category":"economy","certainty":"confirmed","importance_level":"high","must_include_candidate":True,"importance_reason":"中核決定","evidence":[{"source_article_id":str(article_id),"rationale":"title"}],"confidence":0.9,"same_event_candidate_ids":[]}
        client=httpx.AsyncClient(base_url="https://offline",transport=httpx.MockTransport(lambda r:httpx.Response(200,json={"id":"r","output":[{"content":[{"type":"output_text","text":json.dumps(payload)}]}]})))
        monkeypatch.setattr(analysis,"build_provider",lambda settings:OpenAIProvider(client,"m",True))
        await analyze_article(factory,Settings(_env_file=None),article_id,job_id=job_id,retry_count=1)
        async with factory() as s:
            assert (await s.get(AIRun,recent_id)).status==AIRunStatus.started
            assert (await s.get(Article,article_id)).processing_status==ProcessingStatus.needs_review
            run=(await s.scalars(select(AIRun).where(AIRun.article_id==article_id,AIRun.status==AIRunStatus.succeeded))).one()
            assert run.job_id==job_id and run.attempt_number==2 and run.retry_count==1
    finally:
        async with factory() as s:
            await s.execute(delete(AIRun).where(AIRun.article_id==article_id)); await s.execute(delete(Article).where(Article.id==article_id)); await s.execute(delete(Job).where(Job.id==job_id)); await s.execute(delete(Feed).where(Feed.id==feed_id)); await s.commit()
        await engine.dispose()
