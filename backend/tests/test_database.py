import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from artist_lead_finder.database import open_database
from artist_lead_finder.models import Lead, LeadSource, SearchJob, Setting
from artist_lead_finder.schemas import SearchConfiguration


def test_restart_persists_leads_and_jobs(tmp_path):
    path = tmp_path / "leads.sqlite3"
    engine, sessions = open_database(path)
    with sessions.begin() as session:
        session.add(SearchJob(name="September", status="completed"))
        session.add(Lead(platform="mock", platform_user_id="123", username="artist"))
    engine.dispose()
    engine, sessions = open_database(path)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        assert session.scalar(select(SearchJob.name)) == "September"
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
        assert session.scalar(text("PRAGMA journal_mode")) == "wal"
    engine.dispose()


def test_schema_16_speeds_up_the_default_pace_only(tmp_path):
    path = tmp_path / "leads.sqlite3"
    engine, sessions = open_database(path)
    with sessions.begin() as session:
        session.execute(text("DELETE FROM schema_migrations WHERE version = 16"))
        session.add_all(
            [
                Setting(key="page_delay_min", value=8),
                Setting(key="page_delay_max", value=30),  # chosen by the user
                Setting(key="profiles_per_hour", value=60),
            ]
        )
    engine.dispose()
    engine, sessions = open_database(path)
    with sessions() as session:
        values = dict(session.execute(select(Setting.key, Setting.value)).all())
    assert values == {"page_delay_min": 4, "page_delay_max": 30, "profiles_per_hour": 200}
    engine.dispose()


def test_identity_unique_and_foreign_keys(tmp_path):
    engine, sessions = open_database(tmp_path / "test.db")
    with sessions.begin() as session:
        session.add(Lead(platform="mock", platform_user_id="123", username="artist"))
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(Lead(platform="mock", platform_user_id="123", username="renamed"))
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            LeadSource(
                lead_id=999,
                search_job_id=999,
                source_provider="mock",
                source_type="keyword",
                source_value="rapper",
            )
        )
    engine.dispose()


def test_search_validates_ranges_and_limits():
    assert SearchConfiguration(name="Search").minimum_score == 70
    for invalid in (
        {"min_followers": 2000, "max_followers": 1000},
        {"activity_days": 2},
        {"minimum_score": 101},
        {"target_leads": 0},
    ):
        with pytest.raises(ValidationError):
            SearchConfiguration(name="Search", **invalid)
