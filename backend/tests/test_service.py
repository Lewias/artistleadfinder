import csv

import pytest

from artist_lead_finder.database import open_database
from artist_lead_finder.models import Lead
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
