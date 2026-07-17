"""APScheduler 3.x process that enqueues due feeds."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .db import SessionFactory, engine
from .jobs import enqueue, recover_expired_leases
from .models import Feed, JobType


async def enqueue_due_feeds(factory=SessionFactory, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    async with factory() as session:
        await recover_expired_leases(session, now)
        feeds = (await session.scalars(select(Feed).where(Feed.enabled.is_(True), Feed.next_fetch_at <= now)
                                       .order_by(Feed.next_fetch_at).with_for_update(skip_locked=True))).all()
        for feed in feeds:
            job_id = await enqueue(session, JobType.fetch_feed, {"feed_id": str(feed.id)}, dedupe_key=f"feed:{feed.id}")
            if job_id is not None:
                # Prevent enqueue churn while the durable job is waiting/running.
                feed.next_fetch_at = now + timedelta(minutes=feed.fetch_interval_minutes)
        await session.commit()


async def run() -> None:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(enqueue_due_feeds, "interval", minutes=1, id="enqueue-due-feeds",
                      replace_existing=True, coalesce=True, max_instances=1)
    scheduler.start(); await enqueue_due_feeds()
    try: await asyncio.Event().wait()
    finally: scheduler.shutdown(wait=True); await engine.dispose()


if __name__ == "__main__": asyncio.run(run())
