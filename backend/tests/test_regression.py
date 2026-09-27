import csv
import json
import logging
import time

import pytest
from sqlalchemy import insert

from artist_lead_finder.database import open_database
from artist_lead_finder.discovery import DiscoveryEngine
from artist_lead_finder.jobs import DiscoveryManager
from artist_lead_finder.models import Lead, ProviderHealth, SearchJob
from artist_lead_finder.providers import MockProvider, ProviderError, generate_profiles
from artist_lead_finder.rpc import JsonLogFormatter
from artist_lead_finder.schemas import SearchConfiguration
from artist_lead_finder.service import ApplicationService


def test_settings_import_restart_and_secret_rejection(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([p.model_dump(mode="json") for p in generate_profiles(10)]))
    engine, sessions = open_database(tmp_path / "database.db")
    service = ApplicationService(sessions, tmp_path)
    assert service.call("providers.import", {"path": str(path)})["count"] == 10
    service.call("settings.save", {"minimum_score": 85, "enabled_providers": ["imported"]})
    with pytest.raises(ValueError):
        service.call("settings.save", {"api_key": "secret"})
    service.shutdown()
    engine.dispose()
    engine, sessions = open_database(tmp_path / "database.db")
    service = ApplicationService(sessions, tmp_path)
    assert service.settings()["minimum_score"] == 85
    assert len(next(p for p in service.providers if p.name == "imported").profiles) == 10
    service.shutdown()
    engine.dispose()


def test_10000_profiles_query_and_selected_all_export(tmp_path):
    engine, sessions = open_database(tmp_path / "large.db")
    service = ApplicationService(sessions, tmp_path)
    with sessions.begin() as session:
        session.execute(
            insert(Lead),
            [
                {
                    "platform": "test",
                    "username": f"artist{i}",
                    "followers": i,
                    "lead_score": i % 101,
                }
                for i in range(10000)
            ],
        )
    start = time.perf_counter()
    result = service.call("leads.list", {"page": 20, "page_size": 30, "minimum_score": 70})
    elapsed = time.perf_counter() - start
    assert len(result["items"]) == 30
    assert result["total"] > 3000
    # A generous smoke budget avoids turning hardware variance into a flaky microbenchmark.
    assert elapsed < 5
    path = tmp_path / "selected.csv"
    assert service.call("leads.export", {"path": str(path), "ids": [1, 2, 3]})["count"] == 3
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[-1]["followers"] == "0" or any(row["followers"] == "0" for row in rows)
    assert service.call("leads.export", {"path": str(path), "ids": []})["count"] == 0
    assert service.call("leads.export", {"path": str(path)})["count"] == 10000
    service.shutdown()
    engine.dispose()


def test_429_stops_provider_without_secret_in_recorded_error(tmp_path):
    class LimitedProvider(MockProvider):
        attempts = 0

        def search_by_keyword(self, value):
            self.attempts += 1
            raise ProviderError("429 token=do-not-store", "Limited")

    provider = LimitedProvider(1)
    engine, sessions = open_database(tmp_path / "limited.db")
    manager = DiscoveryManager(sessions, DiscoveryEngine([provider]))
    job_id = manager.start(SearchConfiguration(name="Limited", keywords=["artist", "rapper"]))
    manager.futures[job_id].result(timeout=10)
    assert provider.attempts == 1
    with sessions() as session:
        assert session.get(ProviderHealth, "mock").status == "Limited"
        assert "do-not-store" not in json.dumps(session.get(SearchJob, job_id).errors)
    manager.shutdown()
    engine.dispose()


def test_structured_logs_do_not_dump_exception_or_credentials():
    record = logging.LogRecord("test", logging.ERROR, "", 1, "provider_failure", (), None)
    record.api_key = "do-not-store"
    record.exc_info = (ValueError, ValueError("token=do-not-store"), None)
    assert "do-not-store" not in JsonLogFormatter().format(record)
