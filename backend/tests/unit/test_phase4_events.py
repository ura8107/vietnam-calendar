from datetime import date
from types import SimpleNamespace
import uuid
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from vietnam_calendar.api import app, current_session
from vietnam_calendar.db import get_session
from vietnam_calendar.events import require_publishable, snapshot, title_similarity, validate_human_values
from vietnam_calendar.models import (Certainty, DateCertainty, Event, Importance,
                                      PublicationStatus, Relevance)

def event():
    return Event(id=uuid.uuid4(),title_ja="ベトナム中銀が政策金利を決定",summary_ja="正式決定",event_date=date(2026,7,18),date_certainty=DateCertainty.confirmed,category="economy",relevance_status=Relevance.target,relevance_reason="Vietnam",importance_level=Importance.high,importance_score=90,importance_reason="nationwide",must_include=True,must_include_reason="formal policy",certainty=Certainty.confirmed,publication_status=PublicationStatus.needs_review,rule_version="v1",prompt_version="v1",version=2)

def test_snapshot_uses_current_columns_not_revision_json():
    value=snapshot(event())
    assert value["title_ja"]=="ベトナム中銀が政策金利を決定"
    assert value["publication_status"]=="needs_review" and value["version"]==2

def test_human_validation_guards_publication_values():
    with pytest.raises(HTTPException): validate_human_values({"must_include":True,"must_include_reason":""})
    with pytest.raises(HTTPException): validate_human_values({"relevance_status":"out_of_scope","importance_level":"high"})

def test_clustering_is_a_similarity_candidate_not_an_automatic_merge():
    assert title_similarity("Vietnam central bank rate decision","Vietnam central bank decides rate") > .55
    assert title_similarity("Vietnam central bank rate decision","Football club wins match") < .55

@pytest.mark.asyncio
async def test_approval_requires_reason_source_target_and_complete_importance():
    class DB:
        async def scalar(self,query): return 1
    value=event()
    with pytest.raises(HTTPException): await require_publishable(DB(),value,"")
    value.relevance_status=Relevance.uncertain
    with pytest.raises(HTTPException): await require_publishable(DB(),value,"checked")
    value.relevance_status=Relevance.target; value.importance_reason=""
    with pytest.raises(HTTPException): await require_publishable(DB(),value,"checked")
    value.importance_reason="national"; value.merged_into_event_id=uuid.uuid4()
    with pytest.raises(HTTPException): await require_publishable(DB(),value,"checked")

def test_event_mutation_requires_auth_and_csrf():
    value=event(); user=SimpleNamespace(id=uuid.uuid4(),is_admin=True); session=SimpleNamespace(csrf_token_hash=__import__("vietnam_calendar.security",fromlist=["token_hash"]).token_hash("csrf"))
    class Scalars:
        def one_or_none(self): return value
    class DB:
        async def scalars(self,query): return Scalars()
        async def flush(self): pass
        async def commit(self): pass
        def add(self,item): pass
    async def db(): yield DB()
    async def auth(): return session,user
    app.dependency_overrides[get_session]=db
    try:
        with TestClient(app) as client:
            assert client.patch(f"/api/v1/events/{value.id}",json={"version":2,"reason":"checked","summary_ja":"changed"}).status_code==401
        app.dependency_overrides[current_session]=auth
        with TestClient(app) as client:
            assert client.patch(f"/api/v1/events/{value.id}",json={"version":2,"reason":"checked","summary_ja":"changed"}).status_code==403
            response=client.patch(f"/api/v1/events/{value.id}",headers={"x-csrf-token":"csrf"},json={"version":2,"reason":"checked","summary_ja":"changed"})
            assert response.status_code==200 and response.json()["version"]==3
            stale=client.patch(f"/api/v1/events/{value.id}",headers={"x-csrf-token":"csrf"},json={"version":2,"reason":"stale","summary_ja":"lost update"})
            assert stale.status_code==409
    finally: app.dependency_overrides.clear()
