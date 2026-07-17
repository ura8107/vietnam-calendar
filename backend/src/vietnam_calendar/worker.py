"""Single-process durable queue worker entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from .collection import FeedNotFound, collect_feed
from .analysis import analyze_article
from .infrastructure.ai.providers import AIProviderError
from .config import get_settings
from .db import SessionFactory, engine
from .infrastructure.feeds.rss import FeedError
from .jobs import claim, fail, heartbeat, recover_expired_leases, succeed
from .models import AIRun,AIRunStatus,EventArticle,Feed,JobStatus,JobType
from .application.evals import evaluate_rules
from .events import generate_cluster_candidates
from sqlalchemy import select
from datetime import UTC,datetime
from pathlib import Path

log = logging.getLogger(__name__)


class LeaseLost(Exception): pass
class HeartbeatUnavailable(Exception): pass


async def _heartbeat_loop(job_id: uuid.UUID, ownership: str, lease_seconds: int) -> None:
    while True:
        await asyncio.sleep(max(1, lease_seconds / 3))
        try:
            async with SessionFactory() as session:
                owned = await heartbeat(session, job_id, ownership, lease_seconds); await session.commit()
        except asyncio.CancelledError: raise
        except Exception as exc: raise HeartbeatUnavailable("heartbeat database unavailable") from exc
        if not owned: raise LeaseLost("job lease ownership was lost")


async def _with_heartbeat(awaitable, job_id: uuid.UUID, ownership: str, lease_seconds: int):
    work = asyncio.create_task(awaitable); beat = asyncio.create_task(_heartbeat_loop(job_id, ownership, lease_seconds))
    try:
        done, _ = await asyncio.wait({work, beat}, return_when=asyncio.FIRST_COMPLETED)
        if beat in done: await beat
        return await work
    finally:
        for task in (work, beat):
            if not task.done(): task.cancel()
        await asyncio.gather(work, beat, return_exceptions=True)


async def _disable_feed(feed_id: str) -> None:
    async with SessionFactory() as session:
        feed = await session.get(Feed, uuid.UUID(feed_id))
        if feed: feed.enabled = False
        await session.commit()


async def _handle_heartbeat_unavailable(job, ownership: str, exc: HeartbeatUnavailable) -> JobStatus | None:
    async with SessionFactory() as session:
        state = await fail(session, job.id, ownership, code="heartbeat_unavailable",
                           message=str(exc), retryable=True)
        await session.commit()
    if state == JobStatus.dead and job.job_type == JobType.fetch_feed:
        await _disable_feed(str(job.payload["feed_id"]))
    return state


async def run() -> None:
    settings = get_settings(); worker_id = f"{socket.gethostname()}-{os.getpid()}"
    while True:
        job = None
        try:
            ownership = f"{worker_id}:{uuid.uuid4().hex}"
            async with SessionFactory() as session:
                await recover_expired_leases(session)
                job = await claim(session, ownership, settings.worker_lease_seconds)
                await session.commit()
            if job is None:
                await asyncio.sleep(settings.worker_poll_seconds); continue
            if job.job_type == JobType.fetch_feed:
                work=collect_feed(SessionFactory, settings, uuid.UUID(str(job.payload["feed_id"])), job.id)
            elif job.job_type == JobType.analyze_article:
                work=analyze_article(SessionFactory,settings,uuid.UUID(str(job.payload["article_id"])),job_id=job.id,retry_count=max(0,job.attempts-1))
            elif job.job_type == JobType.reanalyze_event:
                async with SessionFactory() as session:
                    article_id=(await session.scalars(select(EventArticle.article_id).where(EventArticle.event_id==uuid.UUID(str(job.payload["event_id"]))).order_by(EventArticle.is_primary_source.desc()).limit(1))).one_or_none()
                if article_id is None: raise RuntimeError("event has no source article")
                work=analyze_article(SessionFactory,settings,article_id,job_id=job.id,retry_count=max(0,job.attempts-1))
            elif job.job_type == JobType.cluster_event:
                work=generate_cluster_candidates(SessionFactory,uuid.UUID(str(job.payload["event_id"])))
            elif job.job_type == JobType.retention and job.payload.get("kind")=="importance_eval":
                async def run_eval():
                    now=datetime.now(UTC)
                    try: report=evaluate_rules(Path(job.payload["path"]))
                    except Exception as exc:
                        async with SessionFactory() as session:
                            session.add(AIRun(job_id=job.id,attempt_number=job.attempts,provider="rule",base_url_identifier="local",model="importance-rubric-v1",prompt_version="none",schema_version="importance-eval-report-v1",rule_version="importance-rubric-v1",input_hash="0"*64,source_article_ids=[],validation_errors=[{"code":"eval_failed","message":"evaluation failed"}],status=AIRunStatus.failed,retry_count=max(0,job.attempts-1),latency_ms=0,started_at=now,finished_at=datetime.now(UTC))); await session.commit()
                        raise RuntimeError("importance evaluation failed") from exc
                    async with SessionFactory() as session:
                        session.add(AIRun(job_id=job.id,attempt_number=job.attempts,provider="rule",base_url_identifier="local",model="importance-rubric-v1",prompt_version="none",schema_version="importance-eval-report-v1",rule_version="importance-rubric-v1",input_hash=report["dataset_sha256"],source_article_ids=[],parsed_output=report,status=AIRunStatus.succeeded,retry_count=max(0,job.attempts-1),latency_ms=0,started_at=now,finished_at=datetime.now(UTC))); await session.commit()
                work=run_eval()
            else: raise RuntimeError(f"unsupported job type: {job.job_type.value}")
            await _with_heartbeat(work,
                                  job.id, ownership, settings.worker_lease_seconds)
        except AIProviderError as exc:
            async with SessionFactory() as session:
                await fail(session,job.id,ownership,code=exc.code,message=str(exc),retryable=exc.retryable,retry_after=exc.retry_after); await session.commit()
        except FeedError as exc:
            async with SessionFactory() as session:
                state = await fail(session, job.id, ownership, code=exc.code, message=str(exc),
                                   retryable=exc.retryable, retry_after=exc.retry_after); await session.commit()
            if state == JobStatus.dead: await _disable_feed(str(job.payload["feed_id"]))
        except FeedNotFound as exc:
            async with SessionFactory() as session:
                await fail(session, job.id, ownership, code="feed_not_found", message=str(exc), retryable=False); await session.commit()
        except LeaseLost:
            log.warning("job lease lost", extra={"job_id": str(job.id)})
        except HeartbeatUnavailable as exc:
            log.warning("job heartbeat unavailable", extra={"job_id": str(job.id)})
            await _handle_heartbeat_unavailable(job, ownership, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("job failed", extra={"job_id": str(job.id)})
            if job is not None:
                async with SessionFactory() as session:
                    state = await fail(session, job.id, ownership, code="unexpected_error", message="unexpected worker error", retryable=False); await session.commit()
                if state == JobStatus.dead and job.job_type == JobType.fetch_feed: await _disable_feed(str(job.payload["feed_id"]))
            await asyncio.sleep(settings.worker_poll_seconds)
        else:
            async with SessionFactory() as session:
                await succeed(session, job.id, ownership); await session.commit()


async def _main() -> None:
    try: await run()
    finally: await engine.dispose()


if __name__ == "__main__": asyncio.run(_main())
