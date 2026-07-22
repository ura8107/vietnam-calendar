import hmac
import ipaddress
import re
import time
import uuid
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlsplit,urlunsplit

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import engine, get_session
from .jobs import enqueue
from .models import (AIRun, AIRunStatus, Article, AuditLog, Certainty, ClusterCandidateStatus, DateCertainty,
                     Event, EventArticle, EventClusterCandidate, EventRevision, Feed,
                     FetchRun, Importance, Job, JobStatus, JobType, PublicationStatus,
                     Relevance, Review, ReviewDecision, Session, User)
from .events import decide_cluster_candidate, merge_events, revise_event, review_event, snapshot, split_event
from .analysis import build_provider
from .application.ai import ArticleInput,EventAnalysisRequest
from .application.evals import evaluate_rules
from .infrastructure.ai.providers import AIProviderError
from .security import random_token, token_hash, verify_password

DUMMY_ARGON2_HASH = "$argon2id$v=19$m=65536,t=3,p=4$7NPVOF0sfNLwDLGW8SvqzQ$9hxBQrk5NIizcQRb7GFouaNQSnzQNwWct4gDQT6izgM"

class ErrorBody(BaseModel): code: str; message: str; request_id: str; details: Any = None
class LoginInput(BaseModel): username: str=Field(min_length=1,max_length=100); password: str=Field(min_length=8,max_length=1024)
class LoginOutput(BaseModel): csrf_token: str
class MeOutput(BaseModel): id: str; username: str; is_admin: bool; csrf_token: str
class FeedOutput(BaseModel):
    id: str; name: str; url: str; publisher: str; declared_language:str|None; default_category:str|None; enabled: bool; fetch_interval_minutes: int
    trust_score:int|None; next_fetch_at: datetime; last_success_at: datetime|None; last_failure_at: datetime|None; consecutive_failures: int; updated_at: datetime; version:int
class FeedCreate(BaseModel):
    name:str=Field(min_length=1,max_length=200);url:str=Field(min_length=1,max_length=2000);publisher:str=Field(min_length=1,max_length=200);declared_language:str|None=Field(default=None,max_length=35);default_category:str|None=Field(default=None,max_length=60);trust_score:int|None=Field(default=None,ge=0,le=100);fetch_interval_minutes:int=Field(default=30,ge=5,le=1440);enabled:bool=True
class FeedPatch(BaseModel):
    version:int=Field(ge=1);enabled:bool|None=None;fetch_interval_minutes:int|None=Field(default=None,ge=5,le=1440);name:str|None=Field(default=None,min_length=1,max_length=200);url:str|None=Field(default=None,min_length=1,max_length=2000);publisher:str|None=Field(default=None,min_length=1,max_length=200);declared_language:str|None=Field(default=None,max_length=35);default_category:str|None=Field(default=None,max_length=60);trust_score:int|None=Field(default=None,ge=0,le=100)
class FeedUrlTest(BaseModel): url:str=Field(min_length=1,max_length=2000)
class JobAccepted(BaseModel): job_id: str; created: bool
class EventPage(BaseModel): items:list[dict[str,Any]]; total:int; offset:int; limit:int; has_more:bool
class EventPatch(BaseModel):
    version:int=Field(ge=1); reason:str=Field(min_length=1,max_length=1000)
    title_ja:str|None=Field(default=None,min_length=1,max_length=500); summary_ja:str|None=Field(default=None,min_length=1,max_length=10000); event_date:date|None=None
    date_certainty:DateCertainty|None=None; category:str|None=Field(default=None,min_length=1,max_length=60); relevance_status:Relevance|None=None; relevance_reason:str|None=Field(default=None,max_length=4000)
    importance_level:Importance|None=None; importance_score:int|None=Field(default=None,ge=0,le=100); importance_reason:str|None=Field(default=None,max_length=4000); must_include:bool|None=None; must_include_reason:str|None=Field(default=None,max_length=4000); certainty:Certainty|None=None
class ReviewInput(BaseModel): version:int=Field(ge=1); decision:ReviewDecision; reason:str|None=Field(default=None,max_length=4000); uncertainty_note:str|None=Field(default=None,max_length=4000)
class MergeInput(BaseModel): source_event_id:uuid.UUID; target_version:int=Field(ge=1); source_version:int=Field(ge=1); reason:str=Field(min_length=1,max_length=4000)
class SplitEventValues(BaseModel):
    title_ja:str=Field(min_length=1,max_length=500); summary_ja:str=Field(min_length=1,max_length=10000); event_date:date; date_certainty:DateCertainty; category:str=Field(min_length=1,max_length=60); relevance_status:Relevance; relevance_reason:str|None=None; importance_level:Importance|None=None; importance_score:int|None=Field(default=None,ge=0,le=100); importance_reason:str|None=None; must_include:bool=False; must_include_reason:str|None=None; certainty:Certainty
class SplitInput(BaseModel): version:int=Field(ge=1); article_ids:list[uuid.UUID]=Field(min_length=1); event:SplitEventValues; reason:str=Field(min_length=1,max_length=4000)
class CandidateReviewInput(BaseModel): status:ClusterCandidateStatus; reason:str=Field(min_length=1,max_length=4000)

class LoginLimiter:
    def __init__(self, limit:int=5, window:int=60, max_keys:int=4096): self.limit=limit; self.window=window; self.max_keys=max_keys; self.attempts: OrderedDict[str,deque[float]]=OrderedDict()
    def client_key(self,host:str)->str:
        try: return ipaddress.ip_address(host).compressed
        except ValueError: return "unknown"
    def _evict(self,now:float)->None:
        for key in list(self.attempts):
            q=self.attempts[key]
            while q and q[0] < now-self.window: q.popleft()
            if not q: self.attempts.pop(key,None)
        while len(self.attempts)>=self.max_keys: self.attempts.popitem(last=False)
    def check(self,key:str)->None:
        now=time.monotonic(); self._evict(now); q=self.attempts.setdefault(key,deque()); self.attempts.move_to_end(key)
        while q and q[0] < now-self.window: q.popleft()
        if len(q)>=self.limit: raise HTTPException(429,"too many login attempts")
    def fail(self,key:str)->None:
        now=time.monotonic(); self._evict(now); self.attempts.setdefault(key,deque()).append(now); self.attempts.move_to_end(key)
    def clear(self,key:str)->None: self.attempts.pop(key,None)
login_limiter=LoginLimiter()

@asynccontextmanager
async def lifespan(app:FastAPI):
    yield
    await engine.dispose()

app=FastAPI(title="Vietnam Calendar API",version="0.1.0",lifespan=lifespan)
DB=Annotated[AsyncSession,Depends(get_session)]

def safe_request_id(value:str|None)->str:
    if value:
        try: return str(uuid.UUID(value))
        except ValueError: pass
    return str(uuid.uuid4())
def request_id(request:Request)->str: return getattr(request.state,"request_id",str(uuid.uuid4()))
@app.middleware("http")
async def request_id_middleware(request:Request,call_next):
    request.state.request_id=safe_request_id(request.headers.get("x-request-id")); response=await call_next(request); response.headers["x-request-id"]=request.state.request_id; return response
@app.exception_handler(HTTPException)
async def http_error(request:Request,exc:HTTPException):
    messages={401:"authentication required",403:"forbidden",429:"too many requests",503:"service unavailable"}; message=exc.detail if isinstance(exc.detail,str) else messages.get(exc.status_code,"request failed")
    return JSONResponse(status_code=exc.status_code,content=ErrorBody(code=f"http_{exc.status_code}",message=message,request_id=request_id(request),details=None).model_dump())
@app.exception_handler(RequestValidationError)
async def validation_error(request:Request,exc:RequestValidationError):
    safe=[]
    for error in exc.errors(): safe.append({k:error[k] for k in ("type","loc","msg") if k in error})
    return JSONResponse(status_code=422,content=ErrorBody(code="validation_error",message="request validation failed",request_id=request_id(request),details=safe).model_dump())
@app.exception_handler(Exception)
async def internal_error(request:Request,exc:Exception):
    return JSONResponse(status_code=500,content=ErrorBody(code="internal_error",message="internal server error",request_id=request_id(request),details=None).model_dump())

def should_update_last_seen(last_seen:datetime,now:datetime)->bool: return last_seen < now-timedelta(minutes=5)
async def current_session(request:Request,db:DB,settings:Annotated[Settings,Depends(get_settings)])->tuple[Session,User]:
    raw=request.cookies.get(settings.session_cookie_name)
    if not raw: raise HTTPException(401,"authentication required")
    now=datetime.now(UTC); row=(await db.execute(select(Session,User).join(User).where(Session.token_hash==token_hash(raw),Session.revoked_at.is_(None),Session.expires_at>now,User.is_active.is_(True)))).first()
    if row is None: raise HTTPException(401,"invalid or expired session")
    session,user=row
    if should_update_last_seen(session.last_seen_at,now): session.last_seen_at=now; await db.commit()
    return session,user
Auth=Annotated[tuple[Session,User],Depends(current_session)]
async def admin_user(auth:Auth)->tuple[Session,User]:
    if not auth[1].is_admin: raise HTTPException(403,"administrator required")
    return auth
Admin=Annotated[tuple[Session,User],Depends(admin_user)]

@app.get("/healthz")
async def healthz()->dict[str,str]: return {"status":"ok"}
@app.get("/readyz")
async def readyz(db:DB)->dict[str,str]:
    try:
        await db.execute(text("SELECT 1")); version=(await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one(); heads=ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
        if version not in heads: raise RuntimeError("stale migration")
    except Exception as exc: raise HTTPException(503,"database or migration is not ready") from exc
    return {"status":"ready","migration":version}

@app.post("/api/v1/auth/login",response_model=LoginOutput)
async def login(body:LoginInput,response:Response,request:Request,db:DB,settings:Annotated[Settings,Depends(get_settings)]):
    key=login_limiter.client_key(request.client.host if request.client else "unknown"); login_limiter.check(key)
    user=(await db.execute(select(User).where(User.username==body.username,User.is_active.is_(True)))).scalar_one_or_none(); valid=verify_password(user.password_hash if user else DUMMY_ARGON2_HASH,body.password)
    if user is None or not valid: login_limiter.fail(key); raise HTTPException(401,"invalid credentials")
    login_limiter.clear(key); now=datetime.now(UTC); await db.execute(update(Session).where(Session.user_id==user.id,Session.revoked_at.is_(None)).values(revoked_at=now))
    token=random_token(); stored_token_hash=token_hash(token); csrf=token_hash(f"csrf:{stored_token_hash}"); db.add(Session(user_id=user.id,token_hash=stored_token_hash,csrf_token_hash=token_hash(csrf),expires_at=now+timedelta(seconds=settings.session_ttl_seconds),last_seen_at=now)); db.add(AuditLog(actor_user_id=user.id,action="auth.login",entity_type="session",request_id=request_id(request),before_values=None,after_values={"active_sessions":1},details={}))
    await db.commit(); response.set_cookie(settings.session_cookie_name,token,httponly=True,secure=settings.cookie_secure,samesite="strict",max_age=settings.session_ttl_seconds,path="/"); return LoginOutput(csrf_token=csrf)
@app.post("/api/v1/auth/logout",status_code=204)
async def logout(response:Response,request:Request,db:DB,auth:Auth,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=auth
    if not x_csrf_token or not hmac.compare_digest(token_hash(x_csrf_token),session.csrf_token_hash): raise HTTPException(403,"invalid CSRF token")
    session.revoked_at=datetime.now(UTC); db.add(AuditLog(actor_user_id=user.id,action="auth.logout",entity_type="session",entity_id=str(session.id),request_id=request_id(request),before_values={"revoked":False},after_values={"revoked":True},details={})); await db.commit()
    settings=get_settings(); response.delete_cookie(settings.session_cookie_name,path="/",secure=settings.cookie_secure,httponly=True,samesite="strict")
@app.get("/api/v1/auth/me",response_model=MeOutput)
async def me(response:Response,db:DB,auth:Auth):
    session,u=auth
    csrf=token_hash(f"csrf:{session.token_hash}")
    expected_hash=token_hash(csrf)
    if not hmac.compare_digest(session.csrf_token_hash,expected_hash): session.csrf_token_hash=expected_hash; await db.commit()
    response.headers["Cache-Control"]="no-store"
    return MeOutput(id=str(u.id),username=u.username,is_admin=u.is_admin,csrf_token=csrf)
@app.get("/api/v1/feeds",response_model=list[FeedOutput])
async def list_feeds(db:DB,admin:Admin):
    rows=(await db.scalars(select(Feed).order_by(Feed.name))).all(); return [feed_output(feed) for feed in rows]

def normalized_feed_url(value:str,settings:Settings)->str:
    try: parts=urlsplit(value.strip());port=parts.port
    except ValueError as exc: raise HTTPException(422,"feed URL is invalid") from exc
    host=(parts.hostname or "").lower().rstrip(".")
    if parts.scheme!="https" or not host or parts.username or parts.password or port not in (None,443): raise HTTPException(422,"feed URL must be credential-free HTTPS on port 443")
    if host not in settings.allowed_rss_hosts: raise HTTPException(422,"feed host is not allowlisted")
    return urlunsplit(("https",host,parts.path or "/",parts.query,""))

def feed_output(feed:Feed)->FeedOutput:
    return FeedOutput(id=str(feed.id),name=feed.name,url=feed.url,publisher=feed.publisher,declared_language=feed.declared_language,default_category=feed.default_category,trust_score=feed.trust_score,enabled=feed.enabled,fetch_interval_minutes=feed.fetch_interval_minutes,next_fetch_at=feed.next_fetch_at,last_success_at=feed.last_success_at,last_failure_at=feed.last_failure_at,consecutive_failures=feed.consecutive_failures,updated_at=feed.updated_at,version=feed.version)

async def validate_feed_source(url:str,settings:Settings)->int:
    from .infrastructure.feeds.rss import FeedError,InvalidFeed,SafeFeedClient,parse_feed
    client=SafeFeedClient(settings)
    try:
        fetched=await client.fetch(url)
        if fetched.status_code!=200: raise FeedError("unexpected HTTP status",http_status=fetched.status_code)
        parsed=parse_feed(fetched.body or b"",datetime.now(UTC),max_entries=settings.rss_max_entries,max_raw_entry_bytes=settings.rss_max_raw_entry_bytes)
        if parsed.accepted<1: raise InvalidFeed("feed yielded no acceptable entries",total=parsed.total,rejected=parsed.rejected)
        return parsed.accepted
    finally: await client.aclose()

@app.post("/api/v1/feeds",response_model=FeedOutput,status_code=201)
async def create_feed(body:FeedCreate,request:Request,db:DB,admin:Admin,settings:Annotated[Settings,Depends(get_settings)],x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin;require_csrf(session,x_csrf_token);actor_user_id=user.id;normalized=normalized_feed_url(body.url,settings)
    if await db.scalar(select(func.count()).select_from(Feed).where((Feed.url==body.url)|(Feed.normalized_url==normalized))): raise HTTPException(409,"feed URL already exists")
    await db.rollback()  # do not hold an implicit read transaction during network I/O
    try: await validate_feed_source(normalized,settings)
    except Exception as exc:
        from .infrastructure.feeds.rss import FeedError
        if not isinstance(exc,FeedError): raise
        raise HTTPException(422,f"feed validation failed: {exc.code}") from exc
    if await db.scalar(select(func.count()).select_from(Feed).where((Feed.url==body.url)|(Feed.normalized_url==normalized))): raise HTTPException(409,"feed URL already exists")
    feed=Feed(**body.model_dump(exclude={"url"}),url=normalized,normalized_url=normalized,next_fetch_at=datetime.now(UTC),consecutive_failures=0,version=1)
    try:
        db.add(feed);await db.flush();db.add(AuditLog(actor_user_id=actor_user_id,action="feed.created",entity_type="feed",entity_id=str(feed.id),request_id=request_id(request),before_values=None,after_values={"name":feed.name,"url":normalized,"publisher":feed.publisher,"declared_language":feed.declared_language,"default_category":feed.default_category,"fetch_interval_minutes":feed.fetch_interval_minutes,"enabled":feed.enabled,"trust_score":feed.trust_score,"version":feed.version},details={}));await db.commit()
    except IntegrityError as exc:
        await db.rollback();raise HTTPException(409,"feed URL already exists") from exc
    await db.refresh(feed);return feed_output(feed)

@app.post("/api/v1/feeds/test-url")
async def test_feed_url(body:FeedUrlTest,request:Request,db:DB,admin:Admin,settings:Annotated[Settings,Depends(get_settings)],x_csrf_token:Annotated[str|None,Header()]=None):
    from .infrastructure.feeds.rss import FeedError
    session,user=admin;require_csrf(session,x_csrf_token);actor_user_id=user.id;url=normalized_feed_url(body.url,settings)
    await db.rollback()
    try:
        accepted=await validate_feed_source(url,settings)
    except FeedError as exc:
        failure={"error_code":exc.code,"http_status":exc.http_status};db.add(AuditLog(actor_user_id=actor_user_id,action="feed.url_test_failed",entity_type="feed_candidate",entity_id=None,request_id=request_id(request),before_values=None,after_values=failure,details={"host":urlsplit(url).hostname}));await db.commit();raise HTTPException(422 if exc.code=="invalid_feed" else 503,f"feed test failed: {exc.code}") from exc
    result={"reachable":True,"http_status":200,"accepted_entries":accepted};db.add(AuditLog(actor_user_id=actor_user_id,action="feed.url_test",entity_type="feed_candidate",entity_id=None,request_id=request_id(request),before_values=None,after_values=result,details={"host":urlsplit(url).hostname}));await db.commit();return result

@app.patch("/api/v1/feeds/{feed_id}",response_model=FeedOutput)
async def patch_feed(feed_id:uuid.UUID,body:FeedPatch,request:Request,db:DB,admin:Admin,settings:Annotated[Settings,Depends(get_settings)],x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token);actor_user_id=user.id
    initial=await db.get(Feed,feed_id)
    if initial is None: raise HTTPException(404,"feed not found")
    values=body.model_dump(exclude={"version"},exclude_unset=True)
    if not values: raise HTTPException(422,"at least one feed field is required")
    initial_normalized,initial_enabled=initial.normalized_url,initial.enabled
    candidate=normalized_feed_url(values["url"],settings) if "url" in values else initial_normalized
    requires_validation=("url" in values and candidate!=initial_normalized) or (values.get("enabled") is True and not initial_enabled)
    if requires_validation:
        await db.rollback()  # release the implicit read transaction before network I/O
        try: await validate_feed_source(candidate,settings)
        except Exception as exc:
            from .infrastructure.feeds.rss import FeedError
            if not isinstance(exc,FeedError): raise
            raise HTTPException(422,f"feed validation failed: {exc.code}") from exc
    feed=(await db.scalars(select(Feed).where(Feed.id==feed_id).with_for_update())).one_or_none()
    if feed is None: raise HTTPException(404,"feed not found")
    if feed.version!=body.version: raise HTTPException(409,"feed version conflict")
    if "url" in values:
        normalized=normalized_feed_url(values["url"],settings)
        duplicate=await db.scalar(select(func.count()).select_from(Feed).where(Feed.id!=feed.id,((Feed.url==values["url"])|(Feed.normalized_url==normalized))))
        if duplicate: raise HTTPException(409,"feed URL already exists")
        values["url"]=normalized;values["normalized_url"]=normalized;feed.etag=None;feed.last_modified=None
    before={name:getattr(feed,name) for name in values}
    for name,value in values.items(): setattr(feed,name,value)
    if values.get("enabled") is True: feed.next_fetch_at=datetime.now(UTC)
    feed.version+=1
    db.add(AuditLog(actor_user_id=actor_user_id,action="feed.updated",entity_type="feed",entity_id=str(feed.id),request_id=request_id(request),before_values=before,after_values=values,details={}))
    try: await db.commit()
    except IntegrityError as exc:
        await db.rollback();raise HTTPException(409,"feed URL already exists") from exc
    await db.refresh(feed)
    return feed_output(feed)

@app.post("/api/v1/feeds/{feed_id}/test")
async def test_feed(feed_id:uuid.UUID,request:Request,db:DB,admin:Admin,settings:Annotated[Settings,Depends(get_settings)],x_csrf_token:Annotated[str|None,Header()]=None):
    from .infrastructure.feeds.rss import FeedError
    session,user=admin; require_csrf(session,x_csrf_token);actor_user_id=user.id;feed=await db.get(Feed,feed_id)
    if feed is None: raise HTTPException(404,"feed not found")
    url,entity_id=feed.url,str(feed.id);await db.rollback()
    try:
        accepted=await validate_feed_source(url,settings)
    except FeedError as exc:
        failure={"error_code":exc.code,"http_status":exc.http_status};db.add(AuditLog(actor_user_id=actor_user_id,action="feed.test_failed",entity_type="feed",entity_id=entity_id,request_id=request_id(request),before_values=None,after_values=failure,details={})); await db.commit(); raise HTTPException(422 if exc.code=="invalid_feed" else 503,f"feed test failed: {exc.code}") from exc
    result={"reachable":True,"http_status":200,"accepted_entries":accepted}
    db.add(AuditLog(actor_user_id=actor_user_id,action="feed.test",entity_type="feed",entity_id=entity_id,request_id=request_id(request),before_values=None,after_values=result,details={})); await db.commit(); return result

def require_csrf(session:Session,value:str|None)->None:
    if not value or not hmac.compare_digest(token_hash(value),session.csrf_token_hash): raise HTTPException(403,"invalid CSRF token")

async def enqueue_or_active(db:AsyncSession,job_type:JobType,payload:dict[str,Any],dedupe_key:str,*,max_attempts:int=3)->tuple[uuid.UUID,bool]:
    job_id=await enqueue(db,job_type,payload,dedupe_key=dedupe_key,max_attempts=max_attempts)
    if job_id is not None: return job_id,True
    query=select(Job.id).where(Job.job_type==job_type,Job.dedupe_key==dedupe_key,Job.status.in_([JobStatus.queued,JobStatus.running,JobStatus.retry_wait]))
    job_id=(await db.scalars(query)).one_or_none()
    if job_id is None:
        job_id=await enqueue(db,job_type,payload,dedupe_key=dedupe_key,max_attempts=max_attempts)
        if job_id is not None: return job_id,True
        job_id=(await db.scalars(query)).one_or_none()
    if job_id is None: raise HTTPException(409,"job state changed; retry request")
    return job_id,False

@app.post("/api/v1/feeds/{feed_id}/fetch",response_model=JobAccepted,status_code=202)
async def fetch_feed(feed_id:uuid.UUID,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token)
    feed=await db.get(Feed,feed_id)
    if feed is None: raise HTTPException(404,"feed not found")
    job_id=await enqueue(db,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key=f"feed:{feed_id}")
    created=job_id is not None
    if job_id is None:
        active=select(Job.id).where(Job.job_type==JobType.fetch_feed,Job.dedupe_key==f"feed:{feed_id}",Job.status.in_([JobStatus.queued,JobStatus.running,JobStatus.retry_wait]))
        job_id=(await db.scalars(active)).one_or_none()
        if job_id is None:
            # The conflicting active row may have completed between INSERT and SELECT.
            job_id=await enqueue(db,JobType.fetch_feed,{"feed_id":str(feed_id)},dedupe_key=f"feed:{feed_id}")
            created=job_id is not None
        if job_id is None:
            job_id=(await db.scalars(active)).one_or_none()
        if job_id is None: raise HTTPException(409,"feed job state changed; retry request")
    db.add(AuditLog(actor_user_id=user.id,action="feed.fetch_requested",entity_type="feed",entity_id=str(feed_id),request_id=request_id(request),before_values=None,after_values={"job_id":str(job_id),"created":created},details={}))
    await db.commit(); return JobAccepted(job_id=str(job_id),created=created)

@app.get("/api/v1/jobs")
async def list_jobs(db:DB,admin:Admin,limit:int=50):
    limit=max(1,min(limit,200)); rows=(await db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))).all()
    return [{"id":str(j.id),"job_type":j.job_type.value,"status":j.status.value,"attempts":j.attempts,"max_attempts":j.max_attempts,"run_after":j.run_after,"last_error_code":j.last_error_code,"created_at":j.created_at} for j in rows]

@app.post("/api/v1/jobs/{job_id}/retry",response_model=JobAccepted,status_code=202)
async def retry_job(job_id:uuid.UUID,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token)
    job=(await db.scalars(select(Job).where(Job.id==job_id).with_for_update())).one_or_none()
    if job is None: raise HTTPException(404,"job not found")
    if job.status!=JobStatus.dead: raise HTTPException(409,"only dead jobs can be retried")
    if job.dedupe_key:
        active=await db.scalar(select(func.count()).select_from(Job).where(Job.id!=job.id,Job.job_type==job.job_type,Job.dedupe_key==job.dedupe_key,Job.status.in_([JobStatus.queued,JobStatus.running,JobStatus.retry_wait])))
        if active: raise HTTPException(409,"an active replacement job already exists")
    if job.job_type==JobType.fetch_feed and job.payload.get("feed_id"):
        try: retry_feed=await db.get(Feed,uuid.UUID(str(job.payload["feed_id"])))
        except ValueError: retry_feed=None
        if retry_feed is None: raise HTTPException(409,"source feed no longer exists")
        if not retry_feed.enabled: raise HTTPException(409,"enable the feed before retrying its job")
    before={"status":job.status.value,"attempts":job.attempts,"last_error_code":job.last_error_code}
    job.status=JobStatus.queued; job.attempts=0; job.run_after=datetime.now(UTC); job.finished_at=None; job.started_at=None; job.last_error_code=None; job.last_error_message=None
    db.add(AuditLog(actor_user_id=user.id,action="job.retried",entity_type="job",entity_id=str(job.id),request_id=request_id(request),before_values=before,after_values={"status":"queued","attempts":0},details={})); await db.commit()
    return JobAccepted(job_id=str(job.id),created=False)

@app.get("/api/v1/fetch-runs")
async def list_fetch_runs(db:DB,admin:Admin,limit:int=50):
    limit=max(1,min(limit,200)); rows=(await db.scalars(select(FetchRun).order_by(FetchRun.started_at.desc()).limit(limit))).all()
    return [{"id":str(r.id),"feed_id":str(r.feed_id),"job_id":str(r.job_id) if r.job_id else None,"status":r.status.value,"http_status":r.http_status,"fetched_count":r.fetched_count,"inserted_count":r.inserted_count,"updated_count":r.updated_count,"rejected_count":r.rejected_count,"error_code":r.error_code,"started_at":r.started_at,"finished_at":r.finished_at} for r in rows]

@app.get("/api/v1/ai/providers")
async def ai_providers(admin:Admin,settings:Annotated[Settings,Depends(get_settings)]):
    provider=build_provider(settings)
    try: health=await provider.health()
    finally: await provider.client.aclose()
    return {"selected":settings.ai_provider,"fallback":settings.ai_fallback_provider,"auto_fallback":settings.ai_auto_fallback,"providers":[health.model_dump()]}

@app.post("/api/v1/ai/providers/{name}/test")
async def test_ai_provider(name:str,request:Request,db:DB,admin:Admin,settings:Annotated[Settings,Depends(get_settings)],x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token)
    if name!=settings.ai_provider: raise HTTPException(409,"provider is not selected")
    provider=build_provider(settings)
    fixed=EventAnalysisRequest(articles=[ArticleInput(article_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),title="Vietnam central bank formally changed the policy rate on July 17, 2026",summary="The central bank confirmed and enacted the formal policy-rate decision on July 17, 2026.",publisher="Capability fixture",published_at=datetime(2026,7,17,0,0,tzinfo=UTC))])
    try:
        result=await provider.analyze_event(fixed)
    except AIProviderError as exc:
        db.add(AuditLog(actor_user_id=user.id,action="ai.provider_test_failed",entity_type="ai_provider",entity_id=name,request_id=request_id(request),before_values=None,after_values={"schema_valid":False,"error_code":exc.code},details={})); await db.commit()
        raise HTTPException(503,f"provider capability test failed: {exc.code}") from exc
    finally: await provider.client.aclose()
    db.add(AuditLog(actor_user_id=user.id,action="ai.provider_test",entity_type="ai_provider",entity_id=name,request_id=request_id(request),before_values=None,after_values={"schema_valid":True},details={}))
    await db.commit(); return {"provider":name,"schema_valid":True,"relevance":result.relevance.value}

@app.get("/api/v1/evals/importance")
async def importance_eval_summary(db:DB,admin:Admin):
    from pathlib import Path
    latest=(await db.scalars(select(AIRun).where(AIRun.provider=="rule",AIRun.schema_version=="importance-eval-report-v1",AIRun.status==AIRunStatus.succeeded).order_by(AIRun.finished_at.desc()).limit(1))).one_or_none()
    if latest and latest.parsed_output: return latest.parsed_output
    root=Path(__file__).resolve().parents[3]
    path=(root/"evals"/"importance-v1.jsonl") if (root/"evals").exists() else Path("/app/evals/importance-v1.jsonl")
    return evaluate_rules(path)

@app.post("/api/v1/evals/importance/run")
async def run_importance_eval(request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    from pathlib import Path
    session,user=admin; require_csrf(session,x_csrf_token)
    root=Path(__file__).resolve().parents[3]
    path=(root/"evals"/"importance-v1.jsonl") if (root/"evals").exists() else Path("/app/evals/importance-v1.jsonl")
    job_id,created=await enqueue_or_active(db,JobType.retention,{"kind":"importance_eval","path":str(path)},"eval:importance-rubric-v1",max_attempts=2)
    db.add(AuditLog(actor_user_id=user.id,action="eval.importance_run",entity_type="eval",entity_id=str(job_id),request_id=request_id(request),before_values=None,after_values={"job_id":str(job_id)},details={}))
    await db.commit(); return JSONResponse(status_code=202,content={"job_id":str(job_id),"created":created})

@app.post("/api/v1/events/{event_id}/reanalyze",response_model=JobAccepted,status_code=202)
async def reanalyze_event(event_id:uuid.UUID,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    from .models import Event
    session,user=admin; require_csrf(session,x_csrf_token)
    if await db.get(Event,event_id) is None: raise HTTPException(404,"event not found")
    job_id,created=await enqueue_or_active(db,JobType.reanalyze_event,{"event_id":str(event_id)},f"event:{event_id}:reanalyze",max_attempts=2)
    db.add(AuditLog(actor_user_id=user.id,action="event.reanalyze_requested",entity_type="event",entity_id=str(event_id),request_id=request_id(request),before_values=None,after_values={"job_id":str(job_id)},details={})); await db.commit()
    return JobAccepted(job_id=str(job_id),created=created)

def event_output(event:Event)->dict[str,Any]:
    return {"id":str(event.id),**snapshot(event),"current_revision_id":str(event.current_revision_id) if event.current_revision_id else None,"rule_version":event.rule_version,"prompt_version":event.prompt_version,"created_at":event.created_at,"updated_at":event.updated_at}

@app.get("/api/v1/calendar")
async def calendar_month(month:str,db:DB,admin:Admin,include_drafts:bool=False):
    """Return a compact month projection; event bodies remain in /events."""
    try:
        if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",month) is None: raise ValueError
        start=datetime.strptime(month,"%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise HTTPException(422,"month must be YYYY-MM") from exc
    end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
    statuses=[PublicationStatus.approved]
    if include_drafts: statuses.extend([PublicationStatus.draft,PublicationStatus.needs_review])
    importance_rank=case(
        (Event.importance_level==Importance.high,4),(Event.importance_level==Importance.middle_high,3),
        (Event.importance_level==Importance.middle,2),(Event.importance_level==Importance.low,1),else_=0,
    )
    rows=(await db.execute(
        select(Event.event_date,func.count(Event.id),func.max(importance_rank),func.bool_or(Event.must_include),func.array_agg(Event.category.distinct()))
        .where(Event.event_date>=start,Event.event_date<end,Event.publication_status.in_(statuses),Event.merged_into_event_id.is_(None))
        .group_by(Event.event_date).order_by(Event.event_date)
    )).all()
    by_rank={4:"high",3:"middle_high",2:"middle",1:"low",0:None}
    return {"month":month,"days":[{"date":day.isoformat(),"count":count,"highest_importance":by_rank[rank],"has_must_include":bool(must),"categories":sorted(categories or [])[:5]} for day,count,rank,must,categories in rows]}

@app.get("/api/v1/events",response_model=EventPage)
async def list_events(db:DB,admin:Admin,status:PublicationStatus|None=None,event_date:date|None=None,date_from:date|None=None,date_to:date|None=None,category:str|None=None,importance:Importance|None=None,publisher:str|None=None,source_feed_id:uuid.UUID|None=None,q:str|None=None,limit:int=50,offset:int=0):
    limit=max(1,min(limit,100)); offset=max(0,min(offset,100_000))
    importance_rank=case(
        (Event.importance_level==Importance.high,4),(Event.importance_level==Importance.middle_high,3),
        (Event.importance_level==Importance.middle,2),(Event.importance_level==Importance.low,1),else_=0,
    )
    filters=[Event.merged_into_event_id.is_(None)]
    if status is not None: filters.append(Event.publication_status==status)
    if event_date is not None: filters.append(Event.event_date==event_date)
    if date_from is not None: filters.append(Event.event_date>=date_from)
    if date_to is not None: filters.append(Event.event_date<=date_to)
    if date_from and date_to and date_from>date_to: raise HTTPException(422,"date_from must not be after date_to")
    if category: filters.append(Event.category==category)
    if importance: filters.append(Event.importance_level==importance)
    source_filters=[]
    if source_feed_id: source_filters.append(Feed.id==source_feed_id)
    if publisher and publisher.strip():
        literal_publisher=publisher.strip()[:100].replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
        source_filters.append(Feed.publisher.ilike(f"%{literal_publisher}%",escape="\\"))
    if source_filters:
        filters.append(Event.id.in_(select(EventArticle.event_id).join(Article,Article.id==EventArticle.article_id).join(Feed,Feed.id==Article.feed_id).where(*source_filters)))
    if q and q.strip():
        literal=q.strip()[:200].replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
        pattern=f"%{literal}%"
        filters.append(or_(Event.title_ja.ilike(pattern,escape="\\"),Event.summary_ja.ilike(pattern,escape="\\"),Event.category.ilike(pattern,escape="\\")))
    query=select(Event).where(*filters).order_by(Event.event_date.desc(),importance_rank.desc(),Event.updated_at.desc(),Event.id).offset(offset).limit(limit)
    total=int((await db.scalar(select(func.count()).select_from(Event).where(*filters))) or 0)
    items=[event_output(e) for e in (await db.scalars(query)).all()]
    return EventPage(items=items,total=total,offset=offset,limit=limit,has_more=offset+len(items)<total)

@app.get("/api/v1/events/{event_id}")
async def get_event(event_id:uuid.UUID,db:DB,admin:Admin):
    event=await db.get(Event,event_id)
    if event is None: raise HTTPException(404,"event not found")
    links=(await db.execute(select(EventArticle,Article,Feed).join(Article,Article.id==EventArticle.article_id).join(Feed,Feed.id==Article.feed_id).where(EventArticle.event_id==event_id))).all()
    revisions=(await db.scalars(select(EventRevision).where(EventRevision.event_id==event_id).order_by(EventRevision.version.desc()).limit(50))).all()
    source_ids=select(EventArticle.article_id).where(EventArticle.event_id==event.id)
    ai_run=(await db.scalars(select(AIRun).where((AIRun.event_id==event.id)|(AIRun.article_id.in_(source_ids)),AIRun.status==AIRunStatus.succeeded).order_by(AIRun.finished_at.desc()).limit(1))).one_or_none()
    result=event_output(event); result["articles"]=[{"id":str(a.id),"title":a.title_raw,"url":a.source_url,"published_at":a.published_at,"publisher":feed.publisher,"feed_id":str(feed.id),"is_primary_source":link.is_primary_source,"link_reason":link.link_reason} for link,a,feed in links]; result["revisions"]=[{"id":str(r.id),"version":r.version,"reason":r.reason,"created_at":r.created_at} for r in revisions]
    result["ai_proposal"]={"values":ai_run.parsed_output,"provider":ai_run.provider,"model":ai_run.model,"prompt_version":ai_run.prompt_version,"rule_version":ai_run.rule_version,"finished_at":ai_run.finished_at} if ai_run else None
    return result

@app.get("/api/v1/events/{event_id}/similar-examples")
async def get_similar_examples(event_id:uuid.UUID,db:DB,admin:Admin,limit:int=5):
    from pathlib import Path
    from .application.evals import similar_cases
    event=await db.get(Event,event_id)
    if event is None: raise HTTPException(404,"event not found")
    root=Path(__file__).resolve().parents[3];path=(root/"evals"/"importance-v1.jsonl") if (root/"evals").exists() else Path("/app/evals/importance-v1.jsonl")
    return similar_cases(path,f"{event.title_ja} {event.summary_ja} {event.importance_reason or ''}",category=event.category,limit=limit)

@app.patch("/api/v1/events/{event_id}")
async def patch_event(event_id:uuid.UUID,body:EventPatch,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token); event=(await db.scalars(select(Event).where(Event.id==event_id).with_for_update())).one_or_none()
    if event is None: raise HTTPException(404,"event not found")
    values=body.model_dump(exclude={"version","reason"},exclude_unset=True)
    if not values: raise HTTPException(422,"at least one event field is required")
    await revise_event(db,event,user,values,body.version,body.reason,request_id(request)); await db.commit(); return event_output(event)

@app.post("/api/v1/events/{event_id}/review")
async def post_event_review(event_id:uuid.UUID,body:ReviewInput,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token); event=(await db.scalars(select(Event).where(Event.id==event_id).with_for_update())).one_or_none()
    if event is None: raise HTTPException(404,"event not found")
    review=await review_event(db,event,user,body.decision,body.version,body.reason,body.uncertainty_note,request_id(request)); await db.commit()
    return {"review_id":str(review.id),"event":event_output(event)}

@app.post("/api/v1/events/{event_id}/merge")
async def post_event_merge(event_id:uuid.UUID,body:MergeInput,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token)
    ids=sorted((event_id,body.source_event_id),key=str); locked=(await db.scalars(select(Event).where(Event.id.in_(ids)).order_by(Event.id).with_for_update())).all(); by_id={e.id:e for e in locked}
    if event_id not in by_id or body.source_event_id not in by_id: raise HTTPException(404,"event not found")
    target=await merge_events(db,by_id[event_id],by_id[body.source_event_id],user,body.target_version,body.source_version,body.reason,request_id(request)); await db.commit(); return event_output(target)

@app.post("/api/v1/events/{event_id}/split",status_code=201)
async def post_event_split(event_id:uuid.UUID,body:SplitInput,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token); source=(await db.scalars(select(Event).where(Event.id==event_id).with_for_update())).one_or_none()
    if source is None: raise HTTPException(404,"event not found")
    new_event=await split_event(db,source,user,body.article_ids,body.version,body.event.model_dump(),body.reason,request_id(request)); await db.commit(); return event_output(new_event)

@app.get("/api/v1/events/{event_id}/cluster-candidates")
async def cluster_candidates(event_id:uuid.UUID,db:DB,admin:Admin):
    if await db.get(Event,event_id) is None: raise HTTPException(404,"event not found")
    rows=(await db.scalars(select(EventClusterCandidate).where((EventClusterCandidate.event_id==event_id)|(EventClusterCandidate.candidate_event_id==event_id)).order_by(EventClusterCandidate.similarity_score.desc()))).all()
    return [{"id":str(c.id),"event_id":str(c.event_id),"candidate_event_id":str(c.candidate_event_id),"similarity_score":float(c.similarity_score),"reasons":c.reasons,"status":c.status.value} for c in rows]

@app.patch("/api/v1/events/{event_id}/cluster-candidates/{candidate_id}")
async def review_cluster_candidate(event_id:uuid.UUID,candidate_id:uuid.UUID,body:CandidateReviewInput,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token)
    candidate,invalid=await decide_cluster_candidate(db,candidate_id,event_id,user,body.status,body.reason,request_id(request)); await db.commit()
    return {"id":str(candidate.id),"status":candidate.status.value,"invalidated":invalid}

@app.post("/api/v1/events/{event_id}/cluster",response_model=JobAccepted,status_code=202)
async def request_cluster(event_id:uuid.UUID,request:Request,db:DB,admin:Admin,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=admin; require_csrf(session,x_csrf_token)
    event=(await db.scalars(select(Event).where(Event.id==event_id).with_for_update())).one_or_none()
    if event is None: raise HTTPException(404,"event not found")
    if event.publication_status==PublicationStatus.hidden or event.merged_into_event_id is not None: raise HTTPException(409,"hidden or merged event cannot be clustered")
    job_id,created=await enqueue_or_active(db,JobType.cluster_event,{"event_id":str(event_id)},f"event:{event_id}:cluster",max_attempts=2)
    db.add(AuditLog(actor_user_id=user.id,action="event.cluster_requested",entity_type="event",entity_id=str(event_id),request_id=request_id(request),before_values=None,after_values={"job_id":str(job_id)},details={})); await db.commit(); return JobAccepted(job_id=str(job_id),created=created)
