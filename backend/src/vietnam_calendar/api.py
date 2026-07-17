import hmac
import ipaddress
import time
import uuid
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import engine, get_session
from .jobs import enqueue
from .models import AuditLog, Feed, FetchRun, Job, JobStatus, JobType, Session, User
from .security import random_token, token_hash, verify_password

DUMMY_ARGON2_HASH = "$argon2id$v=19$m=65536,t=3,p=4$7NPVOF0sfNLwDLGW8SvqzQ$9hxBQrk5NIizcQRb7GFouaNQSnzQNwWct4gDQT6izgM"

class ErrorBody(BaseModel): code: str; message: str; request_id: str; details: Any = None
class LoginInput(BaseModel): username: str=Field(min_length=1,max_length=100); password: str=Field(min_length=8,max_length=1024)
class LoginOutput(BaseModel): csrf_token: str
class MeOutput(BaseModel): id: str; username: str; is_admin: bool
class FeedOutput(BaseModel): id: str; name: str; url: str; publisher: str; enabled: bool; fetch_interval_minutes: int
class JobAccepted(BaseModel): job_id: str; created: bool

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
    token,csrf=random_token(),random_token(); db.add(Session(user_id=user.id,token_hash=token_hash(token),csrf_token_hash=token_hash(csrf),expires_at=now+timedelta(seconds=settings.session_ttl_seconds),last_seen_at=now)); db.add(AuditLog(actor_user_id=user.id,action="auth.login",entity_type="session",request_id=request_id(request),before_values=None,after_values={"active_sessions":1},details={}))
    await db.commit(); response.set_cookie(settings.session_cookie_name,token,httponly=True,secure=settings.cookie_secure,samesite="strict",max_age=settings.session_ttl_seconds,path="/"); return LoginOutput(csrf_token=csrf)
@app.post("/api/v1/auth/logout",status_code=204)
async def logout(response:Response,request:Request,db:DB,auth:Auth,x_csrf_token:Annotated[str|None,Header()]=None):
    session,user=auth
    if not x_csrf_token or not hmac.compare_digest(token_hash(x_csrf_token),session.csrf_token_hash): raise HTTPException(403,"invalid CSRF token")
    session.revoked_at=datetime.now(UTC); db.add(AuditLog(actor_user_id=user.id,action="auth.logout",entity_type="session",entity_id=str(session.id),request_id=request_id(request),before_values={"revoked":False},after_values={"revoked":True},details={})); await db.commit()
    settings=get_settings(); response.delete_cookie(settings.session_cookie_name,path="/",secure=settings.cookie_secure,httponly=True,samesite="strict")
@app.get("/api/v1/auth/me",response_model=MeOutput)
async def me(auth:Auth): _,u=auth; return MeOutput(id=str(u.id),username=u.username,is_admin=u.is_admin)
@app.get("/api/v1/feeds",response_model=list[FeedOutput])
async def list_feeds(db:DB,admin:Admin):
    rows=(await db.scalars(select(Feed).order_by(Feed.name))).all(); return [FeedOutput(id=str(f.id),name=f.name,url=f.url,publisher=f.publisher,enabled=f.enabled,fetch_interval_minutes=f.fetch_interval_minutes) for f in rows]

def require_csrf(session:Session,value:str|None)->None:
    if not value or not hmac.compare_digest(token_hash(value),session.csrf_token_hash): raise HTTPException(403,"invalid CSRF token")

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

@app.get("/api/v1/fetch-runs")
async def list_fetch_runs(db:DB,admin:Admin,limit:int=50):
    limit=max(1,min(limit,200)); rows=(await db.scalars(select(FetchRun).order_by(FetchRun.started_at.desc()).limit(limit))).all()
    return [{"id":str(r.id),"feed_id":str(r.feed_id),"job_id":str(r.job_id) if r.job_id else None,"status":r.status.value,"http_status":r.http_status,"fetched_count":r.fetched_count,"inserted_count":r.inserted_count,"updated_count":r.updated_count,"rejected_count":r.rejected_count,"error_code":r.error_code,"started_at":r.started_at,"finished_at":r.finished_at} for r in rows]
