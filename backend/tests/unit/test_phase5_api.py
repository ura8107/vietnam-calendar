from datetime import UTC, date, datetime, timedelta
import uuid
import pytest

from fastapi.testclient import TestClient

from vietnam_calendar.api import FeedCreate,FeedPatch, app, current_session,normalized_feed_url
from vietnam_calendar.config import Settings
from vietnam_calendar.db import get_session
from vietnam_calendar.models import Session, User


class Rows:
    def all(self):
        return [(date(2026, 7, 18), 3, 4, True, ["economy","policy"]), (date(2026, 7, 21), 1, 2, False, ["society"])]


class DB:
    async def execute(self, query): return Rows()


async def db(): yield DB()


async def auth():
    user = User(id=uuid.uuid4(), username="admin", password_hash="x", is_active=True, is_admin=True)
    session = Session(id=uuid.uuid4(), user_id=user.id, token_hash="x" * 64, csrf_token_hash="y" * 64,
                      expires_at=datetime.now(UTC) + timedelta(hours=1), last_seen_at=datetime.now(UTC))
    return session, user


def test_calendar_month_projection_and_must_include_label_data():
    app.dependency_overrides[current_session] = auth
    app.dependency_overrides[get_session] = db
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/calendar?month=2026-07")
        assert response.status_code == 200
        assert response.json() == {"month": "2026-07", "days": [
            {"date": "2026-07-18", "count": 3, "highest_importance": "high", "has_must_include": True, "categories":["economy","policy"]},
            {"date": "2026-07-21", "count": 1, "highest_importance": "middle", "has_must_include": False, "categories":["society"]},
        ]}
    finally:
        app.dependency_overrides.clear()


def test_calendar_requires_auth_and_rejects_non_month_values():
    with TestClient(app) as client:
        assert client.get("/api/v1/calendar?month=2026-07").status_code == 401
    app.dependency_overrides[current_session] = auth
    app.dependency_overrides[get_session] = db
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/calendar?month=2026-7")
        assert response.status_code == 422
        assert response.json()["message"] == "month must be YYYY-MM"
    finally:
        app.dependency_overrides.clear()

def test_event_search_treats_percent_and_underscore_as_literals():
    from sqlalchemy.dialects import postgresql
    from vietnam_calendar.models import Event
    literal="50%_done\\today".replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
    statement=Event.title_ja.ilike(f"%{literal}%",escape="\\")
    compiled=str(statement.compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    assert "ESCAPE '\\\\'" in compiled
    assert statement.right.value == "%50\\%\\_done\\\\today%"

def test_phase5_openapi_exposes_combined_filters_and_guarded_operations():
    schema=app.openapi(); events=schema["paths"]["/api/v1/events"]["get"]
    parameters={item["name"] for item in events["parameters"]}
    assert {"date_from","date_to","publisher","source_feed_id","category","importance","q","offset","limit"} <= parameters
    assert "patch" in schema["paths"]["/api/v1/feeds/{feed_id}"]
    assert "post" in schema["paths"]["/api/v1/feeds"]
    assert "post" in schema["paths"]["/api/v1/feeds/test-url"]
    assert "post" in schema["paths"]["/api/v1/feeds/{feed_id}/test"]
    assert "post" in schema["paths"]["/api/v1/jobs/{job_id}/retry"]

def test_feed_patch_interval_is_bounded():
    from pydantic import ValidationError
    with pytest.raises(ValidationError): FeedPatch(version=1,fetch_interval_minutes=4)
    with pytest.raises(ValidationError): FeedPatch(version=1,fetch_interval_minutes=1441)

def test_feed_trust_score_is_nullable_and_bounded():
    from pydantic import ValidationError
    assert FeedCreate(name="x",url="https://news.tuoitre.vn/home.rss",publisher="x").trust_score is None
    assert FeedPatch(version=1,trust_score=100).trust_score == 100
    with pytest.raises(ValidationError): FeedCreate(name="x",url="https://news.tuoitre.vn/home.rss",publisher="x",trust_score=-1)
    with pytest.raises(ValidationError): FeedPatch(version=1,trust_score=101)

def test_feed_url_requires_allowlisted_credential_free_https():
    settings=Settings(rss_allowed_hosts="news.tuoitre.vn")
    assert normalized_feed_url("https://news.tuoitre.vn/home.rss#fragment",settings)=="https://news.tuoitre.vn/home.rss"
    from fastapi import HTTPException
    for value in ("http://news.tuoitre.vn/home.rss","https://user:pass@news.tuoitre.vn/home.rss","https://example.com/feed"):
        with pytest.raises(HTTPException): normalized_feed_url(value,settings)

def test_job_retry_requires_dead_state_and_csrf():
    from vietnam_calendar.models import Job,JobStatus,JobType
    from vietnam_calendar.security import token_hash
    job=Job(id=uuid.uuid4(),job_type=JobType.fetch_feed,payload={"feed_id":str(uuid.uuid4())},dedupe_key="feed:x",status=JobStatus.succeeded,attempts=1,max_attempts=3,run_after=datetime.now(UTC),created_at=datetime.now(UTC),updated_at=datetime.now(UTC))
    class Scalars:
        def one_or_none(self): return job
    class RetryDB:
        async def scalars(self,query): return Scalars()
        async def scalar(self,query): return 0
        async def get(self,model,key): return type("RetryFeed",(),{"enabled":True})()
        def add(self,value): pass
        async def commit(self): pass
    retry_db=RetryDB()
    async def override_db(): yield retry_db
    async def override_auth():
        user=User(id=uuid.uuid4(),username="admin",password_hash="x",is_active=True,is_admin=True)
        session=Session(id=uuid.uuid4(),user_id=user.id,token_hash="x"*64,csrf_token_hash=token_hash("csrf"),expires_at=datetime.now(UTC)+timedelta(hours=1),last_seen_at=datetime.now(UTC))
        return session,user
    app.dependency_overrides[current_session]=override_auth;app.dependency_overrides[get_session]=override_db
    try:
        with TestClient(app) as client:
            denied=client.post(f"/api/v1/jobs/{job.id}/retry")
            wrong_state=client.post(f"/api/v1/jobs/{job.id}/retry",headers={"x-csrf-token":"csrf"})
            job.status=JobStatus.dead
            accepted=client.post(f"/api/v1/jobs/{job.id}/retry",headers={"x-csrf-token":"csrf"})
        assert denied.status_code==403 and wrong_state.status_code==409
        assert accepted.status_code==202 and job.status==JobStatus.queued and job.attempts==0
    finally: app.dependency_overrides.clear()
