from datetime import datetime, timezone

from sqlalchemy import func, select

from artist_lead_finder.analysis import ProfileAnalyzer
from artist_lead_finder.classification import RuleBasedClassifier
from artist_lead_finder.database import open_database
from artist_lead_finder.discovery import DiscoveryEngine
from artist_lead_finder.jobs import DiscoveryManager
from artist_lead_finder.models import Lead, LeadAnalysis, LeadScoreBreakdown, LeadSource, SearchJob
from artist_lead_finder.pipeline import CandidatePipeline
from artist_lead_finder.providers import Candidate, DatasetProvider
from artist_lead_finder.schemas import SearchConfiguration
from artist_lead_finder.scoring import LeadScorer


def artist():
    return Candidate(
        platform="test",
        platform_user_id="1",
        username="artist",
        followers=2000,
        bio="Trap rapper. New EP out now. #newmusic",
        external_url="https://open.spotify.com/artist/1",
        last_activity_at=datetime.now(timezone.utc),
    )


def test_full_score_and_hard_filters():
    candidate = artist()
    signals = ProfileAnalyzer().analyze(candidate)
    classification = RuleBasedClassifier().classify(signals)
    config = SearchConfiguration(name="Test", genres=["Trap"])
    result = LeadScorer().score(candidate, signals, classification, config)
    assert result.score == 100
    assert sum(item.points for item in result.breakdown) == 100
    assert result.qualified
    candidate.is_private = True
    assert not LeadScorer().score(candidate, signals, classification, config).qualified


def test_end_to_end_three_sources_persist_analysis_and_unique_counts(tmp_path):
    engine, sessions = open_database(tmp_path / "pipeline.db")
    provider = DatasetProvider([artist()])
    provider.name = "test"
    manager = DiscoveryManager(sessions, DiscoveryEngine([provider]), CandidatePipeline(sessions))
    job_id = manager.start(
        SearchConfiguration(
            name="Test",
            seed_accounts=["@artist"],
            keywords=["rapper"],
            hashtags=["#newmusic"],
            genres=["Trap"],
        )
    )
    manager.futures[job_id].result(timeout=10)
    manager.shutdown()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        assert session.scalar(select(func.count()).select_from(LeadSource)) == 3
        assert session.scalar(select(func.count()).select_from(LeadScoreBreakdown)) == 7
        assert session.scalar(select(LeadAnalysis.confidence)) == 0.95
        job = session.get(SearchJob, job_id)
        assert job.profiles_analyzed == 1
        assert job.qualified_leads == 1
    engine.dispose()
