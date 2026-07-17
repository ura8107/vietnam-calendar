from datetime import UTC, datetime, timedelta
import uuid

from fastapi.testclient import TestClient

from vietnam_calendar.api import LoginLimiter, app, current_session, should_update_last_seen
from vietnam_calendar.db import get_session
from vietnam_calendar.models import Session, User
from vietnam_calendar.security import token_hash


class FakeDB:
    def add(self, value): pass
    async def commit(self): pass
    async def execute(self, value): raise RuntimeError("database unavailable")

async def fake_db(): yield FakeDB()

def auth_pair(*, admin=True, csrf="csrf"):
    user=User(id=uuid.uuid4(),username="admin",password_hash="x",is_active=True,is_admin=admin)
    session=Session(id=uuid.uuid4(),user_id=user.id,token_hash="x"*64,csrf_token_hash=token_hash(csrf),expires_at=datetime.now(UTC)+timedelta(hours=1),last_seen_at=datetime.now(UTC))
    return session,user

def test_logout_cookie_mirrors_security_attributes():
    async def auth(): return auth_pair()
    app.dependency_overrides[current_session]=auth; app.dependency_overrides[get_session]=fake_db
    try:
        with TestClient(app) as client: response=client.post("/api/v1/auth/logout",headers={"x-csrf-token":"csrf"})
        cookie=response.headers["set-cookie"].lower(); assert response.status_code==204; assert "httponly" in cookie; assert "samesite=strict" in cookie; assert "path=/" in cookie; assert "max-age=0" in cookie
    finally: app.dependency_overrides.clear()

def test_logout_rejects_bad_csrf_with_stable_error():
    async def auth(): return auth_pair()
    app.dependency_overrides[current_session]=auth; app.dependency_overrides[get_session]=fake_db
    try:
        with TestClient(app) as client: response=client.post("/api/v1/auth/logout",headers={"x-csrf-token":"bad","x-request-id":"fixed"})
        assert response.status_code==403; assert response.json()["code"]=="http_403"; assert response.json()["message"]=="invalid CSRF token"; assert response.json()["request_id"]!="fixed"
    finally: app.dependency_overrides.clear()

def test_non_admin_cannot_list_feeds():
    async def auth(): return auth_pair(admin=False)
    app.dependency_overrides[current_session]=auth; app.dependency_overrides[get_session]=fake_db
    try:
        with TestClient(app) as client: response=client.get("/api/v1/feeds")
        assert response.status_code==403
    finally: app.dependency_overrides.clear()

def test_ready_reports_database_failure_without_leaking_exception():
    app.dependency_overrides[get_session]=fake_db
    try:
        with TestClient(app) as client: response=client.get("/readyz",headers={"x-request-id":"ready-test"})
        assert response.status_code==503; assert response.json()["request_id"]!="ready-test"; assert response.json()["code"]=="http_503"
    finally: app.dependency_overrides.clear()

def test_ready_rejects_stale_migration(monkeypatch):
    class Result:
        def __init__(self,value=None): self.value=value
        def scalar_one(self): return self.value
    class StaleDB:
        calls=0
        async def execute(self,value):
            self.calls+=1; return Result(None if self.calls==1 else "old-head")
    class Script:
        def get_heads(self): return ["new-head"]
    async def stale_db(): yield StaleDB()
    monkeypatch.setattr("vietnam_calendar.api.ScriptDirectory.from_config",lambda config: Script())
    app.dependency_overrides[get_session]=stale_db
    try:
        with TestClient(app) as client: response=client.get("/readyz")
        assert response.status_code==503; assert response.json()["message"]=="database or migration is not ready"
    finally: app.dependency_overrides.clear()

def test_validation_never_echoes_password_or_input():
    secret="unique-super-secret-value"
    # Force validation failure using an overlong username while keeping secrets in input.
    with TestClient(app) as client: response=client.post("/api/v1/auth/login",json={"username":"x"*101,"password":secret})
    assert response.status_code==422; assert secret not in response.text; assert "input" not in response.json()["details"][0]

def test_request_id_rejects_long_and_malformed_values():
    import uuid
    for supplied in ("x"*1000,"not-a-uuid"):
        with TestClient(app) as client: response=client.get("/healthz",headers={"x-request-id":supplied})
        assert response.headers["x-request-id"] != supplied; uuid.UUID(response.headers["x-request-id"])

def test_login_limiter_is_ip_keyed_bounded_and_evicts_stale(monkeypatch):
    limiter=LoginLimiter(limit=2,window=10,max_keys=2); now=[100.0]; monkeypatch.setattr("vietnam_calendar.api.time.monotonic",lambda:now[0])
    assert limiter.client_key("127.0.0.1")=="127.0.0.1"; assert limiter.client_key("garbage")=="unknown"
    key=limiter.client_key("127.0.0.1"); limiter.fail(key); limiter.fail(key)
    # Rotating usernames cannot rotate the key; attempts remain on the client IP.
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException): limiter.check(key)
    limiter.fail("10.0.0.1"); limiter.fail("10.0.0.2"); assert len(limiter.attempts)<=2
    now[0]=111.0; limiter.check("10.0.0.3"); assert set(limiter.attempts)=={"10.0.0.3"}

def test_last_seen_is_throttled():
    now=datetime.now(UTC)
    assert not should_update_last_seen(now-timedelta(minutes=4),now)
    assert should_update_last_seen(now-timedelta(minutes=6),now)


def test_manual_fetch_requires_csrf_and_is_idempotent_202(monkeypatch):
    feed_id=uuid.uuid4(); job_id=uuid.uuid4(); calls=[job_id,None]
    class Scalars:
        def one_or_none(self): return job_id
    class DB:
        async def get(self,model,key):
            from vietnam_calendar.models import Feed
            return Feed(id=feed_id,name="feed",url="https://news.tuoitre.vn/home.rss",normalized_url="https://news.tuoitre.vn/home.rss",publisher="Tuoi Tre",enabled=True,fetch_interval_minutes=30)
        async def scalars(self,query): return Scalars()
        def add(self,value): pass
        async def commit(self): pass
    async def db(): yield DB()
    async def auth(): return auth_pair()
    async def fake_enqueue(*args,**kwargs): return calls.pop(0)
    monkeypatch.setattr("vietnam_calendar.api.enqueue",fake_enqueue)
    app.dependency_overrides[current_session]=auth; app.dependency_overrides[get_session]=db
    try:
        with TestClient(app) as client:
            denied=client.post(f"/api/v1/feeds/{feed_id}/fetch")
            first=client.post(f"/api/v1/feeds/{feed_id}/fetch",headers={"x-csrf-token":"csrf"})
            duplicate=client.post(f"/api/v1/feeds/{feed_id}/fetch",headers={"x-csrf-token":"csrf"})
        assert denied.status_code==403
        assert first.status_code==202 and first.json()=={"job_id":str(job_id),"created":True}
        assert duplicate.status_code==202 and duplicate.json()=={"job_id":str(job_id),"created":False}
    finally: app.dependency_overrides.clear()
