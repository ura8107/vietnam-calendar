import os
import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vietnam_calendar.collection import collect_feed
from vietnam_calendar.config import Settings
from vietnam_calendar.infrastructure.feeds.rss import FeedError, SafeFeedClient
from vietnam_calendar.jobs import claim, enqueue, fail, heartbeat, recover_expired_leases, succeed
from vietnam_calendar.models import Article, Feed, FetchRun, FetchStatus, Job, JobStatus, JobType
from vietnam_calendar.scheduler import enqueue_due_feeds
import vietnam_calendar.worker as worker_module

pytestmark = pytest.mark.skipif(not os.getenv("PHASE2_TEST_DATABASE_URL"), reason="requires explicit PostgreSQL test database")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "feeds" / "tuoitre-home.xml"


async def resolver(host, port): return ["93.184.216.34"]


@pytest.mark.asyncio
async def test_collection_idempotency_304_claim_and_lease_recovery():
    engine = create_async_engine(os.environ["PHASE2_TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(FetchRun)); await session.execute(delete(Article)); await session.execute(delete(Job))
        feed = (await session.scalars(select(Feed).limit(1))).one(); feed.enabled=True; feed.consecutive_failures=0; feed_id = feed.id
        first = await enqueue(session, JobType.fetch_feed, {"feed_id": str(feed_id)}, dedupe_key="integration:first")
        await session.commit()

    raw = FIXTURE.read_bytes(); mode = "200"
    async def handler(request):
        if mode == "304": return httpx.Response(304, request=request)
        return httpx.Response(200, content=raw, headers={"etag": '"v1"'}, request=request)
    settings = Settings(_env_file=None, database_url=os.environ["PHASE2_TEST_DATABASE_URL"])
    client = SafeFeedClient(settings, transport=httpx.MockTransport(handler), resolver=resolver)
    await collect_feed(factory, settings, feed_id, first, client=client)
    async with factory() as session:
        assert (await session.scalars(select(Article))).one().title_raw == "Sample Vietnam infrastructure decision"
        assert (await session.get(FetchRun, (await session.scalars(select(FetchRun.id))).one())).inserted_count == 1
        second = await enqueue(session, JobType.fetch_feed, {"feed_id": str(feed_id)}, dedupe_key="integration:second")
        await session.commit()
    await collect_feed(factory, settings, feed_id, second, client=client)
    async with factory() as session:
        runs=(await session.scalars(select(FetchRun).order_by(FetchRun.started_at))).all()
        assert runs[-1].inserted_count == 0 and runs[-1].updated_count == 0 and runs[-1].rejected_count == 0
        third = await enqueue(session, JobType.fetch_feed, {"feed_id": str(feed_id)}, dedupe_key="integration:third"); await session.commit()
    raw = raw.replace(b"Sample Vietnam infrastructure decision", b"Updated Vietnam infrastructure decision")
    await collect_feed(factory, settings, feed_id, third, client=client)
    async with factory() as session:
        assert (await session.scalars(select(FetchRun).order_by(FetchRun.started_at.desc()))).first().updated_count == 1
        article=(await session.scalars(select(Article))).one(); old_updated=article.updated_at
        metadata = await enqueue(session, JobType.fetch_feed, {"feed_id": str(feed_id)}, dedupe_key="integration:metadata"); await session.commit()
    raw = raw.replace(b"Tuoi Tre News", b"Updated Author")
    await collect_feed(factory, settings, feed_id, metadata, client=client)
    async with factory() as session:
        latest=(await session.scalars(select(FetchRun).order_by(FetchRun.started_at.desc()))).first()
        article=(await session.scalars(select(Article))).one()
        assert latest.updated_count == 1 and article.author_raw == "Updated Author" and article.updated_at >= old_updated
        fourth = await enqueue(session, JobType.fetch_feed, {"feed_id": str(feed_id)}, dedupe_key="integration:fourth"); await session.commit()
    mode = "304"; await collect_feed(factory, settings, feed_id, fourth, client=client)
    async with factory() as session:
        assert (await session.scalars(select(FetchRun).order_by(FetchRun.started_at.desc()))).first().status.value == "not_modified"
        for old in (await session.scalars(select(Job))).all(): old.status=JobStatus.succeeded; old.finished_at=datetime.now(UTC)
        for n in range(2): await enqueue(session, JobType.fetch_feed, {"feed_id": str(feed_id)}, dedupe_key=f"claim:{n}")
        await session.commit()
    async with factory() as one:
        job1=await claim(one,"worker-1",30); await one.commit()
    async with factory() as two:
        job2=await claim(two,"worker-2",30); await two.commit()
    assert job1 and job2 and job1.id != job2.id
    async with factory() as session:
        job1=await session.get(Job,job1.id); job1.lease_expires_at=datetime.now(UTC)-timedelta(seconds=1); await session.commit()
    async with factory() as session:
        assert await recover_expired_leases(session) == 1; await session.commit()
        assert (await session.get(Job,job1.id)).status == JobStatus.retry_wait
        for claimed in (job1.id,job2.id):
            row=await session.get(Job,claimed); row.status=JobStatus.succeeded; row.locked_by=None; row.locked_at=None; row.lease_expires_at=None; row.finished_at=datetime.now(UTC)
        exhausted=await enqueue(session,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key="exhausted",max_attempts=1); await session.commit()
    async with factory() as session:
        exhausted_job=await claim(session,"owner:max",30); await session.commit()
    async with factory() as session:
        exhausted_job=await session.get(Job,exhausted_job.id); exhausted_job.lease_expires_at=datetime.now(UTC)-timedelta(seconds=1); await session.commit()
    async with factory() as session:
        await recover_expired_leases(session); await session.commit(); exhausted_job=await session.get(Job,exhausted_job.id)
        assert exhausted_job.status==JobStatus.dead and exhausted_job.finished_at is not None
        feed=await session.get(Feed,feed_id); assert not feed.enabled; feed.enabled=True
        owned=await enqueue(session,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key="ownership"); await session.commit()
    async with factory() as session:
        owned_job=await claim(session,"owner:one",30); await session.commit()
    async with factory() as session:
        assert await heartbeat(session,owned_job.id,"owner:one",30); await session.commit()
    async with factory() as session:
        row=await session.get(Job,owned_job.id); row.locked_by="owner:two"; await session.commit()
    async with factory() as session:
        assert not await heartbeat(session,owned_job.id,"owner:one",30)
        assert not await succeed(session,owned_job.id,"owner:one")
        assert await fail(session,owned_job.id,"owner:one",code="stale",message="stale",retryable=False) is None
        await session.commit()
    async with factory() as session:
        invalid=await enqueue(session,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key="all-invalid"); await session.commit()
    invalid_xml=b"<rss><channel><item><title>missing link</title></item><item><link>https://news.tuoitre.vn/x</link></item></channel></rss>"
    invalid_client=SafeFeedClient(settings,transport=httpx.MockTransport(lambda request:httpx.Response(200,content=invalid_xml,request=request)),resolver=resolver)
    with pytest.raises(FeedError): await collect_feed(factory,settings,feed_id,invalid,client=invalid_client)
    async with factory() as session:
        run=(await session.scalars(select(FetchRun).order_by(FetchRun.started_at.desc()))).first(); feed=await session.get(Feed,feed_id)
        assert run.fetched_count==2 and run.rejected_count==2 and run.error_code=="invalid_feed"; feed.enabled=True; await session.commit()
    await invalid_client.aclose()
    async with factory() as session:
        unexpected=await enqueue(session,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key="unexpected"); await session.commit()
    broken=SafeFeedClient(settings,transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(ValueError("secret detail"))),resolver=resolver)
    with pytest.raises(ValueError): await collect_feed(factory,settings,feed_id,unexpected,client=broken)
    async with factory() as session:
        run=(await session.scalars(select(FetchRun).order_by(FetchRun.started_at.desc()))).first(); feed=await session.get(Feed,feed_id)
        assert run.status==FetchStatus.failed and run.error_code=="collection_unexpected"
        assert run.safe_error_message=="unexpected collection failure" and not feed.enabled and feed.consecutive_failures==2
        feed.enabled=True; permanent=await enqueue(session,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key="permanent"); await session.commit()
    permanent_client=SafeFeedClient(settings,transport=httpx.MockTransport(lambda request:httpx.Response(404,request=request)),resolver=resolver)
    with pytest.raises(FeedError): await collect_feed(factory,settings,feed_id,permanent,client=permanent_client)
    async with factory() as session:
        feed=await session.get(Feed,feed_id); assert not feed.enabled
        assert not (await session.scalars(select(Feed).where(Feed.enabled.is_(True),Feed.next_fetch_at<=datetime.now(UTC)))).all()
    await permanent_client.aclose()
    await broken.aclose()
    await client.aclose(); await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_dedupe_and_concurrent_enqueue():
    engine=create_async_engine(os.environ["PHASE2_TEST_DATABASE_URL"]); factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(FetchRun)); await session.execute(delete(Job)); feed=(await session.scalars(select(Feed).limit(1))).one()
        feed.enabled=True; feed.next_fetch_at=datetime.now(UTC)-timedelta(minutes=1); feed_id=feed.id; await session.commit()
    now=datetime.now(UTC); await enqueue_due_feeds(factory,now); await enqueue_due_feeds(factory,now)
    async with factory() as session:
        jobs=(await session.scalars(select(Job).where(Job.dedupe_key==f"feed:{feed_id}"))).all(); feed=await session.get(Feed,feed_id)
        assert len(jobs)==1 and feed.next_fetch_at>=now+timedelta(minutes=feed.fetch_interval_minutes)
        await session.execute(delete(Job)); await session.commit()
    async def add_one():
        async with factory() as session:
            result=await enqueue(session,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key="concurrent:same"); await session.commit(); return result
    results=await asyncio.gather(add_one(),add_one())
    assert sum(value is not None for value in results)==1
    async with factory() as session: assert len((await session.scalars(select(Job).where(Job.dedupe_key=="concurrent:same"))).all())==1
    await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_unavailable_terminal_policy(monkeypatch):
    engine=create_async_engine(os.environ["PHASE2_TEST_DATABASE_URL"]); factory=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(worker_module,"SessionFactory",factory)
    async with factory() as session:
        await session.execute(delete(Job)); feed=(await session.scalars(select(Feed).limit(1))).one(); feed.enabled=True; feed_id=feed.id
        await enqueue(session,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key="heartbeat:retry",max_attempts=2); await session.commit()
    async with factory() as session: retry_job=await claim(session,"owner:retry",30); await session.commit()
    state=await worker_module._handle_heartbeat_unavailable(retry_job,"owner:retry",worker_module.HeartbeatUnavailable("db unavailable"))
    async with factory() as session:
        feed=await session.get(Feed,feed_id); stored=await session.get(Job,retry_job.id)
        assert state==JobStatus.retry_wait and stored.status==JobStatus.retry_wait and feed.enabled
        stored.status=JobStatus.succeeded; stored.finished_at=datetime.now(UTC)
        await enqueue(session,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key="heartbeat:dead",max_attempts=1); await session.commit()
    async with factory() as session: dead_job=await claim(session,"owner:dead",30); await session.commit()
    state=await worker_module._handle_heartbeat_unavailable(dead_job,"owner:dead",worker_module.HeartbeatUnavailable("db unavailable"))
    async with factory() as session:
        feed=await session.get(Feed,feed_id); stored=await session.get(Job,dead_job.id)
        assert state==JobStatus.dead and stored.status==JobStatus.dead and not feed.enabled
    await engine.dispose()
