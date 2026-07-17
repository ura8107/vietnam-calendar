"""Human-controlled event editing, review, merge/split and clustering suggestions."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import (AIRun, AIRunStatus, AuditLog, ClusterCandidateStatus, Event,
                     EventArticle, EventClusterCandidate, EventRevision, PublicationStatus,
                     Relevance, Review, ReviewDecision, User)

EDITABLE_FIELDS=("title_ja","summary_ja","event_date","date_certainty","category","relevance_status","relevance_reason","importance_level","importance_score","importance_reason","must_include","must_include_reason","certainty")

def snapshot(event:Event)->dict[str,Any]:
    result={}
    for name in (*EDITABLE_FIELDS,"publication_status","merged_into_event_id","version"):
        value=getattr(event,name)
        result[name]=value.value if hasattr(value,"value") else value.isoformat() if hasattr(value,"isoformat") else value
    return result

def validate_human_values(values:dict[str,Any])->None:
    if values.get("must_include") and not str(values.get("must_include_reason") or "").strip():
        raise HTTPException(422,"must_include_reason is required")
    if values.get("relevance_status") == "out_of_scope" and (values.get("importance_level") is not None or values.get("importance_score") is not None):
        raise HTTPException(422,"out-of-scope events cannot have importance")

def membership_snapshot(links:list[EventArticle])->list[dict[str,Any]]:
    return sorted(({"article_id":str(link.article_id),"is_primary_source":link.is_primary_source,"similarity_score":float(link.similarity_score) if link.similarity_score is not None else None,"link_reason":link.link_reason} for link in links),key=lambda value:value["article_id"])

async def require_publishable(db:AsyncSession,event:Event,reason:str|None)->None:
    if not (reason or "").strip(): raise HTTPException(422,"approval reason is required")
    if event.merged_into_event_id is not None: raise HTTPException(409,"merged event cannot be approved")
    if event.relevance_status != Relevance.target: raise HTTPException(422,"only target events can be approved")
    required=(event.title_ja,event.summary_ja,event.category,event.relevance_reason,event.importance_level,event.importance_reason,event.event_date,event.date_certainty,event.certainty,event.rule_version,event.prompt_version)
    if any(value is None or (isinstance(value,str) and not value.strip()) for value in required): raise HTTPException(422,"event is not publishable")
    validate_human_values(snapshot(event))
    article_count=await db.scalar(select(func.count()).select_from(EventArticle).where(EventArticle.event_id==event.id))
    if not article_count: raise HTTPException(422,"approved event requires a source article")

def _audit(db:AsyncSession,user:User,action:str,event_id:uuid.UUID,request_id:str,before:dict|None,after:dict|None,details:dict|None=None)->None:
    db.add(AuditLog(actor_user_id=user.id,action=action,entity_type="event",entity_id=str(event_id),request_id=request_id,before_values=before,after_values=after,details=details or {}))

async def revise_event(db:AsyncSession,event:Event,user:User,values:dict[str,Any],expected_version:int,reason:str,request_id:str)->Event:
    if event.version != expected_version: raise HTTPException(409,"event version conflict")
    if event.merged_into_event_id is not None: raise HTTPException(409,"merged event cannot be edited")
    required={"title_ja","summary_ja","event_date","date_certainty","category","relevance_status","must_include","certainty"}
    if any(name in values and values[name] is None for name in required): raise HTTPException(422,"required event fields cannot be null")
    before=snapshot(event); combined={**before,**values}; validate_human_values(combined)
    for name,value in values.items(): setattr(event,name,value)
    event.publication_status=PublicationStatus.needs_review
    event.version+=1
    revision=EventRevision(event_id=event.id,version=event.version,changed_by_id=user.id,before_values=before,after_values=snapshot(event),reason=reason)
    db.add(revision); await db.flush(); event.current_revision_id=revision.id
    _audit(db,user,"event.updated",event.id,request_id,before,snapshot(event),{"reason":reason})
    return event

async def review_event(db:AsyncSession,event:Event,user:User,decision:ReviewDecision,expected_version:int,reason:str|None,uncertainty_note:str|None,request_id:str)->Review:
    if event.version != expected_version: raise HTTPException(409,"event version conflict")
    if decision == ReviewDecision.approve: await require_publishable(db,event,reason)
    elif not (reason or "").strip(): raise HTTPException(422,"reason is required")
    before=snapshot(event)
    event.publication_status={ReviewDecision.approve:PublicationStatus.approved,ReviewDecision.reject:PublicationStatus.hidden,ReviewDecision.needs_changes:PublicationStatus.needs_review}[decision]
    event.version+=1
    after=snapshot(event)
    source_ids=select(EventArticle.article_id).where(EventArticle.event_id==event.id)
    ai_run=(await db.scalars(select(AIRun).where((AIRun.event_id==event.id)|(AIRun.article_id.in_(source_ids)),AIRun.status==AIRunStatus.succeeded).order_by(AIRun.finished_at.desc()).limit(1))).one_or_none()
    review=Review(event_id=event.id,reviewer_id=user.id,decision=decision,reason=reason,uncertainty_note=uncertainty_note,ai_proposal=ai_run.parsed_output if ai_run and ai_run.parsed_output else {},human_values=after,rule_version=event.rule_version,prompt_version=event.prompt_version,provider=ai_run.provider if ai_run else "human",model=ai_run.model if ai_run else "no-ai-proposal")
    revision=EventRevision(event_id=event.id,version=event.version,changed_by_id=user.id,before_values=before,after_values=after,reason=reason or decision.value)
    db.add_all([review,revision]); await db.flush(); event.current_revision_id=revision.id
    if decision==ReviewDecision.reject:
        candidates=(await db.scalars(select(EventClusterCandidate).where(EventClusterCandidate.status==ClusterCandidateStatus.pending,((EventClusterCandidate.event_id==event.id)|(EventClusterCandidate.candidate_event_id==event.id))).with_for_update())).all()
        for candidate in candidates:
            candidate.status=ClusterCandidateStatus.dismissed; candidate.reviewed_by_id=user.id; candidate.reviewed_at=datetime.now(UTC)
            db.add(AuditLog(actor_user_id=user.id,action="event.cluster_candidate_invalidated",entity_type="event_cluster_candidate",entity_id=str(candidate.id),request_id=request_id,before_values={"status":"pending"},after_values={"status":"dismissed"},details={"reason":"event rejected","event_id":str(event.id)}))
    _audit(db,user,"event.reviewed",event.id,request_id,before,after,{"decision":decision.value,"review_id":str(review.id)})
    return review

async def merge_events(db:AsyncSession,target:Event,source:Event,user:User,target_version:int,source_version:int,reason:str,request_id:str)->Event:
    if target.id==source.id: raise HTTPException(422,"cannot merge an event into itself")
    if target.version!=target_version or source.version!=source_version: raise HTTPException(409,"event version conflict")
    if target.merged_into_event_id is not None or source.merged_into_event_id is not None: raise HTTPException(409,"merged event cannot be merged again")
    links=(await db.scalars(select(EventArticle).where(EventArticle.event_id==source.id).with_for_update())).all()
    target_links=(await db.scalars(select(EventArticle).where(EventArticle.event_id==target.id).with_for_update())).all()
    source_membership_before=membership_snapshot(links); target_membership_before=membership_snapshot(target_links)
    existing={link.article_id for link in target_links}; target_has_primary=any(link.is_primary_source for link in target_links)
    for link in links:
        if link.article_id not in existing:
            make_primary=not target_has_primary and link.is_primary_source
            db.add(EventArticle(event_id=target.id,article_id=link.article_id,similarity_score=link.similarity_score,is_primary_source=make_primary,link_reason=f"human merge: {reason}")); target_has_primary=target_has_primary or make_primary
        await db.delete(link)
    source_before=snapshot(source); source.publication_status=PublicationStatus.hidden; source.merged_into_event_id=target.id; source.version+=1
    target_before=snapshot(target); target.publication_status=PublicationStatus.needs_review; target.version+=1
    for event,before in ((target,target_before),(source,source_before)):
        rev=EventRevision(event_id=event.id,version=event.version,changed_by_id=user.id,before_values=before,after_values=snapshot(event),reason=reason); db.add(rev); await db.flush(); event.current_revision_id=rev.id
    await db.flush()
    target_membership_after=membership_snapshot((await db.scalars(select(EventArticle).where(EventArticle.event_id==target.id))).all())
    candidates=(await db.scalars(select(EventClusterCandidate).where(EventClusterCandidate.status==ClusterCandidateStatus.pending,((EventClusterCandidate.event_id.in_([target.id,source.id]))|(EventClusterCandidate.candidate_event_id.in_([target.id,source.id])))).with_for_update())).all()
    for candidate in candidates:
        candidate.status=ClusterCandidateStatus.dismissed; candidate.reviewed_by_id=user.id; candidate.reviewed_at=datetime.now(UTC)
        db.add(AuditLog(actor_user_id=user.id,action="event.cluster_candidate_invalidated",entity_type="event_cluster_candidate",entity_id=str(candidate.id),request_id=request_id,before_values={"status":"pending"},after_values={"status":"dismissed"},details={"reason":"event merged","target_event_id":str(target.id),"source_event_id":str(source.id)}))
    membership_details={"source_event_id":str(source.id),"reason":reason,"target_membership_before":target_membership_before,"source_membership_before":source_membership_before,"target_membership_after":target_membership_after,"moved_article_ids":[str(link.article_id) for link in links if link.article_id not in existing],"duplicate_article_ids":[str(link.article_id) for link in links if link.article_id in existing]}
    _audit(db,user,"event.merged",target.id,request_id,target_before,snapshot(target),membership_details)
    _audit(db,user,"event.merged_into",source.id,request_id,source_before,snapshot(source),membership_details)
    return target

async def split_event(db:AsyncSession,source:Event,user:User,article_ids:list[uuid.UUID],expected_version:int,new_values:dict[str,Any],reason:str,request_id:str)->Event:
    if source.version!=expected_version: raise HTTPException(409,"event version conflict")
    if source.merged_into_event_id is not None: raise HTTPException(409,"merged event cannot be split")
    requested=set(article_ids)
    if not requested: raise HTTPException(422,"at least one article is required")
    links=(await db.scalars(select(EventArticle).where(EventArticle.event_id==source.id).with_for_update())).all()
    present={link.article_id for link in links}
    source_membership_before=membership_snapshot(links)
    if not requested <= present: raise HTTPException(422,"article does not belong to source event")
    if requested==present: raise HTTPException(422,"split must leave at least one source article")
    validate_human_values(new_values)
    new_event=Event(**new_values,publication_status=PublicationStatus.needs_review,rule_version=source.rule_version,prompt_version=source.prompt_version,version=1)
    db.add(new_event); await db.flush()
    moved=[link for link in links if link.article_id in requested]; moved_primary=next((link.article_id for link in moved if link.is_primary_source),moved[0].article_id)
    for link in links:
        if link.article_id in requested:
            db.add(EventArticle(event_id=new_event.id,article_id=link.article_id,similarity_score=link.similarity_score,is_primary_source=link.article_id==moved_primary,link_reason=f"human split: {reason}")); await db.delete(link)
    retained=[link for link in links if link.article_id not in requested]
    await db.flush()
    if not any(link.is_primary_source for link in retained): retained[0].is_primary_source=True
    source_before=snapshot(source); source.version+=1; source.publication_status=PublicationStatus.needs_review
    source_rev=EventRevision(event_id=source.id,version=source.version,changed_by_id=user.id,before_values=source_before,after_values=snapshot(source),reason=reason)
    new_rev=EventRevision(event_id=new_event.id,version=1,changed_by_id=user.id,before_values=None,after_values=snapshot(new_event),reason=reason)
    db.add_all([source_rev,new_rev]); await db.flush(); source.current_revision_id=source_rev.id; new_event.current_revision_id=new_rev.id
    await db.flush()
    membership_details={"new_event_id":str(new_event.id),"reason":reason,"source_membership_before":source_membership_before,"source_membership_after":membership_snapshot((await db.scalars(select(EventArticle).where(EventArticle.event_id==source.id))).all()),"new_membership_after":membership_snapshot((await db.scalars(select(EventArticle).where(EventArticle.event_id==new_event.id))).all()),"moved_article_ids":[str(v) for v in requested]}
    _audit(db,user,"event.split",source.id,request_id,source_before,snapshot(source),membership_details)
    _audit(db,user,"event.created_by_split",new_event.id,request_id,None,snapshot(new_event),{"source_event_id":str(source.id),**membership_details})
    return new_event

def title_similarity(left:str,right:str)->float:
    normalize=lambda s:" ".join(re.findall(r"[\w]+",s.casefold()))
    return SequenceMatcher(None,normalize(left),normalize(right)).ratio()

async def generate_cluster_candidates(factory:async_sessionmaker[AsyncSession],event_id:uuid.UUID)->None:
    async with factory() as db:
        event=await db.get(Event,event_id)
        if event is None: raise RuntimeError("event not found")
        if event.publication_status==PublicationStatus.hidden or event.merged_into_event_id is not None: return
        peers=(await db.scalars(select(Event).where(Event.id!=event.id,Event.publication_status!=PublicationStatus.hidden,Event.merged_into_event_id.is_(None),Event.event_date.between(event.event_date-timedelta(days=3),event.event_date+timedelta(days=3))).limit(200))).all()
        for peer in peers:
            first,second=sorted((event.id,peer.id),key=str); score=title_similarity(event.title_ja,peer.title_ja)
            if score < .55: continue
            candidate_id=uuid.uuid4()
            statement=insert(EventClusterCandidate).values(id=candidate_id,event_id=first,candidate_event_id=second,similarity_score=score,reasons=["title_similarity","nearby_event_date"],status=ClusterCandidateStatus.pending).on_conflict_do_nothing(constraint="uq_event_cluster_pair").returning(EventClusterCandidate.id)
            inserted=(await db.execute(statement)).scalar_one_or_none()
            if inserted is not None: db.add(AuditLog(actor_user_id=None,action="event.cluster_candidate_created",entity_type="event_cluster_candidate",entity_id=str(inserted),before_values=None,after_values={"event_id":str(first),"candidate_event_id":str(second),"similarity_score":score},details={"source":"cluster_event_job"}))
        await db.commit()
