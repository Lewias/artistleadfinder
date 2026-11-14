from artist_lead_finder.database import open_database
from artist_lead_finder.models import SearchJob
from artist_lead_finder.service import ApplicationService


def test_nav_counts_return_only_the_open_channel(tmp_path):
    engine, sessions = open_database(tmp_path / "nav.db")
    app = ApplicationService(sessions, tmp_path)
    with sessions.begin() as session:
        session.add_all([SearchJob(name="One", status="completed"), SearchJob(name="Two")])
    instagram = app.call("nav.counts", {"channel": "instagram"})
    assert instagram["history"] == 2 + len(app.call("instagram_warmup.history", {}))
    assert instagram["leads"] == 0
    assert set(instagram) <= {"crm", "discovery", "profiles", "leads", "history"}
    assert set(app.call("nav.counts", {"channel": "email"})) <= {"email-crm"}
    assert app.call("nav.counts", {"channel": "unknown"}) == {}
    app.shutdown()
    engine.dispose()


def test_nav_counts_skip_a_failing_badge(tmp_path, monkeypatch):
    engine, sessions = open_database(tmp_path / "nav.db")
    app = ApplicationService(sessions, tmp_path)

    def broken(_params):
        raise RuntimeError("down")

    monkeypatch.setitem(app.handlers, "scout.sources", broken)
    counts = app.call("nav.counts", {"channel": "instagram"})
    assert "discovery" not in counts
    assert counts["leads"] == 0
    app.shutdown()
    engine.dispose()
