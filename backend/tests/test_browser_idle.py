"""The account's browser window closes by itself once the app has no work left for it."""

from types import SimpleNamespace

import pytest

from artist_lead_finder.browser_idle import IDLE_GRACE, IdleCloser
from artist_lead_finder.database import open_database
from artist_lead_finder.models import InboxScan, SearchJob, Setting
from artist_lead_finder.service import ApplicationService

ACCOUNT = "a" * 32
OTHER = "b" * 32


class Windows:
    """Stands in for ChromiumRuntime: open windows and close()."""

    def __init__(self, *identifiers):
        self.windows = {i: SimpleNamespace(worked=False, idle_since=None) for i in identifiers}
        self.closed = []

    def close(self, identifier):
        self.windows.pop(identifier, None)
        self.closed.append(identifier)


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "idle.db")
    service = ApplicationService(sessions, tmp_path)
    service.scout.ai = None
    yield service, sessions
    service.shutdown()
    engine.dispose()


def start_run(service):
    service.call("settings.save", {"profiles_per_hour": 0})
    service.call("scout.source_add", {"values": ["https://www.instagram.com/rapdaily/"]})
    return service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]


def test_the_window_closes_after_the_parser_run_and_a_grace(app):
    service, sessions = app
    windows, clock = Windows(ACCOUNT, OTHER), Clock()
    closer = IdleCloser(sessions, windows, lambda: True, now=clock)
    job = start_run(service)
    assert closer.tick() == {"closed": []}
    assert windows.windows[ACCOUNT].worked and not windows.windows[OTHER].worked
    # Paused (it may need a login): the window stays.
    service.call("jobs.control", {"id": job, "action": "pause"})
    clock.value += IDLE_GRACE * 3
    assert closer.tick() == {"closed": []}
    service.call("jobs.control", {"id": job, "action": "cancel"})
    assert closer.tick() == {"closed": []}  # the grace starts
    clock.value += IDLE_GRACE - 1
    assert closer.tick() == {"closed": []}
    clock.value += 2
    # The parser's window closes; the one opened by hand and never worked in stays.
    assert closer.tick() == {"closed": [ACCOUNT]}
    assert list(windows.windows) == [OTHER]


def test_other_work_of_the_account_keeps_the_window(app):
    service, sessions = app
    windows, clock = Windows(ACCOUNT), Clock()
    closer = IdleCloser(sessions, windows, lambda: True, now=clock)
    windows.windows[ACCOUNT].worked = True
    # Reading Direct in this window.
    with sessions.begin() as session:
        session.add(InboxScan(sender_account_id=ACCOUNT, status="running"))
    closer.tick()
    clock.value += IDLE_GRACE * 2
    assert closer.tick() == {"closed": []}
    with sessions.begin() as session:
        for scan in session.query(InboxScan):
            scan.status = "done"
        # «Найти и написать» still holds the account between its parser and its outreach.
        session.add(
            Setting(key="autopilot", value={"status": "scouting", "profile_ids": [ACCOUNT]})
        )
    closer.tick()
    clock.value += IDLE_GRACE * 2
    assert closer.tick() == {"closed": []}
    with sessions.begin() as session:
        session.get(Setting, "autopilot").value = {"status": "done", "profile_ids": [ACCOUNT]}
    closer.tick()
    clock.value += IDLE_GRACE + 1
    assert closer.tick() == {"closed": [ACCOUNT]}


def test_nothing_closes_when_turned_off_or_for_a_one_off_capture(app):
    service, sessions = app
    windows, clock = Windows(ACCOUNT), Clock()
    on = {"value": False}
    closer = IdleCloser(sessions, windows, lambda: on["value"], now=clock)
    job = start_run(service)
    closer.tick()
    service.call("jobs.control", {"id": job, "action": "cancel"})
    closer.tick()
    clock.value += IDLE_GRACE * 2
    assert closer.tick() == {"closed": []}
    # A capture of the page open in the window is not the app's work in it.
    other = Windows(OTHER)
    capture = IdleCloser(sessions, other, lambda: True, now=clock)
    created = service.call(
        "capture.start_internal",
        {"profile_id": OTHER, "urls": ["https://www.instagram.com/x/"], "name": "Открытый артист"},
    )
    capture.tick()
    with sessions.begin() as session:
        session.get(SearchJob, created["id"]).status = "completed"
    capture.tick()
    clock.value += IDLE_GRACE * 2
    assert capture.tick() == {"closed": []}


def test_the_setting_is_on_by_default(app):
    service, _ = app
    assert service.call("settings.get", {})["scout_close_browser"] is True
    assert service.call("browser.runtime.close_idle", {}) == {"closed": []}
