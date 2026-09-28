"""Persistence only. No provider, analysis or scoring logic."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SearchJob(Base):
    __tablename__ = "search_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','paused','completed','failed','cancelled')"),
        CheckConstraint("min_followers >= 0 AND max_followers >= min_followers"),
        CheckConstraint("minimum_score BETWEEN 0 AND 100"),
        CheckConstraint("target_leads > 0"),
        Index("ix_jobs_status_created", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    seed_accounts: Mapped[list[str]] = mapped_column(JSON, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    min_followers: Mapped[int] = mapped_column(default=1000)
    max_followers: Mapped[int] = mapped_column(default=50000)
    activity_days: Mapped[int] = mapped_column(default=30)
    minimum_score: Mapped[int] = mapped_column(default=70)
    target_leads: Mapped[int] = mapped_column(default=500)
    candidates_found: Mapped[int] = mapped_column(default=0)
    profiles_analyzed: Mapped[int] = mapped_column(default=0)
    artists_detected: Mapped[int] = mapped_column(default=0)
    qualified_leads: Mapped[int] = mapped_column(default=0)
    stage: Mapped[str] = mapped_column(String(80), default="queued")
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_lead_platform_id"),
        UniqueConstraint("platform", "username", name="uq_lead_platform_username"),
        CheckConstraint("followers >= 0 AND following >= 0"),
        CheckConstraint("lead_score BETWEEN 0 AND 100"),
        CheckConstraint("artist_probability BETWEEN 0 AND 1"),
        CheckConstraint("status IN ('new','reviewed','qualified','rejected','contacted')"),
        Index("ix_leads_score", "lead_score", "id"),
        Index("ix_leads_followers", "followers", "id"),
        Index("ix_leads_activity", "last_activity_at", "id"),
        Index("ix_leads_created", "created_at", "id"),
        Index("ix_leads_genre_status", "primary_genre", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(40))
    platform_user_id: Mapped[str | None] = mapped_column(String(160))
    username: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str] = mapped_column(String(240), default="")
    profile_url: Mapped[str] = mapped_column(String(2048), default="")
    avatar_url: Mapped[str] = mapped_column(String(2048), default="")
    bio: Mapped[str] = mapped_column(String(10000), default="")
    followers: Mapped[int] = mapped_column(default=0)
    following: Mapped[int] = mapped_column(default=0)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    external_url: Mapped[str] = mapped_column(String(2048), default="")
    artist_probability: Mapped[float] = mapped_column(default=0)
    primary_genre: Mapped[str | None] = mapped_column(String(80))
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_activity_at: Mapped[datetime | None]
    lead_score: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class LeadSource(Base):
    __tablename__ = "lead_sources"
    __table_args__ = (
        UniqueConstraint(
            "lead_id",
            "search_job_id",
            "source_provider",
            "source_type",
            "source_value",
            name="uq_source_provenance",
        ),
        Index("ix_sources_job_lead", "search_job_id", "lead_id"),
        Index("ix_sources_lead", "lead_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    search_job_id: Mapped[int] = mapped_column(ForeignKey("search_jobs.id", ondelete="RESTRICT"))
    source_provider: Mapped[str] = mapped_column(String(80))
    source_type: Mapped[str] = mapped_column(String(30))
    source_value: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LeadAnalysis(Base):
    __tablename__ = "lead_analysis"
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(default=1)
    classifier: Mapped[str] = mapped_column(String(80), default="rule_based")
    is_artist: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[float]
    primary_genre: Mapped[str | None] = mapped_column(String(80))
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    extracted_signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analyzed_at: Mapped[datetime] = mapped_column(default=utcnow)


class LeadScoreBreakdown(Base):
    __tablename__ = "lead_score_breakdowns"
    __table_args__ = (UniqueConstraint("lead_id", "rule"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    rule: Mapped[str] = mapped_column(String(80))
    points: Mapped[int]
    reason: Mapped[str] = mapped_column(String(240))
    weights_version: Mapped[int] = mapped_column(default=1)
    scored_at: Mapped[datetime] = mapped_column(default=utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class BrowserQueue(Base):
    __tablename__ = "browser_queues"
    job_id: Mapped[int] = mapped_column(ForeignKey("search_jobs.id"), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(32))
    urls: Mapped[list[str]] = mapped_column(JSON)
    cursor: Mapped[int] = mapped_column(default=0)
    weights: Mapped[dict[str, Any]] = mapped_column(JSON)
    last_error: Mapped[str | None]


class ProviderHealth(Base):
    __tablename__ = "provider_health"
    provider: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), default="Unavailable")
    last_request_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(1000))
    official_rate_limit: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[int] = mapped_column(primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutSource(Base):
    __tablename__ = "scout_sources"
    url: Mapped[str] = mapped_column(String(240), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Schema 5: scan bookkeeping shown in the Lead Scout source table.
    last_scanned_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(40), default="new")
    leads_found: Mapped[int] = mapped_column(default=0)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutRun(Base):
    __tablename__ = "scout_runs"
    job_id: Mapped[int] = mapped_column(ForeignKey("search_jobs.id"), primary_key=True)
    tasks: Mapped[list[dict]] = mapped_column(JSON)
    observations: Mapped[dict] = mapped_column(JSON, default=dict)
    notices: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Schema 4: publications read from the source grid but not queued yet.
    backlog: Mapped[list[dict]] = mapped_column(JSON, default=list)
    found: Mapped[int] = mapped_column(default=0)
    # Schema 5: progress counters, current source/profile and finished sources.
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class ScoutProcessedProfile(Base):
    """Every candidate the scout has decided on, so later runs do not repeat it."""

    __tablename__ = "scout_processed_profiles"
    username: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_username: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(40))
    processed_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutProcessedPost(Base):
    """Publication memory. Keys: shortcode (source posts), tagged:<source>:<shortcode>."""

    __tablename__ = "scout_processed_posts"
    post_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_username: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    processed_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Schema 6: processed | unavailable | failed, with retry bookkeeping.
    status: Mapped[str] = mapped_column(String(20), default="processed")
    attempts: Mapped[int] = mapped_column(default=1)
    last_error: Mapped[str | None] = mapped_column(String(200))


class ScoutProcessedStory(Base):
    """Story memory keyed story:<source>:<media id>; reused only within the TTL."""

    __tablename__ = "scout_processed_stories"
    story_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_username: Mapped[str] = mapped_column(String(40))
    processed_at: Mapped[datetime] = mapped_column(default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="processed")
    attempts: Mapped[int] = mapped_column(default=1)
    last_error: Mapped[str | None] = mapped_column(String(200))


class ScoutAICache(Base):
    __tablename__ = "scout_ai_cache"
    username: Mapped[str] = mapped_column(String(40), primary_key=True)
    category: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[int]
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutState(Base):
    """Small scout bookkeeping values, such as the source rotation cursor."""

    __tablename__ = "scout_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class ScoutEvent(Base):
    __tablename__ = "scout_events"
    __table_args__ = (Index("ix_scout_events_job", "job_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("search_jobs.id"))
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LeadScoutProfile(Base):
    """Lead Scout details kept next to the CRM lead."""

    __tablename__ = "lead_scout_profiles"
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    instagram_id: Mapped[str | None] = mapped_column(String(40))
    posts_count: Mapped[int | None]
    emails: Mapped[list[str]] = mapped_column(JSON, default=list)
    phones: Mapped[list[str]] = mapped_column(JSON, default=list)
    profile_type: Mapped[str] = mapped_column(String(20))
    profile_score: Mapped[int] = mapped_column(default=0)
    profile_confidence: Mapped[int] = mapped_column(default=0)
    profile_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_username: Mapped[str] = mapped_column(String(40))
    discovery_method: Mapped[str] = mapped_column(String(20))
    origin_url: Mapped[str | None] = mapped_column(String(2048))
    ai_model: Mapped[str | None] = mapped_column(String(120))
    ai_confidence: Mapped[int | None]
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutAccount(Base):
    """Per browser profile goal: how many suitable leads this account should find."""

    __tablename__ = "scout_accounts"
    profile_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    target: Mapped[int] = mapped_column(default=100)
    found: Mapped[int] = mapped_column(default=0)


class ScoutPost(Base):
    __tablename__ = "scout_posts"
    url: Mapped[str] = mapped_column(String(240), primary_key=True)
    source: Mapped[str] = mapped_column(String(240))
    caption: Mapped[str] = mapped_column(String(12000))
    published_at: Mapped[str | None] = mapped_column(String(80))
    mentions: Mapped[list[str]] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutAssessment(Base):
    __tablename__ = "scout_assessments"
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), primary_key=True)
    eligible: Mapped[bool] = mapped_column(default=False)
    priority: Mapped[int] = mapped_column(default=0)
    details: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
