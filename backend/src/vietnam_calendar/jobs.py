"""PostgreSQL-backed durable job repository."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Feed, Job, JobStatus, JobType


def retry_delay(attempts: int, retry_after: int | None = None) -> timedelta:
    if retry_after is not None: return timedelta(seconds=min(3600, max(0, retry_after)))
    base = (60, 300, 1200)[min(max(attempts - 1, 0), 2)]
    return timedelta(seconds=base + random.uniform(0, base * 0.1))


async def enqueue(session: AsyncSession, job_type: JobType, payload: dict[str, Any], *,
                  dedupe_key: str | None = None, priority: int = 0, max_attempts: int = 3,
                  run_after: datetime | None = None) -> uuid.UUID | None:
    statement = insert(Job).values(job_type=job_type, payload=payload, dedupe_key=dedupe_key,
                                   priority=priority, max_attempts=max_attempts,
                                   run_after=run_after or datetime.now(UTC)).on_conflict_do_nothing().returning(Job.id)
    return (await session.execute(statement)).scalar_one_or_none()


async def recover_expired_leases(session: AsyncSession, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    jobs = (await session.scalars(select(Job).where(Job.status == JobStatus.running,
                                                    Job.lease_expires_at < now).with_for_update(skip_locked=True))).all()
    for job in jobs:
        exhausted = job.attempts >= job.max_attempts
        job.status = JobStatus.dead if exhausted else JobStatus.retry_wait
        job.locked_by = None; job.locked_at = None; job.lease_expires_at = None
        job.last_error_code = "lease_exhausted" if exhausted else "lease_expired"
        job.last_error_message = "worker lease expired at max attempts" if exhausted else "worker lease expired"
        if exhausted:
            job.finished_at = now
            if job.job_type == JobType.fetch_feed and job.payload.get("feed_id"):
                try: feed = await session.get(Feed, uuid.UUID(str(job.payload["feed_id"])))
                except ValueError: feed = None
                if feed: feed.enabled = False
        else: job.run_after = now
    return len(jobs)


async def claim(session: AsyncSession, worker_id: str, lease_seconds: int, now: datetime | None = None) -> Job | None:
    now = now or datetime.now(UTC)
    query = (select(Job).where(Job.status.in_([JobStatus.queued, JobStatus.retry_wait]), Job.run_after <= now)
             .order_by(Job.priority.desc(), Job.run_after, Job.created_at).with_for_update(skip_locked=True).limit(1))
    job = (await session.scalars(query)).first()
    if job is None: return None
    job.status = JobStatus.running; job.locked_by = worker_id; job.locked_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds); job.started_at = job.started_at or now
    job.attempts += 1
    await session.flush()
    return job


async def succeed(session: AsyncSession, job_id: uuid.UUID, worker_id: str) -> bool:
    now = datetime.now(UTC)
    result = await session.execute(update(Job).where(Job.id == job_id, Job.status == JobStatus.running,
                                                      Job.locked_by == worker_id).values(
        status=JobStatus.succeeded, finished_at=now, locked_by=None, locked_at=None, lease_expires_at=None,
        last_error_code=None, last_error_message=None))
    return result.rowcount == 1


async def heartbeat(session: AsyncSession, job_id: uuid.UUID, ownership: str,
                    lease_seconds: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    result = await session.execute(update(Job).where(Job.id == job_id, Job.status == JobStatus.running,
                                                      Job.locked_by == ownership,
                                                      Job.lease_expires_at >= now).values(
        lease_expires_at=now + timedelta(seconds=lease_seconds)))
    return result.rowcount == 1


async def fail(session: AsyncSession, job_id: uuid.UUID, worker_id: str, *, code: str,
               message: str, retryable: bool, retry_after: int | None = None) -> JobStatus | None:
    job = (await session.scalars(select(Job).where(Job.id == job_id, Job.status == JobStatus.running,
                                                   Job.locked_by == worker_id).with_for_update())).one_or_none()
    if job is None: return None
    now = datetime.now(UTC); can_retry = retryable and job.attempts < job.max_attempts
    job.status = JobStatus.retry_wait if can_retry else JobStatus.dead
    job.run_after = now + retry_delay(job.attempts, retry_after) if can_retry else job.run_after
    job.finished_at = None if can_retry else now
    job.locked_by = None; job.locked_at = None; job.lease_expires_at = None
    job.last_error_code = code[:80]; job.last_error_message = message[:1000]
    return job.status
