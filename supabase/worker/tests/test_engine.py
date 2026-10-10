"""The app's real core as the cloud parser uses it, without a browser."""

import uuid

import pytest
from artist_lead_finder.models import Lead, LeadScoutProfile, ScoutDecision, SearchJob
from sqlalchemy import select

from cloud_worker.engine import Engine

PROFILE = "b" * 32


@pytest.fixture
def engine(tmp_path):
    item = Engine(tmp_path, str(uuid.uuid4()))
    yield item
    item.close()


def test_the_session_is_kept_encrypted_and_read_back(engine):
    record = {
        "cookies": [{"name": "sessionid", "value": "s1", "domain": ".instagram.com", "path": "/"}],
        "proxy": {"scheme": "http", "host": "1.2.3.4", "port": 8000},
    }
    engine.put_session(PROFILE, "Рабочий", record)
    vault = (engine.folder / "browser-sessions" / f"{PROFILE}.vault").read_bytes()
    assert b"sessionid" not in vault
    assert engine.session(PROFILE) == record
    names = [item["name"] for item in engine.call("browser.list", {})]
    assert names == ["Рабочий"]


def test_only_scout_settings_are_taken_and_ai_stays_off(engine):
    before = engine.call("settings.get", {})
    methods = ["tagged"] if before["scout_methods"] != ["tagged"] else ["posts"]
    engine.apply_settings(
        {"scout_methods": methods, "scout_ai_mode": "always", "outreach_daily_limit_per_sender": 1}
    )
    after = engine.call("settings.get", {})
    assert after["scout_methods"] == methods and after["scout_ai_mode"] == "off"
    assert after["outreach_daily_limit_per_sender"] == before["outreach_daily_limit_per_sender"]


def test_new_leads_come_once_with_what_the_scout_learned(engine):
    with engine.sessions.begin() as session:
        job = SearchJob(name="Lead Scout", status="running")
        session.add(job)
        lead = Lead(
            platform="instagram",
            username="jay.carter",
            display_name="Jay Carter",
            bio="Rapper. jay@music.com",
            followers=4200,
            external_url="https://open.spotify.com/artist/x",
            profile_url="https://www.instagram.com/jay.carter/",
        )
        session.add(lead)
        session.flush()
        session.add(
            LeadScoutProfile(
                lead_id=lead.id,
                instagram_id="555",
                emails=["jay@music.com"],
                phones=[],
                profile_type="artist",
                profile_score=80,
                profile_confidence=91,
                profile_reasons=["streaming links", "release wording"],
                source_username="rapgoat.tv",
                discovery_method="posts",
                origin_url="https://www.instagram.com/p/C1/",
            )
        )
        session.add(
            ScoutDecision(
                job_id=job.id,
                username="jay.carter",
                source_username="rapgoat.tv",
                decision="lead_created",
            )
        )
        job_id = job.id
    [found] = engine.new_leads(job_id)
    assert found["username"] == "jay.carter" and found["instagram_id"] == "555"
    assert found["category"] == "ARTIST" and found["confidence"] == 91
    assert found["reason"] == "streaming links; release wording"
    assert found["via"] == ["posts"] and found["origins"][0]["source"] == "rapgoat.tv"
    assert found["profile"]["emails"] == ["jay@music.com"]
    assert found["profile"]["links"] == ["https://open.spotify.com/artist/x"]
    assert engine.new_leads(job_id) == []
    with engine.sessions() as session:
        assert session.scalar(select(ScoutDecision.id)) == engine.seen
