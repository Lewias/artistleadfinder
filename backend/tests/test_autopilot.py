"""«Найти и написать»: the parser run's new leads, then «Рассылка» to them.

The parser run is a database stand-in of the one the window drives; nothing is sent.
"""

from datetime import timedelta

import pytest

from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import BrowserQueue, Lead, ScoutDecision, SearchJob
from artist_lead_finder.service import ApplicationService

MESSAGE = "Yo bro your sound caught me fr, lets work"


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "autopilot.db")
    service = ApplicationService(sessions, tmp_path / "data")
    service.outreach.window_open = lambda profile_id: True
    sender = service.call("browser.create", {"name": "Sender A"})["id"]
    service.call("outreach.workspace_update", {"messages": [MESSAGE]})
    yield service, sessions, sender
    service.shutdown()
    engine.dispose()


def parser_run(sessions, sender, names, status="running") -> int:
    """A parser run of the sender's window that created leads for `names`."""
    with sessions.begin() as session:
        job = SearchJob(name="Lead Scout", status=status)
        session.add(job)
        session.flush()
        session.add(BrowserQueue(job_id=job.id, profile_id=sender, urls=[], weights={}))
        for name in names:
            session.add(
                Lead(
                    platform="instagram",
                    username=name,
                    profile_url=f"https://www.instagram.com/{name}/",
                )
            )
            session.add(
                ScoutDecision(
                    job_id=job.id,
                    username=name,
                    source_username="rapdaily",
                    decision="lead_created",
                )
            )
        return job.id


def finish(sessions, job_id, status="completed"):
    with sessions.begin() as session:
        session.get(SearchJob, job_id).status = status


def test_writes_to_the_new_leads_once_the_parser_is_done(app):
    service, sessions, sender = app
    state = service.call("autopilot.start", {"profile_id": sender, "target": 40})
    assert state["status"] == "starting" and state["target"] == 40
    # The goal counts from zero and the finding account writes.
    assert service.call("scout.accounts", {})  # the goal row exists
    workspace = service.call("outreach.workspace", {})
    assert workspace["sender_ids"] == [sender]
    with pytest.raises(UserError):
        service.call("autopilot.start", {"profile_id": sender, "target": 40})

    job = parser_run(sessions, sender, ["jay.carter", "kid.vibes"])
    service.call("outreach.next_internal", {})
    assert service.call("autopilot.state", {})["status"] == "scouting"

    finish(sessions, job)
    service.call("outreach.next_internal", {})
    state = service.call("autopilot.state", {})
    assert state["status"] == "sending" and state["found"] == 2
    assert state["total"] == 2
    workspace = service.call("outreach.workspace", {})
    assert {"jay.carter", "kid.vibes"} <= {item["username"] for item in workspace["usernames"]}
    assert workspace["running"] is True

    service.call("outreach.workspace_stop", {})
    assert service.call("autopilot.state", {})["status"] == "done"


def test_a_stopped_parser_sends_nothing(app):
    service, sessions, sender = app
    service.call("autopilot.start", {"profile_id": sender, "target": 10})
    job = parser_run(sessions, sender, ["jay.carter"])
    finish(sessions, job, "cancelled")
    state = service.call("autopilot.state", {})
    assert state["status"] == "stopped"
    assert service.call("outreach.workspace", {})["running"] is False


def test_nothing_new_found_is_reported_not_sent(app):
    service, sessions, sender = app
    service.call("autopilot.start", {"profile_id": sender, "target": 10})
    job = parser_run(sessions, sender, [])
    finish(sessions, job)
    state = service.call("autopilot.state", {})
    assert state["status"] == "failed" and "Рассылка не запустилась" in state["message"]


def test_a_parser_that_never_starts_gives_up(app):
    service, sessions, sender = app
    service.call("autopilot.start", {"profile_id": sender, "target": 10})
    later = service.autopilot.now() + timedelta(minutes=5)
    service.autopilot.now = lambda: later
    assert service.call("autopilot.state", {})["status"] == "failed"


def test_needs_messages_and_a_known_account(app):
    service, sessions, sender = app
    with pytest.raises(UserError):
        service.call("autopilot.start", {"profile_id": "f" * 32, "target": 10})
    with pytest.raises(UserError):
        service.call("autopilot.start", {"profile_id": sender, "target": 0})
    service.call("outreach.workspace_update", {"messages": []})
    with pytest.raises(UserError):
        service.call("autopilot.start", {"profile_id": sender, "target": 10})
    service.call("outreach.workspace_update", {"messages": [MESSAGE]})
    state = service.call("autopilot.start", {"profile_id": sender, "target": 10})
    assert service.call("autopilot.cancel", {})["status"] == "cancelled"
    assert state["status"] == "starting"
