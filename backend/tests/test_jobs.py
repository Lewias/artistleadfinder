import threading

from artist_lead_finder.database import open_database
from artist_lead_finder.discovery import DiscoveryEngine
from artist_lead_finder.jobs import DiscoveryManager
from artist_lead_finder.models import ProviderHealth, SearchJob
from artist_lead_finder.providers import MetaInstagramProvider, MockProvider
from artist_lead_finder.schemas import SearchConfiguration


def test_complete_and_recover_interrupted_job(tmp_path):
    engine, sessions = open_database(tmp_path / "jobs.db")
    manager = DiscoveryManager(sessions, DiscoveryEngine([MockProvider(10)]))
    job_id = manager.start(SearchConfiguration(name="Demo", seed_accounts=["seed"]))
    manager.futures[job_id].result(timeout=10)
    manager.shutdown()
    with sessions.begin() as session:
        assert session.get(SearchJob, job_id).status == "completed"
        assert session.get(SearchJob, job_id).profiles_analyzed == 10
        interrupted = SearchJob(name="Interrupted", status="running")
        session.add(interrupted)
        session.flush()
        interrupted_id = interrupted.id
    manager = DiscoveryManager(sessions, DiscoveryEngine([]))
    with sessions() as session:
        assert session.get(SearchJob, interrupted_id).stage == "interrupted"
    manager.shutdown()
    engine.dispose()


def test_cancel_and_pause_do_not_corrupt_database(tmp_path):
    engine, sessions = open_database(tmp_path / "jobs.db")
    entered, release = threading.Event(), threading.Event()

    def processor(_job, _record, _config):
        entered.set()
        assert release.wait(5)
        return False, False

    manager = DiscoveryManager(sessions, DiscoveryEngine([MockProvider(100)]), processor)
    job_id = manager.start(SearchConfiguration(name="Demo", seed_accounts=["seed"]))
    assert entered.wait(5)
    manager.control(job_id, "pause")
    with sessions() as session:
        assert session.get(SearchJob, job_id).status == "paused"
    manager.control(job_id, "cancel")
    release.set()
    manager.futures[job_id].result(timeout=10)
    manager.shutdown()
    with sessions() as session:
        job = session.get(SearchJob, job_id)
        assert job.status == "cancelled"
        assert job.profiles_analyzed == 1
    engine.dispose()


def test_provider_auth_failure_is_recorded_and_not_retried(tmp_path):
    engine, sessions = open_database(tmp_path / "jobs.db")
    manager = DiscoveryManager(sessions, DiscoveryEngine([MetaInstagramProvider()]))
    job_id = manager.start(SearchConfiguration(name="API", keywords=["rapper", "artist"]))
    manager.futures[job_id].result(timeout=10)
    manager.shutdown()
    with sessions() as session:
        assert session.get(SearchJob, job_id).status == "failed"
        assert len(session.get(SearchJob, job_id).errors) == 1
        assert session.get(ProviderHealth, "meta_instagram").status == "Authentication Required"
    engine.dispose()


def test_clear_history_hides_finished_searches_and_keeps_active(tmp_path):
    from artist_lead_finder.service import ApplicationService

    engine, sessions = open_database(tmp_path / "jobs.db")
    app = ApplicationService(sessions, tmp_path)
    with sessions.begin() as session:
        session.add_all(
            [
                SearchJob(name="Done", status="completed"),
                SearchJob(name="Failed", status="failed"),
                SearchJob(name="Active", status="paused"),
            ]
        )
    assert app.call("jobs.clear_history", {}) == {"ok": True}
    assert [job["name"] for job in app.call("jobs.list", {})] == ["Active"]
    with sessions() as session:
        assert session.query(SearchJob).count() == 3
    app.shutdown()
    engine.dispose()
