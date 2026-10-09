"""Persistence only. No provider, analysis or scoring logic."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
)
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
    # «Очистить историю» hides finished searches; their leads stay in the database.
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)


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
    # Schema 10: never message this lead; outreach contact bookkeeping.
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    last_contacted_at: Mapped[datetime | None]


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
    # Schema 9: totals over all runs, shown in the source table.
    candidates_found: Mapped[int] = mapped_column(default=0)
    profiles_resolved: Mapped[int] = mapped_column(default=0)
    profiles_skipped: Mapped[int] = mapped_column(default=0)
    errors_count: Mapped[int] = mapped_column(default=0)


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
    # Schema 9: run history order; status and finish time live in search_jobs.
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class ScoutProcessedProfile(Base):
    """Every candidate the scout has decided on, so later runs do not repeat it."""

    __tablename__ = "scout_processed_profiles"
    username: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_username: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(20))
    # Skip reason; DUPLICATE_LEAD for a lead that was found again.
    reason: Mapped[str | None] = mapped_column(String(40))
    processed_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Schema 9: identity and classification at decision time.
    instagram_user_id: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[int | None]


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


class ScoutAIClassification(Base):
    """Schema 8: AI answers keyed by a hash of the classified profile fields; answers of
    another classifier version are ignored."""

    __tablename__ = "scout_ai_classifications"
    profile_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[int]
    model: Mapped[str] = mapped_column(String(120))
    classifier_version: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutPendingAIJob(Base):
    """Schema 8: AI classifications that failed transiently, retried with backoff."""

    __tablename__ = "scout_pending_ai_jobs"
    profile_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(40))
    # Classifier input (public profile fields) and the caller's context (scout task).
    profile: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(200))
    retry_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutProfileCache(Base):
    """Schema 7: resolved Instagram profiles, reused within the configured TTL."""

    __tablename__ = "scout_profile_cache"
    username: Mapped[str] = mapped_column(String(40), primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(10))
    resolved_at: Mapped[datetime] = mapped_column(default=utcnow)


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
    # Schema 8: "local", "ai" or "local+ai".
    profile_decided_by: Mapped[str] = mapped_column(String(10), default="local")
    # Schema 9: remaining NormalizedInstagramProfile fields and both classifier opinions.
    category_name: Mapped[str | None] = mapped_column(String(120))
    is_business: Mapped[bool | None]
    bio_links: Mapped[list[str]] = mapped_column(JSON, default=list)
    local_category: Mapped[str | None] = mapped_column(String(20))
    local_confidence: Mapped[int | None]
    ai_category: Mapped[str | None] = mapped_column(String(20))
    origin_id: Mapped[str | None] = mapped_column(String(120))
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScoutLeadSource(Base):
    """Schema 9: every SMM source and method a lead was found through."""

    __tablename__ = "scout_lead_sources"
    __table_args__ = (
        UniqueConstraint("lead_id", "source_username", "discovery_method", name="uq_scout_source"),
        Index("ix_scout_lead_sources_lead", "lead_id"),
        Index("ix_scout_lead_sources_source", "source_username"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    source_username: Mapped[str] = mapped_column(String(40))
    discovery_method: Mapped[str] = mapped_column(String(20))
    origin_id: Mapped[str | None] = mapped_column(String(120))
    origin_url: Mapped[str | None] = mapped_column(String(2048))
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    times_seen: Mapped[int] = mapped_column(default=1)


class ScoutDecision(Base):
    """Schema 9: Scout decision log for debugging; pruned with the events (30 days)."""

    __tablename__ = "scout_decisions"
    __table_args__ = (
        Index("ix_scout_decisions_job", "job_id", "id"),
        Index("ix_scout_decisions_created", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("search_jobs.id"))
    username: Mapped[str] = mapped_column(String(40))
    source_username: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(20))  # lead_created | lead_updated | skipped
    skip_reason: Mapped[str | None] = mapped_column(String(40))
    details: Mapped[str | None] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[int | None]
    followers: Mapped[int | None]
    contacts_present: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


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


# ---------- Outreach (schema 10) ----------


class OutreachTemplate(Base):
    __tablename__ = "outreach_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(String(4000))
    enabled: Mapped[bool] = mapped_column(default=True)
    # Schema 11: the internal template of the primary-outreach list; not listed as a template.
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class FollowUpSequence(Base):
    """Follow-up steps after an initial message: [{delay_days, template_id}]."""

    __tablename__ = "outreach_followup_sequences"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    steps: Mapped[list[dict]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OutreachCampaign(Base):
    __tablename__ = "outreach_campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','scheduled','running','paused','completed','cancelled','failed')"
        ),
        CheckConstraint("sender_strategy IN ('single','round_robin')"),
        Index("ix_outreach_campaigns_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    # UTC; None starts on "Start Campaign".
    scheduled_at: Mapped[datetime | None]
    template_id: Mapped[int] = mapped_column(ForeignKey("outreach_templates.id"))
    followup_sequence_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_followup_sequences.id")
    )
    sender_strategy: Mapped[str] = mapped_column(String(20), default="single")
    # Browser profile ids chosen as senders.
    sender_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Schema 11: message variants given in turn to queued recipients (else the template).
    message_variants: Mapped[list[str]] = mapped_column(JSON, default=list)
    total_recipients: Mapped[int] = mapped_column(default=0)
    queued_count: Mapped[int] = mapped_column(default=0)
    sent_count: Mapped[int] = mapped_column(default=0)
    skipped_count: Mapped[int] = mapped_column(default=0)
    failed_count: Mapped[int] = mapped_column(default=0)
    replied_count: Mapped[int] = mapped_column(default=0)


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_recipient"),
        CheckConstraint(
            "status IN ('pending','queued','sending','sent','skipped','failed','cancelled')"
        ),
        Index("ix_campaign_recipients_status", "campaign_id", "status"),
        Index("ix_campaign_recipients_lead", "lead_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("outreach_campaigns.id", ondelete="CASCADE")
    )
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    username: Mapped[str] = mapped_column(String(160))
    sender_account_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    skip_reason: Mapped[str | None] = mapped_column(String(40))
    failure_reason: Mapped[str | None] = mapped_column(String(40))
    # Readable detail of a skip or failure (never a raw platform response).
    reason_details: Mapped[str | None] = mapped_column(String(300))
    queued_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    failed_at: Mapped[datetime | None]
    replied_at: Mapped[datetime | None]
    # The browser may have sent the message but no result came back (crash, timeout):
    # never resent automatically; the user checks the Instagram thread.
    needs_review: Mapped[bool] = mapped_column(default=False)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    rendered_message: Mapped[str | None] = mapped_column(String(4000))


class OutboundMessageJob(Base):
    __tablename__ = "outbound_message_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending','ready','sending','sent','failed','cancelled')"),
        Index("ix_outbound_jobs_due", "status", "scheduled_at"),
        Index("ix_outbound_jobs_sender", "sender_account_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("outreach_campaigns.id", ondelete="CASCADE")
    )
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_recipients.id", ondelete="CASCADE")
    )
    # initial-outreach:<campaignId>:<leadId>; one job and at most one message per key.
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    sender_account_id: Mapped[str] = mapped_column(String(32))
    scheduled_at: Mapped[datetime] = mapped_column(default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    # Set when the worker hands the job to the browser; the result must bring it back.
    claim_token: Mapped[str | None] = mapped_column(String(40))
    claimed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    sent_at: Mapped[datetime | None]


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("lead_id", "platform", "sender_account_id", name="uq_conversation"),
        CheckConstraint("status IN ('waiting_reply','replied','stopped')"),
        Index("ix_conversations_lead", "lead_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(40), default="instagram")
    sender_account_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="waiting_reply")
    platform_thread_id: Mapped[str | None] = mapped_column(String(80))
    first_outbound_at: Mapped[datetime | None]
    last_outbound_at: Mapped[datetime | None]
    last_inbound_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("direction IN ('outbound','inbound')"),
        CheckConstraint("type IN ('initial','followup','reply')"),
        Index("ix_messages_conversation", "conversation_id", "id"),
        Index("ix_messages_sender_sent", "sender_account_id", "sent_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(10))
    type: Mapped[str] = mapped_column(String(10))
    body: Mapped[str] = mapped_column(String(4000), default="")
    sender_account_id: Mapped[str | None] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(default=utcnow)
    platform_message_id: Mapped[str | None] = mapped_column(String(80))
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_campaigns.id"))
    # Same key as the job: a second record of one initial message is impossible.
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)


class FollowUpJob(Base):
    """Planned follow-ups; sending them is the follow-up module's job."""

    __tablename__ = "outreach_followup_jobs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_id", "step_index", name="uq_followup_step"),
        CheckConstraint("status IN ('pending','sent','cancelled','failed')"),
        Index("ix_followup_jobs_due", "status", "scheduled_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    sequence_id: Mapped[int] = mapped_column(ForeignKey("outreach_followup_sequences.id"))
    step_index: Mapped[int]
    template_id: Mapped[int] = mapped_column(ForeignKey("outreach_templates.id"))
    scheduled_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(20), default="pending")
    cancel_reason: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OutreachSender(Base):
    """Sender account health, keyed by browser profile id."""

    __tablename__ = "outreach_senders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','auth_required','checkpoint','rate_limited','disabled')"
        ),
    )
    profile_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    reason: Mapped[str | None] = mapped_column(String(300))
    # rate_limited: the break ends here; other statuses wait for the user.
    until: Mapped[datetime | None]
    last_sent_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class OutreachEvent(Base):
    __tablename__ = "outreach_events"
    __table_args__ = (
        Index("ix_outreach_events_campaign", "campaign_id", "id"),
        Index("ix_outreach_events_created", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_campaigns.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OutreachWorkspace(Base):
    """The single primary-outreach list: usernames, message variants and senders."""

    __tablename__ = "outreach_workspace"
    id: Mapped[int] = mapped_column(primary_key=True)
    usernames: Mapped[list[str]] = mapped_column(JSON, default=list)
    messages: Mapped[list[str]] = mapped_column(JSON, default=list)
    sender_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # The campaign started from the list last.
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_campaigns.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


# ---------- iMessage through the user's iPhone (schema 12) ----------


class IMessageWorkspace(Base):
    """The single iMessage list: recipients, texts, attachments and bridge settings."""

    __tablename__ = "imessage_workspace"
    id: Mapped[int] = mapped_column(primary_key=True)
    # [{phone (E.164 number or an iMessage email), message}]; an empty message takes
    # the next message variant.
    recipients: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Before the variants: one common text; read as the only variant.
    message: Mapped[str] = mapped_column(String(2000), default="")
    messages: Mapped[list[str]] = mapped_column(JSON, default=list)
    attachment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Schema 14: a chain of messages every recipient gets in order, [{text, attachment_ids}];
    # empty means the variants and the common attachments above.
    sequence: Mapped[list[dict]] = mapped_column(JSON, default=list)
    protocol: Mapped[str] = mapped_column(String(10), default="legacy")
    shortcut_name: Mapped[str] = mapped_column(String(80), default="Verse iPhone Bridge")
    legacy_shortcut_name: Mapped[str] = mapped_column(String(80), default="Verse iMessage")
    delay_seconds: Mapped[int] = mapped_column(default=30)
    bind_ip: Mapped[str | None] = mapped_column(String(45))
    port: Mapped[int] = mapped_column(default=47615)
    # The bridge comes back after a restart of the core when it was on.
    bridge_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Bridge token encrypted for the OS user (secret_box); never stored in plain text.
    token_box: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class IMessageAttachment(Base):
    """A file copied into the app folder; served to the phone by id only."""

    __tablename__ = "imessage_attachments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class IMessageCampaign(Base):
    __tablename__ = "imessage_campaigns"
    __table_args__ = (
        CheckConstraint("status IN ('running','paused','stopped','finished')"),
        CheckConstraint("protocol IN ('legacy','v2')"),
        Index("ix_imessage_campaigns_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    protocol: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="running")
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str] = mapped_column(String(2000), default="")
    attachment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Schema 14, a chain: [{attachment_ids, run}] per message; a run is one launch of the
    # Shortcut. Empty for a single message with the attachments above.
    steps: Mapped[list[dict]] = mapped_column(JSON, default=list)
    delay_seconds: Mapped[int] = mapped_column(default=30)
    total: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]


class IMessageJob(Base):
    """One recipient of a campaign. Handed to the phone at most once by itself:
    pending -> issued -> execution_acknowledged; a lost ACK makes it uncertain."""

    __tablename__ = "imessage_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','issued','execution_acknowledged','uncertain','failed')"
        ),
        Index("ix_imessage_jobs_campaign", "campaign_id", "status", "position"),
        Index("ix_imessage_jobs_phone", "phone"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("imessage_campaigns.id", ondelete="CASCADE")
    )
    # The stable jobId of both protocols.
    key: Mapped[str] = mapped_column(String(40), unique=True)
    position: Mapped[int]
    # Index of the message in the campaign's chain (0 without a chain).
    step: Mapped[int] = mapped_column(default=0)
    # Phone number or email of the recipient.
    phone: Mapped[str] = mapped_column(String(254))
    message: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    issued_at: Mapped[datetime | None]
    # No ACK by then: the job becomes uncertain.
    deadline_at: Mapped[datetime | None]
    text_acked_at: Mapped[datetime | None]
    acked_at: Mapped[datetime | None]
    # text: the original Shortcut passed the text step; complete: text and attachments;
    # manual: the user checked the thread on the phone.
    ack_scope: Mapped[str | None] = mapped_column(String(10))
    ack_count: Mapped[int] = mapped_column(default=0)
    resolution: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(300))


class IMessageTemplate(Base):
    """Schema 14: a saved iMessage template, one or more messages of text and/or files."""

    __tablename__ = "imessage_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    folder: Mapped[str] = mapped_column(String(60), default="")
    # [{text, attachment_ids}] in sending order.
    parts: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class IMessageEvent(Base):
    __tablename__ = "imessage_events"
    __table_args__ = (Index("ix_imessage_events_campaign", "campaign_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("imessage_campaigns.id", ondelete="CASCADE")
    )
    job_id: Mapped[int | None]
    type: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CrmContact(Base):
    """Schema 13: a person in the Instagram or the iMessage CRM. The two CRMs are separate
    tables of the same shape; contacts move between them only by an explicit import."""

    __tablename__ = "crm_contacts"
    __table_args__ = (
        CheckConstraint("crm IN ('instagram','imessage')"),
        Index("ix_crm_contacts_crm", "crm", "deleted_at", "id"),
        Index("ix_crm_contacts_lead", "lead_id"),
        Index("ix_crm_contacts_remote", "remote_id", unique=True),
        Index("ix_crm_contacts_dirty", "dirty"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    crm: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(160))
    # Labels in display order; colors come from crm_statuses, unknown labels are neutral.
    statuses: Mapped[list[str]] = mapped_column(JSON, default=list)
    # [{"kind": "instagram" | "email" | "phone", "value": str}]; the first is the main one.
    channels: Mapped[list[dict]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(String(5000), default="")
    last_contact_at: Mapped[datetime | None]
    next_action: Mapped[str] = mapped_column(String(300), default="")
    next_action_at: Mapped[datetime | None]
    earned: Mapped[float] = mapped_column(default=0)
    potential: Mapped[float] = mapped_column(default=0)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    # In the trash since; null for live contacts.
    deleted_at: Mapped[datetime | None]
    # Schema 15, the shared CRM: the server's id, the owner's account, and whether the
    # row has changes the server has not received yet (set on every local change).
    remote_id: Mapped[str | None] = mapped_column(String(36))
    owner_id: Mapped[str | None] = mapped_column(String(36))
    owner_name: Mapped[str] = mapped_column(String(160), default="")
    dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class CrmTombstone(Base):
    """Schema 15: a contact deleted for good here, until the server learns about it."""

    __tablename__ = "crm_tombstones"
    remote_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CrmStatus(Base):
    """Schema 13: the configured statuses of one CRM («Настроить статусы»)."""

    __tablename__ = "crm_statuses"
    __table_args__ = (
        CheckConstraint("crm IN ('instagram','imessage')"),
        UniqueConstraint("crm", "label", name="uq_crm_status_label"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    crm: Mapped[str] = mapped_column(String(12))
    label: Mapped[str] = mapped_column(String(40))
    color: Mapped[str] = mapped_column(String(12), default="violet")
    # Schema 18: an optional emoji before the label; empty when there is none.
    emoji: Mapped[str] = mapped_column(String(16), default="")
    position: Mapped[int] = mapped_column(default=0)


class InboxScan(Base):
    """Schema 19: one reading of an account's outreach threads («Ответы»)."""

    __tablename__ = "inbox_scans"
    __table_args__ = (
        CheckConstraint("status IN ('running','done','stopped')"),
        Index("ix_inbox_scans_sender", "sender_account_id", "started_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    sender_account_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(10), default="running")
    days: Mapped[int] = mapped_column(default=30)
    total: Mapped[int] = mapped_column(default=0)
    done: Mapped[int] = mapped_column(default=0)
    replied: Mapped[int] = mapped_column(default=0)
    found: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
    # Why the scan stopped before the end; empty otherwise.
    reason: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    # The next thread is opened no earlier than this (the pause between threads).
    next_at: Mapped[datetime | None]


class InboxScanItem(Base):
    """One thread of a scan: pending → reading → done / error."""

    __tablename__ = "inbox_scan_items"
    __table_args__ = (
        CheckConstraint("status IN ('pending','reading','done','error')"),
        Index("ix_inbox_items_scan", "scan_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("inbox_scans.id", ondelete="CASCADE"))
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(10), default="pending")
    error: Mapped[str] = mapped_column(String(40), default="")
    # When the shell took the thread; a reading without a result for long is an error.
    claimed_at: Mapped[datetime | None]


class InboxFinding(Base):
    """A phone or email a person wrote in a reply, waiting for review."""

    __tablename__ = "inbox_findings"
    __table_args__ = (
        CheckConstraint("kind IN ('phone','email')"),
        CheckConstraint("status IN ('new','added','hidden')"),
        UniqueConstraint("lead_id", "kind", "value", name="uq_inbox_finding"),
        Index("ix_inbox_findings_status", "status", "found_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    sender_account_id: Mapped[str] = mapped_column(String(32))
    username: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10))
    value: Mapped[str] = mapped_column(String(200))
    raw: Mapped[str] = mapped_column(String(200), default="")
    snippet: Mapped[str] = mapped_column(String(300), default="")
    # The country code came from the default region, not from the message.
    guessed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), default="new")
    found_at: Mapped[datetime] = mapped_column(default=utcnow)
