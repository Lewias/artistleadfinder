import csv

import pytest
from sqlalchemy import func, select

from artist_lead_finder.browser_capture import count_from, parse_snapshot, profile_url
from artist_lead_finder.database import open_database
from artist_lead_finder.models import Lead, LeadAnalysis, LeadSource, SearchJob
from artist_lead_finder.service import ApplicationService


def snapshot(username="test_artist", followers="12.5K Followers"):
    return dict(
        url=f"https://www.instagram.com/{username}/",
        ready=True,
        title=f"Artist Name (@{username}) • Instagram photos",
        header=f"{username}\n{followers}\n200 following",
        description=f"{followers} - Artist (@{username}) on Instagram: "
        '"Independent hip-hop artist. New single out now."',
        external_url="https://open.spotify.com/artist/test",
    )


def test_browser_parser_counts_boundaries_and_unknown_fields():
    assert profile_url("@Artist.Name") == "https://www.instagram.com/artist.name/"
    assert count_from("1,234 Followers", "followers") == 1234
    assert count_from("1,2 тыс. подписчиков", "followers") == 1200
    assert count_from("1.5M followers", "followers") == 1500000
    assert count_from("artist123\n12.5K Followers", "followers") == 12500
    assert count_from("Подписчики: 5M, Подписки: 2,885", "followers") == 5000000
    for value in [
        "https://instagram.com.evil.test/name/",
        "https://instagram.com/p/123/",
        "https://x:secret@instagram.com/name/",
        "https://instagram.com/accounts/",
    ]:
        with pytest.raises(ValueError):
            profile_url(value)
    candidate, evidence = parse_snapshot(snapshot())
    assert candidate.followers == 12500
    assert "hip-hop artist" in candidate.bio
    assert evidence["unknown_fields"] == ["last_activity_at"]
    assert candidate.last_activity_at is None
    russian = snapshot()
    russian["description"] = "Подписчики: 5M, Подписки: 2,885"
    russian["header"] = (
        "test_artist\n4,7 млн подписчиков\n2 825 подписок\nArtist Name\n"
        "Independent hip-hop artist. New single out now.\nartist.lnk.to/music"
    )
    candidate, evidence = parse_snapshot(russian)
    assert candidate.followers == 4700000
    assert candidate.bio == "Independent hip-hop artist. New single out now."
    assert evidence["bio_method"] == "header_excerpt"
    with pytest.raises(ValueError):
        parse_snapshot({**snapshot(), "blocked": True})
    with pytest.raises(ValueError):
        parse_snapshot(snapshot(), "https://www.instagram.com/another/")


def test_browser_queue_pipeline_control_dedupe_and_restart(tmp_path):
    engine, sessions = open_database(tmp_path / "browser.db")
    service = ApplicationService(sessions, tmp_path)
    params = dict(
        profile_id="0" * 32,
        urls=["@test_artist", "@test_artist", "@second"],
    )
    identifier = service.call("capture.start_internal", params)["id"]
    assert service.call("capture.state", {"id": identifier})["total"] == 2
    service.call("jobs.control", {"id": identifier, "action": "pause"})
    assert not service.call("capture.commit_internal", {"id": identifier, "snapshot": snapshot()})[
        "saved"
    ]
    service.call("jobs.control", {"id": identifier, "action": "resume"})
    result = service.call("capture.commit_internal", {"id": identifier, "snapshot": snapshot()})
    assert result["saved"] and result["score"] > 0
    with sessions() as session:
        analysis = session.get(LeadAnalysis, result["lead_id"])
        assert analysis.extracted_signals["browser_capture"]["method"] == "browser_dom"
        assert session.get(SearchJob, identifier).profiles_analyzed == 1
        assert "discovery" not in analysis.extracted_signals["browser_capture"]
    service.call("capture.error_internal", {"id": identifier, "reason": "blocked"})
    assert service.call("capture.state", {"id": identifier})["status"] == "paused"
    service.call("jobs.control", {"id": identifier, "action": "cancel"})
    assert not service.call(
        "capture.commit_internal", {"id": identifier, "snapshot": snapshot("second")}
    )["saved"]
    service.call("leads.status", {"id": result["lead_id"], "status": "contacted"})
    next_id = service.call("capture.start_internal", {**params, "urls": ["@test_artist"]})["id"]
    partial = snapshot(followers="")
    partial["description"] = ""
    service.call("capture.commit_internal", {"id": next_id, "snapshot": partial})
    listed = service.call("leads.list", {"source": "instagram_browser"})
    assert listed["total"] == 1
    assert "followers" in listed["items"][0]["unknown_fields"]
    export_path = tmp_path / "browser.csv"
    service.call("leads.export", {"path": str(export_path), "query": {}})
    with export_path.open(encoding="utf-8-sig", newline="") as file:
        exported = next(csv.DictReader(file))
    assert exported["followers"] == ""
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        assert session.scalar(select(func.count()).select_from(LeadSource)) == 2
        lead = session.get(Lead, result["lead_id"])
        assert lead.followers == 12500 and lead.status == "contacted"
        assert service.call("capture.state", {"id": next_id})["status"] == "completed"
    pending = service.call("capture.start_internal", params)["id"]
    service.shutdown()
    restored = ApplicationService(sessions, tmp_path)
    assert restored.call("capture.state", {"id": pending})["stage"] == "interrupted"
    restored.shutdown()
    engine.dispose()
