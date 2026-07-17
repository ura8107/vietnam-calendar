import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, ForeignKeyConstraint, Index, Integer, LargeBinary, Numeric, SmallInteger, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase): pass

class Importance(str, enum.Enum): low="low"; middle="middle"; middle_high="middle_high"; high="high"
class ProcessingStatus(str, enum.Enum): fetched="fetched"; normalized="normalized"; relevance_ruled="relevance_ruled"; ai_analyzed="ai_analyzed"; ai_skipped="ai_skipped"; ai_failed="ai_failed"; event_linked="event_linked"; needs_review="needs_review"; approved="approved"; hidden="hidden"; rejected="rejected"
class DateSource(str, enum.Enum): published="published"; updated="updated"; fetched="fetched"
class Relevance(str, enum.Enum): target="target"; out_of_scope="out_of_scope"; uncertain="uncertain"
class DateCertainty(str, enum.Enum): confirmed="confirmed"; estimated="estimated"; published_fallback="published_fallback"
class Certainty(str, enum.Enum): confirmed="confirmed"; partially_confirmed="partially_confirmed"; planned="planned"; speculative="speculative"
class PublicationStatus(str, enum.Enum): draft="draft"; needs_review="needs_review"; approved="approved"; hidden="hidden"
class ReviewDecision(str, enum.Enum): approve="approve"; reject="reject"; needs_changes="needs_changes"
class JobType(str, enum.Enum): fetch_feed="fetch_feed"; normalize_article="normalize_article"; analyze_article="analyze_article"; cluster_event="cluster_event"; reanalyze_event="reanalyze_event"; retention="retention"
class JobStatus(str, enum.Enum): queued="queued"; running="running"; retry_wait="retry_wait"; succeeded="succeeded"; dead="dead"
class FetchStatus(str, enum.Enum): started="started"; succeeded="succeeded"; not_modified="not_modified"; failed="failed"
class AIRunStatus(str, enum.Enum): started="started"; succeeded="succeeded"; failed="failed"; invalid="invalid"
class ClusterCandidateStatus(str, enum.Enum): pending="pending"; accepted="accepted"; dismissed="dismissed"

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Feed(Base, TimestampMixin):
    __tablename__="feeds"; __table_args__=(CheckConstraint("fetch_interval_minutes BETWEEN 5 AND 1440", name="ck_feeds_interval"), CheckConstraint("trust_score IS NULL OR trust_score BETWEEN 0 AND 100", name="ck_feeds_trust"), CheckConstraint("consecutive_failures >= 0", name="ck_feeds_failures"))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str]=mapped_column(Text); url: Mapped[str]=mapped_column(Text, unique=True); normalized_url: Mapped[str]=mapped_column(Text, unique=True)
    publisher: Mapped[str]=mapped_column(Text); declared_language: Mapped[str|None]=mapped_column(String(35)); default_category: Mapped[str|None]=mapped_column(String(60)); trust_score: Mapped[int|None]=mapped_column(SmallInteger)
    enabled: Mapped[bool]=mapped_column(Boolean, default=True); fetch_interval_minutes: Mapped[int]=mapped_column(Integer, default=30)
    etag: Mapped[str|None]=mapped_column(Text); last_modified: Mapped[str|None]=mapped_column(Text); next_fetch_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_success_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); last_failure_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); consecutive_failures: Mapped[int]=mapped_column(Integer, default=0)

class Job(Base):
    __tablename__="jobs"; __table_args__=(CheckConstraint("priority BETWEEN -1000 AND 1000", name="ck_jobs_priority"), CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 100 AND attempts <= max_attempts", name="ck_jobs_attempts"), Index("ix_jobs_claim", "status", text("priority DESC"), "run_after", "created_at"), Index("uq_jobs_active_dedupe", "job_type", "dedupe_key", unique=True, postgresql_where=text("dedupe_key IS NOT NULL AND status IN ('queued','running','retry_wait')")))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); job_type: Mapped[JobType]=mapped_column(Enum(JobType, name="job_type")); dedupe_key: Mapped[str|None]=mapped_column(String(255)); payload: Mapped[dict[str,Any]]=mapped_column(JSONB)
    status: Mapped[JobStatus]=mapped_column(Enum(JobStatus,name="job_status"), default=JobStatus.queued); priority: Mapped[int]=mapped_column(Integer, default=0); attempts: Mapped[int]=mapped_column(Integer, default=0); max_attempts: Mapped[int]=mapped_column(Integer, default=3)
    run_after: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now()); locked_by: Mapped[str|None]=mapped_column(String(100)); locked_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); lease_expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); last_error_code: Mapped[str|None]=mapped_column(String(80)); last_error_message: Mapped[str|None]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now()); started_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); finished_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now())

class FetchRun(Base):
    __tablename__="fetch_runs"; __table_args__=(CheckConstraint("http_status IS NULL OR http_status BETWEEN 100 AND 599",name="ck_fetch_http_status"), CheckConstraint("fetched_count>=0 AND inserted_count>=0 AND updated_count>=0 AND rejected_count>=0",name="ck_fetch_counts"))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); feed_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("feeds.id",ondelete="CASCADE"),index=True); job_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey("jobs.id",ondelete="SET NULL"),index=True)
    status: Mapped[FetchStatus]=mapped_column(Enum(FetchStatus,name="fetch_status")); started_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now()); finished_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); http_status: Mapped[int|None]=mapped_column(SmallInteger)
    request_etag: Mapped[str|None]=mapped_column(Text); request_last_modified: Mapped[str|None]=mapped_column(Text); response_etag: Mapped[str|None]=mapped_column(Text); response_last_modified: Mapped[str|None]=mapped_column(Text)
    response_body_hash: Mapped[str|None]=mapped_column(String(64)); response_body_gzip: Mapped[bytes|None]=mapped_column(LargeBinary); fetched_count: Mapped[int]=mapped_column(Integer,default=0); inserted_count: Mapped[int]=mapped_column(Integer,default=0); updated_count: Mapped[int]=mapped_column(Integer,default=0); rejected_count: Mapped[int]=mapped_column(Integer,default=0)
    error_class: Mapped[str|None]=mapped_column(String(120)); error_code: Mapped[str|None]=mapped_column(String(80)); safe_error_message: Mapped[str|None]=mapped_column(Text); retryable: Mapped[bool]=mapped_column(Boolean,default=False)

class Article(Base,TimestampMixin):
    __tablename__="articles"; __table_args__=(UniqueConstraint("feed_id","identity_key"),)
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); feed_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("feeds.id",ondelete="RESTRICT"),index=True); source_guid: Mapped[str|None]=mapped_column(Text); source_url: Mapped[str]=mapped_column(Text); normalized_url: Mapped[str]=mapped_column(Text,index=True); identity_key: Mapped[str]=mapped_column(String(80))
    title_raw: Mapped[str]=mapped_column(Text); summary_raw: Mapped[str|None]=mapped_column(Text); author_raw: Mapped[str|None]=mapped_column(Text); title_normalized: Mapped[str]=mapped_column(Text); summary_text: Mapped[str|None]=mapped_column(Text); image_url: Mapped[str|None]=mapped_column(Text)
    published_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),index=True); updated_at_source: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); fetched_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now()); date_source: Mapped[DateSource]=mapped_column(Enum(DateSource,name="date_source")); detected_language: Mapped[str|None]=mapped_column(String(35)); content_hash: Mapped[str]=mapped_column(String(64)); raw_entry: Mapped[dict[str,Any]]=mapped_column(JSONB); processing_status: Mapped[ProcessingStatus]=mapped_column(Enum(ProcessingStatus,name="processing_status"),default=ProcessingStatus.fetched,index=True)

class Event(Base,TimestampMixin):
    __tablename__="events"; __table_args__=(CheckConstraint("importance_score IS NULL OR importance_score BETWEEN 0 AND 100",name="ck_events_importance_score"),CheckConstraint("version >= 1",name="ck_events_version"),CheckConstraint("NOT must_include OR (must_include_reason IS NOT NULL AND length(trim(must_include_reason)) > 0)",name="ck_events_must_reason"),CheckConstraint("relevance_status <> 'out_of_scope' OR (importance_level IS NULL AND importance_score IS NULL)",name="ck_events_outscope_importance"),ForeignKeyConstraint(["current_revision_id","id"],["event_revisions.id","event_revisions.event_id"],name="fk_events_current_revision_same_event",use_alter=True,ondelete="RESTRICT"))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); title_ja: Mapped[str]=mapped_column(Text); summary_ja: Mapped[str]=mapped_column(Text); event_date: Mapped[date]=mapped_column(Date,index=True); date_certainty: Mapped[DateCertainty]=mapped_column(Enum(DateCertainty,name="date_certainty")); category: Mapped[str]=mapped_column(String(60),index=True)
    relevance_status: Mapped[Relevance]=mapped_column(Enum(Relevance,name="relevance_status")); relevance_reason: Mapped[str|None]=mapped_column(Text); importance_level: Mapped[Importance|None]=mapped_column(Enum(Importance,name="importance_level")); importance_score: Mapped[int|None]=mapped_column(SmallInteger); importance_reason: Mapped[str|None]=mapped_column(Text); must_include: Mapped[bool]=mapped_column(Boolean,default=False); must_include_reason: Mapped[str|None]=mapped_column(Text); certainty: Mapped[Certainty]=mapped_column(Enum(Certainty,name="event_certainty")); publication_status: Mapped[PublicationStatus]=mapped_column(Enum(PublicationStatus,name="publication_status"),default=PublicationStatus.draft,index=True); current_revision_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True)); merged_into_event_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey("events.id",ondelete="RESTRICT"),index=True); rule_version: Mapped[str]=mapped_column(String(80)); prompt_version: Mapped[str]=mapped_column(String(80)); version: Mapped[int]=mapped_column(Integer,default=1)

class EventArticle(Base):
    __tablename__="event_articles"; __table_args__=(CheckConstraint("similarity_score IS NULL OR similarity_score BETWEEN 0 AND 1",name="ck_event_article_similarity"),Index("uq_event_primary_source","event_id",unique=True,postgresql_where=text("is_primary_source")))
    event_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("events.id",ondelete="CASCADE"),primary_key=True); article_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("articles.id",ondelete="CASCADE"),primary_key=True); similarity_score: Mapped[float|None]=mapped_column(Numeric(5,4)); is_primary_source: Mapped[bool]=mapped_column(Boolean,default=False); link_reason: Mapped[str]=mapped_column(Text)

class EventClusterCandidate(Base, TimestampMixin):
    """A review suggestion only; candidates never change event membership themselves."""
    __tablename__="event_cluster_candidates"
    __table_args__=(UniqueConstraint("event_id","candidate_event_id",name="uq_event_cluster_pair"),CheckConstraint("event_id <> candidate_event_id",name="ck_event_cluster_distinct"),CheckConstraint("similarity_score BETWEEN 0 AND 1",name="ck_event_cluster_similarity"))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    event_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("events.id",ondelete="CASCADE"),index=True)
    candidate_event_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("events.id",ondelete="CASCADE"),index=True)
    similarity_score: Mapped[float]=mapped_column(Numeric(5,4))
    reasons: Mapped[list[str]]=mapped_column(JSONB,default=list)
    status: Mapped[ClusterCandidateStatus]=mapped_column(Enum(ClusterCandidateStatus,name="cluster_candidate_status"),default=ClusterCandidateStatus.pending,index=True)
    reviewed_by_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey("users.id",ondelete="SET NULL"))
    reviewed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class User(Base,TimestampMixin):
    __tablename__="users"; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); username: Mapped[str]=mapped_column(String(100),unique=True); password_hash: Mapped[str]=mapped_column(Text); is_active: Mapped[bool]=mapped_column(Boolean,default=True); is_admin: Mapped[bool]=mapped_column(Boolean,default=True)
class Session(Base):
    __tablename__="sessions"; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); user_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),index=True); token_hash: Mapped[str]=mapped_column(String(64),unique=True); csrf_token_hash: Mapped[str]=mapped_column(String(64)); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now()); expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True); last_seen_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now()); revoked_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class Review(Base):
    __tablename__="reviews"; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); event_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("events.id",ondelete="CASCADE"),index=True); reviewer_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("users.id",ondelete="RESTRICT")); reviewed_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now()); decision: Mapped[ReviewDecision]=mapped_column(Enum(ReviewDecision,name="review_decision")); reason: Mapped[str|None]=mapped_column(Text); uncertainty_note: Mapped[str|None]=mapped_column(Text); ai_proposal: Mapped[dict[str,Any]]=mapped_column(JSONB); human_values: Mapped[dict[str,Any]]=mapped_column(JSONB); rule_version: Mapped[str]=mapped_column(String(80)); prompt_version: Mapped[str]=mapped_column(String(80)); provider: Mapped[str]=mapped_column(String(60)); model: Mapped[str]=mapped_column(String(150))
class EventRevision(Base):
    __tablename__="event_revisions"; __table_args__=(UniqueConstraint("event_id","version"),UniqueConstraint("id","event_id",name="uq_event_revision_identity"),CheckConstraint("version >= 1",name="ck_event_revisions_version")); id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); event_id: Mapped[uuid.UUID]=mapped_column(ForeignKey("events.id",ondelete="CASCADE"),index=True); version: Mapped[int]=mapped_column(Integer); changed_by_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey("users.id",ondelete="SET NULL")); before_values: Mapped[dict[str,Any]|None]=mapped_column(JSONB); after_values: Mapped[dict[str,Any]]=mapped_column(JSONB); reason: Mapped[str|None]=mapped_column(Text); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class AIRun(Base):
    __tablename__="ai_runs"; __table_args__=(CheckConstraint("retry_count >= 0",name="ck_ai_retry"),CheckConstraint("latency_ms IS NULL OR latency_ms >= 0",name="ck_ai_latency"),CheckConstraint("estimated_cost_usd IS NULL OR estimated_cost_usd >= 0",name="ck_ai_cost"),CheckConstraint("input_tokens IS NULL OR input_tokens >= 0",name="ck_ai_input_tokens"),CheckConstraint("output_tokens IS NULL OR output_tokens >= 0",name="ck_ai_output_tokens"))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); article_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey("articles.id",ondelete="SET NULL"),index=True); event_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey("events.id",ondelete="SET NULL"),index=True); job_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey("jobs.id",ondelete="SET NULL"),index=True); attempt_number: Mapped[int|None]=mapped_column(Integer); provider: Mapped[str]=mapped_column(String(60)); base_url_identifier: Mapped[str|None]=mapped_column(String(120)); model: Mapped[str]=mapped_column(String(150)); prompt_version: Mapped[str]=mapped_column(String(80)); schema_version: Mapped[str]=mapped_column(String(80)); rule_version: Mapped[str]=mapped_column(String(80)); input_hash: Mapped[str]=mapped_column(String(64)); source_article_ids: Mapped[list[str]]=mapped_column(JSONB); raw_response: Mapped[dict[str,Any]|None]=mapped_column(JSONB); parsed_output: Mapped[dict[str,Any]|None]=mapped_column(JSONB); validation_errors: Mapped[list[Any]|None]=mapped_column(JSONB); status: Mapped[AIRunStatus]=mapped_column(Enum(AIRunStatus,name="ai_run_status")); retry_count: Mapped[int]=mapped_column(Integer,default=0); latency_ms: Mapped[int|None]=mapped_column(Integer); input_tokens: Mapped[int|None]=mapped_column(Integer); output_tokens: Mapped[int|None]=mapped_column(Integer); estimated_cost_usd: Mapped[float|None]=mapped_column(Numeric(12,6)); external_request_id: Mapped[str|None]=mapped_column(String(200)); started_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now()); finished_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class AuditLog(Base):
    __tablename__="audit_logs"; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); actor_user_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey("users.id",ondelete="SET NULL"),index=True); action: Mapped[str]=mapped_column(String(100),index=True); entity_type: Mapped[str]=mapped_column(String(80)); entity_id: Mapped[str|None]=mapped_column(String(100)); request_id: Mapped[str|None]=mapped_column(String(100)); before_values: Mapped[dict[str,Any]|None]=mapped_column(JSONB); after_values: Mapped[dict[str,Any]|None]=mapped_column(JSONB); details: Mapped[dict[str,Any]]=mapped_column(JSONB,default=dict); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),index=True)
