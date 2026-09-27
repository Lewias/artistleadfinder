from datetime import datetime, timezone

from sqlalchemy import func, select

from artist_lead_finder.database import open_database
from artist_lead_finder.discovery import DiscoveryEngine
from artist_lead_finder.jobs import DiscoveryManager
from artist_lead_finder.models import Lead, LeadSource, SearchJob
from artist_lead_finder.pipeline import CandidatePipeline
from artist_lead_finder.providers import Candidate, MockProvider
from artist_lead_finder.schemas import SearchConfiguration


def test_repeated_source_target_limit_and_manual_crm_status(tmp_path):
    engine, sessions = open_database(tmp_path / "limits.db")
    provider = MockProvider(3)
    provider.profiles = [
        Candidate(
            platform="mock",
            platform_user_id=str(i),
            username=f"artist{i}",
            bio="rapper",
            followers=2000,
            last_activity_at=datetime.now(timezone.utc),
        )
        for i in range(3)
    ]
    manager = DiscoveryManager(sessions, DiscoveryEngine([provider]), CandidatePipeline(sessions))
    config = SearchConfiguration(
        name="Limited",
        seed_accounts=["seed"],
        keywords=["rapper", "rapper"],
        minimum_score=0,
        target_leads=1,
    )
    job_id = manager.start(config)
    manager.futures[job_id].result(timeout=10)
    with sessions.begin() as session:
        job = session.get(SearchJob, job_id)
        assert job.candidates_found == 9
        assert job.profiles_analyzed == 3
        assert job.qualified_leads == 1
        assert session.scalar(select(func.count()).select_from(LeadSource)) == 6
        lead = session.scalar(select(Lead).where(Lead.username == "artist0"))
        lead.status = "contacted"
    second_id = manager.start(config)
    manager.futures[second_id].result(timeout=10)
    manager.shutdown()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 3
        assert session.scalar(select(Lead.status).where(Lead.username == "artist0")) == "contacted"
    engine.dispose()
