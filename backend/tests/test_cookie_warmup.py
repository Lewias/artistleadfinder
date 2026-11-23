"""Cookie warm-up through the application's RPC handlers with a stand-in browser worker."""

import json
import threading

import pytest
from pydantic import ValidationError

from artist_lead_finder import browser_sessions, cookie_warmup_browser
from artist_lead_finder.browser_idle import window_work
from artist_lead_finder.cookie_warmup import KEY, CookieWarmup, CookieWarmupSettings
from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import Setting
from artist_lead_finder.service import ApplicationService


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_sessions, "protect", lambda data, decrypt=False: data)
    engine, sessions = open_database(tmp_path / "app.db")
    service = ApplicationService(sessions, tmp_path)
    service.call(
        "browser.external_add",
        {
            "provider": "nst",
            "token": "fixture-secret",
            "profiles": [{"id": "abc", "name": "NST"}],
        },
    )
    profile = service.call("cookie_warmup.state", {})["profiles"][0]["id"]
    yield service, profile
    service.shutdown()
    engine.dispose()


def test_cookie_start_persists_run_and_settings_and_keeps_credentials_private(app, monkeypatch):
    service, profile = app

    def browse(connection, settings, stopping, report):
        assert connection["token"] == "fixture-secret" and connection["id"] == "abc"
        assert settings.sites == ["https://www.reddit.com/"]
        report("Просмотр завершён", current="reddit.com", visited=True)

    monkeypatch.setattr(cookie_warmup_browser, "browse", browse)
    service.call(
        "cookie_warmup.start",
        {
            "profile_id": profile,
            "settings": {"minutes": 3, "sites": ["https://www.reddit.com/"]},
        },
    )
    service.cookie_warmup.thread.join(5)
    assert not service.cookie_warmup.thread.is_alive()
    state = service.call("cookie_warmup.state", {})
    assert state["run"]["status"] == "done" and state["run"]["visited"] == 1
    assert state["settings"]["minutes"] == 3 and state["run"]["completed_at"]
    assert "fixture-secret" not in json.dumps(state)
    reopened = CookieWarmup(service.sessions, service.browser_sessions)
    assert reopened.state()["run"] == state["run"]


def test_cookie_stop_reserves_profile_until_worker_exits_and_restart_interrupts(app, monkeypatch):
    service, profile = app
    entered, finish = threading.Event(), threading.Event()

    def browse(connection, settings, stopping, report):
        entered.set()
        assert stopping.wait(5)
        assert finish.wait(5)
        raise InterruptedError

    monkeypatch.setattr(cookie_warmup_browser, "browse", browse)
    try:
        service.call("cookie_warmup.start", {"profile_id": profile})
        assert entered.wait(5)
        with service.sessions() as session:
            assert window_work(session, profile) == (True, True)
        with pytest.raises(UserError, match="уже работает"):
            service.call("cookie_warmup.start", {"profile_id": profile})
        with pytest.raises(UserError, match="идёт прогрев"):
            service.call("capture.start_internal", {"profile_id": profile, "urls": []})
        assert service.call("cookie_warmup.stop", {})["run"]["status"] == "stopping"
        with service.sessions() as session:
            assert window_work(session, profile)[0]
    finally:
        service.cookie_warmup.stopping.set()
        finish.set()
        service.cookie_warmup.thread.join(5)
    assert service.call("cookie_warmup.state", {})["run"]["status"] == "stopped"
    with service.sessions.begin() as session:
        row = session.get(Setting, KEY)
        row.value = {**row.value, "run": {**row.value["run"], "status": "running"}}
    reopened = CookieWarmup(service.sessions, service.browser_sessions)
    assert reopened.state()["run"]["status"] == "interrupted"


def test_cookie_warmup_rejects_missing_or_busy_profiles(app):
    service, profile = app
    with pytest.raises(UserError, match="профиль NSTbrowser"):
        service.call("cookie_warmup.start", {"profile_id": "missing"})
    with service.sessions.begin() as session:
        session.add(
            Setting(
                key="instagram_warmup",
                value={
                    "run": {"profile_id": profile, "status": "running"},
                },
            )
        )
    with pytest.raises(UserError, match="Профиль занят"):
        service.call("cookie_warmup.start", {"profile_id": profile})
    # Avoid the legacy Instagram run migration in the service's shutdown.
    with service.sessions.begin() as session:
        session.delete(session.get(Setting, "instagram_warmup"))


@pytest.mark.parametrize("site", ["file:///etc/passwd", "https://a:b@example.com", "https://"])
def test_cookie_settings_reject_invalid_sites(site):
    with pytest.raises(ValidationError):
        CookieWarmupSettings(sites=[site])


def test_google_is_search_only_and_sites_are_deduplicated():
    settings = CookieWarmupSettings(
        sites=[
            "https://www.google.com/",
            " https://www.reddit.com/ ",
            "https://www.reddit.com/",
        ]
    )
    assert settings.sites == ["https://www.reddit.com/"]
