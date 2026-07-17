"""RSS collection use case and PostgreSQL persistence."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .infrastructure.feeds.rss import FeedError, InvalidFeed, SafeFeedClient, parse_feed
from .models import Article, DateSource, Feed, FetchRun, FetchStatus, ProcessingStatus


class FeedNotFound(Exception): pass


async def _finalize_failure(factory: async_sessionmaker[AsyncSession], feed_id: uuid.UUID,
                            run_id: uuid.UUID, exc: BaseException) -> None:
    is_feed = isinstance(exc, FeedError)
    code = exc.code if is_feed else ("collection_cancelled" if isinstance(exc, asyncio.CancelledError) else "collection_unexpected")
    message = str(exc)[:1000] if is_feed else ("collection was cancelled" if isinstance(exc, asyncio.CancelledError) else "unexpected collection failure")
    retryable = exc.retryable if is_feed else isinstance(exc, asyncio.CancelledError)
    async with factory() as session:
        feed = await session.get(Feed, feed_id); run = (await session.scalars(
            select(FetchRun).where(FetchRun.id == run_id).with_for_update())).one_or_none()
        if run is None or run.status != FetchStatus.started: return
        now = datetime.now(UTC)
        if feed:
            feed.last_failure_at = now; feed.consecutive_failures += 1
            if not retryable: feed.enabled = False
        run.status = FetchStatus.failed; run.finished_at = now
        run.http_status = exc.http_status if is_feed else None
        run.error_class = type(exc).__name__[:120]; run.error_code = code[:80]
        run.safe_error_message = message; run.retryable = retryable
        if isinstance(exc, InvalidFeed):
            run.fetched_count = exc.total; run.rejected_count = exc.rejected
        await session.commit()


async def collect_feed(factory: async_sessionmaker[AsyncSession], settings: Settings, feed_id: uuid.UUID,
                       job_id: uuid.UUID, *, client: SafeFeedClient | None = None) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
        feed = await session.get(Feed, feed_id)
        if feed is None or not feed.enabled: raise FeedNotFound("feed is missing or disabled")
        url, etag, last_modified = feed.url, feed.etag, feed.last_modified
        run = FetchRun(feed_id=feed.id, job_id=job_id, status=FetchStatus.started,
                       request_etag=etag, request_last_modified=last_modified)
        session.add(run); await session.commit(); run_id = run.id
    owned_client = client is None
    client = client or SafeFeedClient(settings)
    try:
        result = await client.fetch(url, etag=etag, last_modified=last_modified)
        async with factory() as session:
            feed = (await session.scalars(select(Feed).where(Feed.id == feed_id).with_for_update())).one()
            run = await session.get(FetchRun, run_id)
            assert run is not None
            run.http_status = result.status_code; run.response_etag = result.etag
            run.response_last_modified = result.last_modified; run.finished_at = datetime.now(UTC)
            feed.next_fetch_at = datetime.now(UTC) + timedelta(minutes=feed.fetch_interval_minutes)
            feed.last_success_at = datetime.now(UTC); feed.consecutive_failures = 0
            if result.etag is not None: feed.etag = result.etag
            if result.last_modified is not None: feed.last_modified = result.last_modified
            if result.status_code == 304:
                run.status = FetchStatus.not_modified
            else:
                assert result.body is not None
                parsed = parse_feed(result.body, now, max_entries=settings.rss_max_entries,
                                    max_raw_entry_bytes=settings.rss_max_raw_entry_bytes)
                run.response_body_hash = hashlib.sha256(result.body).hexdigest()
                run.response_body_gzip = gzip.compress(result.body)
                run.fetched_count = parsed.total; run.rejected_count = parsed.rejected
                existing = dict((await session.execute(select(Article.identity_key, Article.content_hash)
                                                        .where(Article.feed_id == feed_id))).all())
                for entry in parsed.entries:
                    values = dict(feed_id=feed_id, source_guid=entry.source_guid, source_url=entry.source_url,
                                  normalized_url=entry.normalized_url, identity_key=entry.identity_key,
                                  title_raw=entry.title_raw, summary_raw=entry.summary_raw, author_raw=entry.author_raw,
                                  title_normalized=entry.title_normalized, summary_text=entry.summary_text,
                                  image_url=entry.image_url, published_at=entry.published_at,
                                  updated_at_source=entry.updated_at_source, fetched_at=now,
                                  date_source=DateSource(entry.date_source), detected_language="en",
                                  content_hash=entry.content_hash, raw_entry=entry.raw_entry,
                                  processing_status=ProcessingStatus.normalized)
                    update_values = {**values, "updated_at": datetime.now(UTC)}
                    statement = insert(Article).values(**values).on_conflict_do_update(
                        index_elements=[Article.feed_id, Article.identity_key], set_=update_values,
                        where=Article.content_hash != entry.content_hash)
                    await session.execute(statement)
                    if entry.identity_key not in existing: run.inserted_count += 1
                    elif existing[entry.identity_key] != entry.content_hash: run.updated_count += 1
                run.status = FetchStatus.succeeded
                if parsed.warning: run.safe_error_message = f"parser warning: {parsed.warning}"
            await session.commit()
    except BaseException as exc:
        await _finalize_failure(factory, feed_id, run_id, exc)
        raise
    finally:
        if owned_client:
            try: await client.aclose()
            except Exception: pass  # Response outcome is already durable; close errors are non-semantic.
