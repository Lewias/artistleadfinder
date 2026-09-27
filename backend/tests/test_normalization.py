from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from artist_lead_finder.database import open_database
from artist_lead_finder.discovery import DiscoveryRecord
from artist_lead_finder.models import Lead, LeadSource, SearchJob
from artist_lead_finder.normalization import normalize
from artist_lead_finder.providers import Candidate
from artist_lead_finder.repository import IdentityConflict, upsert_candidate


def test_normalization():
    candidate = normalize(
        Candidate(
            platform="MOCK",
            username=" @Artist123 ",
            bio="Hello\x00",
            last_activity_at=datetime(2026, 9, 19),
        )
    )
    assert candidate.username == "artist123"
    assert candidate.bio == "Hello"
    assert candidate.last_activity_at.tzinfo == timezone.utc


def test_three_sources_one_lead_and_restart(tmp_path):
    path = tmp_path / "leads.db"
    engine, sessions = open_database(path)
    with sessions.begin() as session:
        job = SearchJob(name="Three sources")
        session.add(job)
        session.flush()
        candidate = Candidate(platform="mock", username="@Artist123")
        for kind, value in [("seed", "@seed"), ("keyword", "rapper"), ("hashtag", "#newmusic")]:
            upsert_candidate(session, job.id, DiscoveryRecord(candidate, "mock", kind, value))
        # Repeated source and later stable id must not create new rows.
        candidate = candidate.model_copy(update={"platform_user_id": "123"})
        upsert_candidate(session, job.id, DiscoveryRecord(candidate, "mock", "seed", "@seed"))
    engine.dispose()
    engine, sessions = open_database(path)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        assert session.scalar(select(func.count()).select_from(LeadSource)) == 3
        assert session.scalar(select(Lead.platform_user_id)) == "123"
    engine.dispose()


def test_rename_and_username_reuse_conflict(tmp_path):
    engine, sessions = open_database(tmp_path / "leads.db")
    with sessions.begin() as session:
        job = SearchJob(name="Names")
        session.add(job)
        session.flush()
        for name in ["old", "new"]:
            upsert_candidate(
                session,
                job.id,
                DiscoveryRecord(
                    Candidate(platform="mock", username=name, platform_user_id="123"),
                    "mock",
                    "seed",
                    name,
                ),
            )
        with pytest.raises(IdentityConflict):
            upsert_candidate(
                session,
                job.id,
                DiscoveryRecord(
                    Candidate(platform="mock", username="new", platform_user_id="456"),
                    "mock",
                    "seed",
                    "new",
                ),
            )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        assert session.scalar(select(Lead.username)) == "new"
    engine.dispose()
