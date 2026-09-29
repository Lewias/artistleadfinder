"""Lead Scout side of the CRM: lead identity, source history, lead details and run metrics.

Everything here works in the caller's session, so one Scout decision (lead, source
relation, processed profile, run and source metrics, decision log, event) commits or
rolls back as a whole.
"""

from datetime import datetime

from sqlalchemy import select

from ..models import Lead, LeadScoutProfile, ScoutDecision, ScoutLeadSource, ScoutSource
from .classification import FinalClassificationResult
from .decision import CandidateRef
from .profiles import NormalizedInstagramProfile

# ScoutRun.stats keys shown as run progress (older runs lack the newer ones).
RUN_COUNTERS = (
    "discovered",
    "resolved",
    "classified",
    "analyzed",
    "leads",
    "leads_updated",
    "skipped",
    "errors",
)
# Source table totals (ScoutSource columns) per run counter.
SOURCE_COUNTERS = {
    "discovered": "candidates_found",
    "resolved": "profiles_resolved",
    "leads": "leads_found",
    "skipped": "profiles_skipped",
    "errors": "errors_count",
}


def existing_lead(session, instagram_id: str | None, username: str) -> Lead | None:
    """Lead identity: the Instagram user id first, the normalized username as fallback."""
    if instagram_id:
        lead = session.scalar(
            select(Lead).where(Lead.platform == "instagram", Lead.platform_user_id == instagram_id)
        )
        if lead is not None:
            return lead
    lead = session.scalar(
        select(Lead).where(Lead.platform == "instagram", Lead.username == username.casefold())
    )
    # A username now used by another Instagram account is not the same lead.
    if lead and instagram_id and lead.platform_user_id not in (None, instagram_id):
        return None
    return lead


def origin_id(origin_url: str | None) -> str | None:
    """Shortcode or story id of the publication a candidate came from."""
    if not origin_url:
        return None
    parts = [part for part in origin_url.split("?")[0].split("/") if part]
    return parts[-1][:120] if parts else None


def record_source(session, lead_id: int, ref: CandidateRef, now: datetime) -> bool:
    """Add or refresh the lead's source relation; True when the source is new for it."""
    row = session.scalar(
        select(ScoutLeadSource).where(
            ScoutLeadSource.lead_id == lead_id,
            ScoutLeadSource.source_username == ref.source_username,
            ScoutLeadSource.discovery_method == ref.method,
        )
    )
    if row is None:
        session.add(
            ScoutLeadSource(
                lead_id=lead_id,
                source_username=ref.source_username,
                discovery_method=ref.method,
                origin_id=ref.origin_id or origin_id(ref.origin_url),
                origin_url=ref.origin_url,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        return True
    row.times_seen += 1
    row.last_seen_at = now
    if ref.origin_url:
        row.origin_url, row.origin_id = ref.origin_url, ref.origin_id or origin_id(ref.origin_url)
    return False


def source_history(session, lead_id: int) -> list[dict]:
    rows = session.scalars(
        select(ScoutLeadSource)
        .where(ScoutLeadSource.lead_id == lead_id)
        .order_by(ScoutLeadSource.first_seen_at, ScoutLeadSource.id)
    )
    return [
        {
            "source_username": row.source_username,
            "discovery_method": row.discovery_method,
            "origin_url": row.origin_url,
            "first_seen_at": row.first_seen_at.isoformat(),
            "last_seen_at": row.last_seen_at.isoformat(),
            "times_seen": row.times_seen,
        }
        for row in rows
    ]


def fill_scout_profile(
    session,
    lead_id: int,
    profile: NormalizedInstagramProfile,
    classification: FinalClassificationResult | None,
    ref: CandidateRef,
    now: datetime,
) -> LeadScoutProfile:
    """Scout details of the lead. The first discovery (source, method, origin) is kept;
    later sources go to the source history. Profile data and classification are current."""
    row = session.get(LeadScoutProfile, lead_id)
    if row is None:
        row = LeadScoutProfile(
            lead_id=lead_id,
            first_seen_at=now,
            source_username=ref.source_username,
            discovery_method=ref.method,
            origin_url=ref.origin_url,
            origin_id=ref.origin_id or origin_id(ref.origin_url),
            profile_type="other",
        )
        session.add(row)
    row.instagram_id = profile.id or row.instagram_id
    row.posts_count = profile.posts_count
    row.emails, row.phones = list(profile.emails), list(profile.phones)
    row.bio_links = list(profile.bio_links)
    row.category_name = profile.category_name
    row.is_business = profile.is_business
    if classification is not None:
        local, ai = classification.local, classification.ai
        row.profile_type = classification.category
        row.profile_score = local.score
        row.profile_confidence = classification.confidence
        row.profile_reasons = local.reasons[:20]
        row.profile_decided_by = classification.decided_by
        row.local_category, row.local_confidence = local.category, local.confidence
        row.ai_category = ai.category if ai else None
        row.ai_model = ai.model if ai else None
        row.ai_confidence = ai.confidence if ai else None
    row.last_seen_at = now
    return row


def count(stats: dict, session, source_url: str | None, key: str, amount: int = 1) -> None:
    """Add to a run counter and the matching total of its source row."""
    stats[key] = int(stats.get(key) or 0) + amount
    column = SOURCE_COUNTERS.get(key)
    source = session.get(ScoutSource, source_url) if column and source_url else None
    if source is not None:
        setattr(source, column, (getattr(source, column) or 0) + amount)


def count_skip(stats: dict, session, source_url: str | None, reason: str) -> None:
    count(stats, session, source_url, "skipped")
    skips = dict(stats.get("skips") or {})
    skips[reason] = skips.get(reason, 0) + 1
    stats["skips"] = skips


def log_decision(
    session,
    job_id: int,
    ref: CandidateRef,
    decision: str,
    *,
    profile: NormalizedInstagramProfile | None = None,
    classification: FinalClassificationResult | None = None,
    reason: str | None = None,
    details: str | None = None,
) -> None:
    session.add(
        ScoutDecision(
            job_id=job_id,
            username=ref.username,
            source_username=ref.source_username,
            decision=decision,
            skip_reason=reason,
            details=(details or "")[:300] or None,
            category=classification.category if classification else None,
            confidence=classification.confidence if classification else None,
            followers=profile.followers_count if profile else None,
            contacts_present=bool(profile and (profile.emails or profile.phones)),
        )
    )
