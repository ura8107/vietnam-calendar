from datetime import UTC,datetime,timedelta
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from vietnam_calendar import api
from vietnam_calendar.api import FeedCreate,FeedPatch,FeedUrlTest
from vietnam_calendar.config import Settings
from vietnam_calendar.models import AuditLog,Feed,Session
from vietnam_calendar.security import token_hash


def request(method="POST"):
    return Request({"type":"http","method":method,"path":"/api/v1/feeds","headers":[],"query_string":b"","server":("test",443),"client":("test",1),"scheme":"https"})


def admin():
    session=Session(id=uuid.uuid4(),user_id=uuid.uuid4(),token_hash="x"*64,csrf_token_hash=token_hash("csrf"),expires_at=datetime.now(UTC)+timedelta(hours=1),last_seen_at=datetime.now(UTC))
    return session,type("User",(),{"id":uuid.uuid4()})()


class Scalars:
    def __init__(self,value): self.value=value
    def one_or_none(self): return self.value


class CreateDB:
    def __init__(self,scalars=(0,0),commit_error=None): self.scalar_values=iter(scalars);self.added=[];self.commit_error=commit_error;self.rollbacks=0
    async def scalar(self,q): return next(self.scalar_values)
    async def rollback(self): self.rollbacks+=1
    def add(self,value): self.added.append(value)
    async def flush(self): pass
    async def commit(self):
        if self.commit_error: raise self.commit_error
    async def refresh(self,feed):
        feed.created_at=feed.updated_at=datetime.now(UTC)


@pytest.mark.asyncio
async def test_create_validates_after_transaction_release_and_audits_all_fields(monkeypatch):
    db=CreateDB();calls=[]
    actor_id=uuid.uuid4()
    class ExpiringUser:
        reads=0
        @property
        def id(self):
            self.reads+=1
            if self.reads>1: raise RuntimeError("simulated expired ORM access / MissingGreenlet")
            return actor_id
    async def validate(url,settings): calls.append((url,db.rollbacks));return 2
    monkeypatch.setattr(api,"validate_feed_source",validate)
    session,_=admin()
    result=await api.create_feed(FeedCreate(name="News",url="https://news.tuoitre.vn/home.rss#x",publisher="TT",declared_language="en",default_category="society",trust_score=80),request(),db,(session,ExpiringUser()),Settings(rss_allowed_hosts="news.tuoitre.vn"),"csrf")
    assert calls==[("https://news.tuoitre.vn/home.rss",1)]
    assert result.trust_score==80
    audit=next(item for item in db.added if isinstance(item,AuditLog))
    assert audit.after_values=={"name":"News","url":"https://news.tuoitre.vn/home.rss","publisher":"TT","declared_language":"en","default_category":"society","fetch_interval_minutes":30,"enabled":True,"trust_score":80,"version":1}


@pytest.mark.asyncio
async def test_create_rejects_csrf_duplicate_validation_and_unique_race(monkeypatch):
    body=FeedCreate(name="News",url="https://news.tuoitre.vn/home.rss",publisher="TT")
    with pytest.raises(HTTPException) as csrf: await api.create_feed(body,request(),CreateDB(),admin(),Settings(rss_allowed_hosts="news.tuoitre.vn"),None)
    assert csrf.value.status_code==403
    with pytest.raises(HTTPException) as duplicate: await api.create_feed(body,request(),CreateDB((1,)),admin(),Settings(rss_allowed_hosts="news.tuoitre.vn"),"csrf")
    assert duplicate.value.status_code==409
    async def invalid(url,settings):
        from vietnam_calendar.infrastructure.feeds.rss import InvalidFeed
        raise InvalidFeed("empty")
    monkeypatch.setattr(api,"validate_feed_source",invalid);failed=CreateDB()
    with pytest.raises(HTTPException) as bad: await api.create_feed(body,request(),failed,admin(),Settings(rss_allowed_hosts="news.tuoitre.vn"),"csrf")
    assert bad.value.status_code==422 and not any(isinstance(x,Feed) for x in failed.added)
    async def valid(url,settings): return 1
    monkeypatch.setattr(api,"validate_feed_source",valid)
    race=CreateDB(commit_error=IntegrityError("insert",{},Exception("unique")))
    with pytest.raises(HTTPException) as conflict: await api.create_feed(body,request(),race,admin(),Settings(rss_allowed_hosts="news.tuoitre.vn"),"csrf")
    assert conflict.value.status_code==409 and race.rollbacks==2


class PatchDB:
    def __init__(self,feed,locked=None,duplicate=0,commit_error=None): self.initial=feed;self.locked=locked or feed;self.duplicate=duplicate;self.commit_error=commit_error;self.rollbacks=0;self.added=[]
    async def get(self,model,key): return self.initial
    async def rollback(self): self.rollbacks+=1
    async def scalars(self,q): return Scalars(self.locked)
    async def scalar(self,q): return self.duplicate
    def add(self,value): self.added.append(value)
    async def commit(self):
        if self.commit_error: raise self.commit_error
    async def refresh(self,feed): pass


def feed(enabled=True,version=1):
    now=datetime.now(UTC)
    return Feed(id=uuid.uuid4(),name="old",url="https://news.tuoitre.vn/old.rss",normalized_url="https://news.tuoitre.vn/old.rss",publisher="TT",declared_language="en",default_category=None,trust_score=50,enabled=enabled,fetch_interval_minutes=30,etag="etag",last_modified="date",next_fetch_at=now,last_success_at=None,last_failure_at=None,consecutive_failures=0,version=version,created_at=now,updated_at=now)


@pytest.mark.asyncio
async def test_patch_validation_rules_failure_preserves_and_url_resets_cache(monkeypatch):
    settings=Settings(rss_allowed_hosts="news.tuoitre.vn");calls=[]
    actor_id=uuid.uuid4()
    class ExpiringUser:
        reads=0
        @property
        def id(self):
            self.reads+=1
            if self.reads>1: raise RuntimeError("simulated expired ORM access / MissingGreenlet")
            return actor_id
    async def valid(url,s): calls.append(url);return 1
    monkeypatch.setattr(api,"validate_feed_source",valid)
    current=feed();db=PatchDB(current)
    session,_=admin()
    result=await api.patch_feed(current.id,FeedPatch(version=1,url="https://news.tuoitre.vn/new.rss"),request("PATCH"),db,(session,ExpiringUser()),settings,"csrf")
    assert calls==["https://news.tuoitre.vn/new.rss"] and current.etag is None
    assert current.last_modified is None and current.version==2
    calls.clear();current=feed();await api.patch_feed(current.id,FeedPatch(version=1,name="metadata"),request("PATCH"),PatchDB(current),admin(),settings,"csrf");assert calls==[]
    current=feed(enabled=False);await api.patch_feed(current.id,FeedPatch(version=1,enabled=True),request("PATCH"),PatchDB(current),admin(),settings,"csrf");assert calls==[current.normalized_url]
    from vietnam_calendar.infrastructure.feeds.rss import InvalidFeed
    async def invalid(url,s): raise InvalidFeed("empty")
    monkeypatch.setattr(api,"validate_feed_source",invalid);current=feed();old=(current.url,current.version,current.etag)
    with pytest.raises(HTTPException): await api.patch_feed(current.id,FeedPatch(version=1,url="https://news.tuoitre.vn/bad.rss"),request("PATCH"),PatchDB(current),admin(),settings,"csrf")
    assert (current.url,current.version,current.etag)==old


@pytest.mark.asyncio
async def test_patch_version_and_unique_commit_races_are_409(monkeypatch):
    async def valid(url,s): return 1
    monkeypatch.setattr(api,"validate_feed_source",valid);settings=Settings(rss_allowed_hosts="news.tuoitre.vn")
    initial,locked=feed(),feed(version=2);locked.id=initial.id
    with pytest.raises(HTTPException) as version: await api.patch_feed(initial.id,FeedPatch(version=1,url="https://news.tuoitre.vn/new.rss"),request("PATCH"),PatchDB(initial,locked),admin(),settings,"csrf")
    assert version.value.status_code==409
    current=feed();db=PatchDB(current,commit_error=IntegrityError("update",{},Exception("unique")))
    with pytest.raises(HTTPException) as unique: await api.patch_feed(current.id,FeedPatch(version=1,url="https://news.tuoitre.vn/new.rss"),request("PATCH"),db,admin(),settings,"csrf")
    assert unique.value.status_code==409 and db.rollbacks==2


@pytest.mark.asyncio
@pytest.mark.parametrize("status",[304,500])
async def test_shared_validator_rejects_every_non_200(monkeypatch,status):
    from vietnam_calendar.infrastructure.feeds import rss
    class Client:
        async def fetch(self,url): return rss.FetchResult(status,None,None,None,url)
        async def aclose(self): pass
    monkeypatch.setattr(rss,"SafeFeedClient",lambda settings:Client())
    with pytest.raises(rss.FeedError) as error: await api.validate_feed_source("https://news.tuoitre.vn/home.rss",Settings())
    assert error.value.http_status == status


@pytest.mark.asyncio
async def test_shared_validator_accepts_200_parseable_feed(monkeypatch):
    from pathlib import Path
    from vietnam_calendar.infrastructure.feeds import rss
    body=(Path(__file__).parents[1]/"fixtures/feeds/tuoitre-home.xml").read_bytes()
    class Client:
        async def fetch(self,url): return rss.FetchResult(200,body,None,None,url)
        async def aclose(self): pass
    monkeypatch.setattr(rss,"SafeFeedClient",lambda settings:Client())
    assert await api.validate_feed_source("https://news.tuoitre.vn/home.rss",Settings()) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("body",[b"",b"not an rss document"])
async def test_shared_validator_rejects_empty_or_invalid_feed(monkeypatch,body):
    from vietnam_calendar.infrastructure.feeds import rss
    class Client:
        async def fetch(self,url): return rss.FetchResult(200,body,None,None,url)
        async def aclose(self): pass
    monkeypatch.setattr(rss,"SafeFeedClient",lambda settings:Client())
    with pytest.raises(rss.InvalidFeed): await api.validate_feed_source("https://news.tuoitre.vn/home.rss",Settings())


class AuditDB:
    def __init__(self,saved_feed=None): self.saved_feed=saved_feed;self.added=[];self.rollbacks=0;self.commits=0
    async def get(self,model,key): return self.saved_feed
    async def rollback(self): self.rollbacks+=1
    def add(self,value): self.added.append(value)
    async def commit(self): self.commits+=1


@pytest.mark.asyncio
async def test_url_tests_share_success_validation_and_audit(monkeypatch):
    calls=[]
    async def valid(url,settings): calls.append(url);return 3
    monkeypatch.setattr(api,"validate_feed_source",valid);settings=Settings(rss_allowed_hosts="news.tuoitre.vn")
    candidate_db=AuditDB();candidate=await api.test_feed_url(FeedUrlTest(url="https://news.tuoitre.vn/home.rss"),request(),candidate_db,admin(),settings,"csrf")
    assert candidate=={"reachable":True,"http_status":200,"accepted_entries":3}
    assert next(x for x in candidate_db.added if isinstance(x,AuditLog)).action=="feed.url_test"
    saved=feed();registered_db=AuditDB(saved);registered=await api.test_feed(saved.id,request(),registered_db,admin(),settings,"csrf")
    assert registered["accepted_entries"]==3 and registered_db.rollbacks==1
    assert next(x for x in registered_db.added if isinstance(x,AuditLog)).action=="feed.test"
    assert calls==["https://news.tuoitre.vn/home.rss",saved.url]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid",[False,True])
async def test_url_tests_audit_safe_failures(monkeypatch,invalid):
    from vietnam_calendar.infrastructure.feeds.rss import FeedError,InvalidFeed
    async def fail(url,settings):
        if invalid: raise InvalidFeed("empty")
        raise FeedError("non-200",http_status=304)
    monkeypatch.setattr(api,"validate_feed_source",fail);settings=Settings(rss_allowed_hosts="news.tuoitre.vn")
    db=AuditDB()
    with pytest.raises(HTTPException) as error: await api.test_feed_url(FeedUrlTest(url="https://news.tuoitre.vn/home.rss"),request(),db,admin(),settings,"csrf")
    assert error.value.status_code==(422 if invalid else 503)
    audit=next(x for x in db.added if isinstance(x,AuditLog));assert audit.action=="feed.url_test_failed" and audit.after_values["error_code"] in {"invalid_feed","feed_error"}
    saved=feed();db=AuditDB(saved)
    with pytest.raises(HTTPException): await api.test_feed(saved.id,request(),db,admin(),settings,"csrf")
    assert next(x for x in db.added if isinstance(x,AuditLog)).action=="feed.test_failed"
