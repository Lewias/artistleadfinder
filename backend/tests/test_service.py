import csv

import pytest

from artist_lead_finder.database import open_database
from artist_lead_finder.models import Lead, LeadAnalysis, LeadSource, SearchJob
from artist_lead_finder.service import ApplicationService, LeadQuery


def test_api_pagination_filters_and_csv(tmp_path):
    engine, sessions = open_database(tmp_path / "service.db")
    service = ApplicationService(sessions, tmp_path)
    with sessions.begin() as session:
        session.add_all(
            [
                Lead(
                    platform="test",
                    username=f"artist{i}",
                    followers=1000 + i,
                    bio="=SUM(1,2)",
                    lead_score=i % 101,
                    primary_genre="Trap" if i % 2 else "Pop",
                )
                for i in range(120)
            ]
        )
    result = service.call("leads.list", {"genre": "Trap", "page_size": 10})
    assert result["total"] == 60
    assert len(result["items"]) == 10
    assert result["items"][0]["lead_score"] >= result["items"][1]["lead_score"]
    assert service.call("leads.list", {"search": "artist_"})["total"] == 0
    path = tmp_path / "export.csv"
    exported = service.call("leads.export", {"path": str(path), "query": {"genre": "Trap"}})
    assert exported["count"] == 60
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["bio"].startswith("'=")
    with pytest.raises(Exception):
        LeadQuery(sort="DROP TABLE leads")
    service.shutdown()
    engine.dispose()


def test_export_joins_sources_and_hides_unknown_fields_across_chunks(tmp_path):
    engine, sessions = open_database(tmp_path / "export.db")
    service = ApplicationService(sessions, tmp_path)
    count = 1200  # spans several export chunks
    with sessions.begin() as session:
        session.add(SearchJob(name="export"))
        session.add_all(
            [Lead(platform="test", username=f"a{i}", followers=500 + i) for i in range(count)]
        )
    with sessions.begin() as session:
        for lead_id in range(1, count + 1):
            for provider in ("mock", "imported"):
                session.add(
                    LeadSource(
                        lead_id=lead_id,
                        search_job_id=1,
                        source_provider=provider,
                        source_type="seed",
                        source_value=str(lead_id),
                    )
                )
            if lead_id % 2:
                session.add(
                    LeadAnalysis(
                        lead_id=lead_id,
                        is_artist=True,
                        confidence=0.5,
                        extracted_signals={"browser_capture": {"unknown_fields": ["followers"]}},
                    )
                )
    path = tmp_path / "all.csv"
    assert service.call("leads.export", {"path": str(path)})["count"] == count
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = {row["username"]: row for row in csv.DictReader(file)}
    assert rows["a0"]["followers"] == ""  # lead 1 has an analysis with unknown followers
    assert rows["a1"]["followers"] == "501"
    assert rows["a1199"]["source"] == "mock:seed:1200; imported:seed:1200"
    with pytest.raises(ValueError):
        service.call("no.such_method", {})
    service.shutdown()
    engine.dispose()
