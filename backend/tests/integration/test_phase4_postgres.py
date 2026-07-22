import os
import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vietnam_calendar.events import decide_cluster_candidate, generate_cluster_candidates, merge_events, review_event, split_event
from vietnam_calendar.models import (Article, AuditLog, Certainty, DateCertainty,
    ClusterCandidateStatus, DateSource, Event, EventArticle, EventClusterCandidate, Feed, Importance, ProcessingStatus,
    PublicationStatus, Relevance, ReviewDecision, User)

pytestmark=pytest.mark.skipif(not os.getenv("PHASE4_TEST_DATABASE_URL"),reason="requires explicit PostgreSQL test database")

def new_event(title):
    return Event(title_ja=title,summary_ja="summary",event_date=date(2026,7,18),date_certainty=DateCertainty.confirmed,category="economy",relevance_status=Relevance.target,relevance_reason="Vietnam",importance_level=Importance.high,importance_score=90,importance_reason="national",must_include=False,must_include_reason=None,certainty=Certainty.confirmed,publication_status=PublicationStatus.needs_review,rule_version="importance-rubric-v1",prompt_version="event-analysis-v1",version=1)

@pytest.mark.asyncio
async def test_review_merge_split_preserve_articles_revisions_and_audit():
    engine=create_async_engine(os.environ["PHASE4_TEST_DATABASE_URL"]); factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        suffix=uuid.uuid4(); rid=f"phase4-{suffix}"; feed=Feed(name=rid,url=f"https://news.tuoitre.vn/{rid}.rss",normalized_url=f"https://news.tuoitre.vn/{rid}.rss",publisher="phase4",enabled=True,fetch_interval_minutes=30); user=User(username=rid,password_hash="unused",is_admin=True); db.add_all([feed,user]); await db.flush()
        articles=[]
        for n in range(3):
            articles.append(Article(feed_id=feed.id,source_url=f"https://news.tuoitre.vn/phase4-{uuid.uuid4()}",normalized_url=f"https://news.tuoitre.vn/phase4-{uuid.uuid4()}",identity_key=f"url:{uuid.uuid4().hex}",title_raw=f"source {n}",title_normalized=f"source {n}",content_hash="0"*64,raw_entry={},date_source=DateSource.fetched,processing_status=ProcessingStatus.needs_review))
        target,source=new_event("target"),new_event("source"); db.add_all([user,target,source,*articles]); await db.flush()
        target_link=EventArticle(event_id=target.id,article_id=articles[0].id,is_primary_source=True,link_reason="seed")
        db.add_all([target_link,EventArticle(event_id=source.id,article_id=articles[0].id,is_primary_source=True,link_reason="duplicate primary"),EventArticle(event_id=source.id,article_id=articles[1].id,is_primary_source=False,link_reason="seed"),EventArticle(event_id=source.id,article_id=articles[2].id,is_primary_source=False,link_reason="seed")]); await db.commit()
        await review_event(db,target,user,ReviewDecision.approve,1,"verified",None,rid); await db.commit(); assert target.publication_status==PublicationStatus.approved
        target_link.is_primary_source=False; await db.commit()  # exercise deterministic promotion of a duplicate source-primary
        first,second=sorted((target.id,source.id),key=str); accepted=EventClusterCandidate(event_id=first,candidate_event_id=second,similarity_score=.8,reasons=["test"],status=ClusterCandidateStatus.accepted); db.add(accepted); await db.commit()
        await merge_events(db,target,source,user,2,1,"same formal decision",rid); await db.commit()
        assert target.publication_status==PublicationStatus.needs_review and source.publication_status==PublicationStatus.hidden
        assert accepted.status==ClusterCandidateStatus.dismissed
        assert (await db.scalar(select(func.count()).select_from(EventArticle).where(EventArticle.event_id==target.id)))==3
        assert (await db.scalar(select(func.count()).select_from(EventArticle).where(EventArticle.event_id==target.id,EventArticle.is_primary_source.is_(True))))==1
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                target.current_revision_id=source.current_revision_id; await db.flush()
        await db.refresh(target)
        values={k:getattr(target,k) for k in ("title_ja","summary_ja","event_date","date_certainty","category","relevance_status","relevance_reason","importance_level","importance_score","importance_reason","must_include","must_include_reason","certainty")}; values["title_ja"]="split"
        split=await split_event(db,target,user,[articles[2].id],target.version,values,"distinct follow-up",rid); await db.commit()
        assert (await db.scalar(select(func.count()).select_from(EventArticle).where(EventArticle.event_id==target.id)))==2
        assert (await db.scalar(select(func.count()).select_from(EventArticle).where(EventArticle.event_id==split.id)))==1
        assert (await db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.request_id==rid)))>=5
        orphan=new_event("Vietnam central bank formal rate decision"); peer=new_event("Vietnam central bank rate decision"); db.add_all([orphan,peer]); await db.commit()
        with pytest.raises(HTTPException): await review_event(db,orphan,user,ReviewDecision.approve,1,"checked",None,rid)
        await asyncio.gather(generate_cluster_candidates(factory,orphan.id),generate_cluster_candidates(factory,peer.id))
        pair_count=await db.scalar(select(func.count()).select_from(EventClusterCandidate).where(((EventClusterCandidate.event_id==orphan.id)&(EventClusterCandidate.candidate_event_id==peer.id))|((EventClusterCandidate.event_id==peer.id)&(EventClusterCandidate.candidate_event_id==orphan.id))))
        assert pair_count==1
        race_a,race_b=new_event("Vietnam bank race decision"),new_event("Vietnam bank race decision update"); db.add_all([race_a,race_b]); await db.flush(); rf,rs=sorted((race_a.id,race_b.id),key=str); race_candidate=EventClusterCandidate(event_id=rf,candidate_event_id=rs,similarity_score=.9,reasons=["race"]); db.add(race_candidate); await db.commit()
        async def decide_race():
            async with factory() as session:
                actor=await session.get(User,user.id)
                try: await decide_cluster_candidate(session,race_candidate.id,race_a.id,actor,ClusterCandidateStatus.accepted,"race",rid)
                except HTTPException as exc:
                    assert exc.status_code==409
                await session.commit()
        async def merge_race():
            async with factory() as session:
                actor=await session.get(User,user.id); left=await session.get(Event,race_a.id); right=await session.get(Event,race_b.id)
                await merge_events(session,left,right,actor,1,1,"race merge",rid); await session.commit()
        await asyncio.wait_for(asyncio.gather(decide_race(),merge_race()),timeout=10)
        await db.refresh(race_candidate); assert race_candidate.status==ClusterCandidateStatus.dismissed
        generation_a,generation_b=new_event("Vietnam fiscal generation race"),new_event("Vietnam fiscal generation race update"); db.add_all([generation_a,generation_b]); await db.commit()
        async def merge_generation_race():
            async with factory() as session:
                actor=await session.get(User,user.id); left=await session.get(Event,generation_a.id); right=await session.get(Event,generation_b.id)
                await merge_events(session,left,right,actor,1,1,"generation race",rid); await session.commit()
        await asyncio.wait_for(asyncio.gather(generate_cluster_candidates(factory,generation_a.id),merge_generation_race()),timeout=10)
        active_stale=await db.scalar(select(func.count()).select_from(EventClusterCandidate).where(EventClusterCandidate.status!=ClusterCandidateStatus.dismissed,((EventClusterCandidate.event_id.in_([generation_a.id,generation_b.id]))|(EventClusterCandidate.candidate_event_id.in_([generation_a.id,generation_b.id])))))
        assert active_stale==0
        crossed=[new_event(f"Vietnam crossed generation decision {index}") for index in range(4)]; db.add_all(crossed); await db.commit(); crossed_ids=[event.id for event in crossed]
        await asyncio.wait_for(asyncio.gather(*(generate_cluster_candidates(factory,event.id) for event in crossed)),timeout=10)
        crossed_candidates=(await db.scalars(select(EventClusterCandidate).where(EventClusterCandidate.event_id.in_(crossed_ids),EventClusterCandidate.candidate_event_id.in_(crossed_ids)))).all()
        pairs={(candidate.event_id,candidate.candidate_event_id) for candidate in crossed_candidates}
        assert len(crossed_candidates)==6 and len(pairs)==6
        assert all(candidate.status==ClusterCandidateStatus.pending for candidate in crossed_candidates)
        owned_event_ids=[target.id,source.id,split.id,orphan.id,peer.id,race_a.id,race_b.id,generation_a.id,generation_b.id,*crossed_ids]
        candidate_ids=[str(value) for value in (await db.scalars(select(EventClusterCandidate.id).where((EventClusterCandidate.event_id.in_(owned_event_ids))|(EventClusterCandidate.candidate_event_id.in_(owned_event_ids))))).all()]
        await db.execute(update(Event).where(Event.id.in_(owned_event_ids)).values(current_revision_id=None)); await db.flush()
        await db.execute(delete(Event).where(Event.id.in_(owned_event_ids)))
        await db.execute(delete(Article).where(Article.id.in_([article.id for article in articles])))
        await db.execute(delete(AuditLog).where((AuditLog.request_id==rid)|((AuditLog.entity_type=="event_cluster_candidate")&(AuditLog.entity_id.in_(candidate_ids)))))
        await db.execute(delete(User).where(User.id==user.id)); await db.execute(delete(Feed).where(Feed.id==feed.id)); await db.commit()
    await engine.dispose()
