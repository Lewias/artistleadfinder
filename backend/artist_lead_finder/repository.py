"""Transactional identity resolution and provenance persistence."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .discovery import DiscoveryRecord
from .models import Lead, LeadSource
from .normalization import normalize


class IdentityConflict(ValueError):
    pass


def upsert_candidate(
    session: Session, job_id: int, record: DiscoveryRecord
) -> tuple[Lead, bool, bool]:
    candidate = normalize(record.candidate)
    by_id = (
        session.scalar(
            select(Lead).where(
                Lead.platform == candidate.platform,
                Lead.platform_user_id == candidate.platform_user_id,
            )
        )
        if candidate.platform_user_id
        else None
    )
    by_name = session.scalar(
        select(Lead).where(Lead.platform == candidate.platform, Lead.username == candidate.username)
    )
    if by_id and by_name and by_id.id != by_name.id:
        # Do not silently merge two known accounts on username reuse.
        raise IdentityConflict("Имя профиля конфликтует с сохранённым идентификатором.")
    if (
        by_name
        and candidate.platform_user_id
        and by_name.platform_user_id not in (None, candidate.platform_user_id)
    ):
        raise IdentityConflict("Имя профиля используется другим аккаунтом.")
    lead = by_id or by_name
    created = lead is None
    if lead is None:
        lead = Lead(platform=candidate.platform, username=candidate.username)
        session.add(lead)
    data = candidate.model_dump(exclude={"recent_content"})
    # A fallback-only observation must never erase a stable identity.
    if candidate.platform_user_id is None and lead.platform_user_id:
        data.pop("platform_user_id")
    for key, value in data.items():
        setattr(lead, key, value)
    session.flush()
    already_in_job = (
        session.scalar(
            select(LeadSource.id)
            .where(LeadSource.lead_id == lead.id, LeadSource.search_job_id == job_id)
            .limit(1)
        )
        is not None
    )
    source = session.scalar(
        select(LeadSource).where(
            LeadSource.lead_id == lead.id,
            LeadSource.search_job_id == job_id,
            LeadSource.source_provider == record.provider,
            LeadSource.source_type == record.source_type,
            LeadSource.source_value == record.source_value,
        )
    )
    if source is None:
        session.add(
            LeadSource(
                lead_id=lead.id,
                search_job_id=job_id,
                source_provider=record.provider,
                source_type=record.source_type,
                source_value=record.source_value,
            )
        )
    return lead, created, already_in_job
