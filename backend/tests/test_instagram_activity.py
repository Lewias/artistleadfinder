"""Batch ordering and recovery through the normal mailing RPCs."""

import sqlite3
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_outreach import add_lead, campaign, sent_ok
from test_outreach import app as outreach_app

from artist_lead_finder.database import open_database
from artist_lead_finder.models import (
    FollowUpJob,
    FollowUpRun,
    InstagramActivityState,
    InstagramActivityTask,
    Message,
    OutreachSender,
)
from artist_lead_finder.outreach.activity import DM_BATCH, ActivityEngine

app = outreach_app
POSTS = [f"https://www.instagram.com/p/{name}/" for name in ("ONE", "TWO", "THREE", "FOUR")]


class Browser:
    def __init__(self, windows):
        self.windows, self.calls, self.closed = windows, [], []
        self.result = None

    def activity_close(self, sender):
        self.closed.append(sender)

    def is_open(self, sender):
        return sender in self.windows

    def activity_step(self, task, guard):
        assert guard()
        self.calls.append((task.sender_id, task.kind, task.target, task.phase, task.payload))
        if self.result is not None:
            return self.result
        if task.kind == "timeline":
            return {"outcome": "done", "posts": [POSTS[0], *POSTS]}
        return {"outcome": "done"}


def install_browser(service, sessions, clock, browser):
    service.activity = ActivityEngine(sessions, browser, now=clock)
    service.handlers = service._handlers()


@pytest.fixture
def activity(app):
    service, sessions, clock, senders, windows = app
    browser = Browser(windows)
    install_browser(service, sessions, clock, browser)
    service.call(
        "outreach.workspace_update",
        {
            "usernames": [f"artist{i}" for i in range(13)],
            "messages": ["hello"],
            "sender_ids": senders[:1],
        },
    )
    cid = service.call("outreach.workspace_start", {})["campaign"]["id"]
    return service, sessions, clock, senders[0], browser, cid


def send_one(service, clock, result=None):
    job = service.call("outreach.next_internal", {})
    assert job
    params = {
        "kind": job["kind"],
        "job_id": job["job_id"],
        "token": job["token"],
        "result": result or sent_ok(job),
    }
    service.call("outreach.commit_internal", params)
    clock.advance(minutes=3)
    return job, params


def send_batch(service, clock):
    for _ in range(DM_BATCH):
        send_one(service, clock)


def tick(service):
    return service.call("outreach.activity_tick_internal", {})


def test_workspace_sends_five_then_reels_feed_likes_and_repeats(activity, caplog):
    service, sessions, clock, sender, browser, _ = activity
    caplog.set_level("INFO", logger="artist_lead_finder.outreach.activity")
    for _ in range(4):
        _, params = send_one(service, clock)
        assert service.call("outreach.commit_internal", params)["ignored"]
        assert not tick(service)["worked"]
    send_one(service, clock, {"outcome": "error", "error": "not_found"})
    assert not tick(service)["worked"]
    send_one(service, clock)

    for phase in ("reels", "timeline", "like", "like", "like"):
        assert service.call("outreach.next_internal", {}) is None
        assert tick(service)["outcome"] == "done"
        assert browser.calls[-1][1] == phase
    assert [row[2] for row in browser.calls if row[1] == "like"] == POSTS[:3]
    assert "instagram_activity_break_started" in caplog.text
    assert "instagram_activity_break_done" in caplog.text
    assert not tick(service)["worked"]
    send_batch(service, clock)
    assert service.call("outreach.next_internal", {}) is None
    assert tick(service)["worked"] and browser.calls[-1][1] == "reels"
    assert tick(service)["worked"] and browser.calls[-1][1] == "timeline"
    assert tick(service)["worked"] and browser.calls[-1][2] == POSTS[3]
    with sessions() as session:
        assert session.get(InstagramActivityState, sender).sent_in_batch == 0
    assert service.call("outreach.next_internal", {})


def test_restart_keeps_the_dm_count_and_resumes_remaining_reels_time(activity):
    service, sessions, clock, sender, browser, _ = activity
    for _ in range(4):
        send_one(service, clock)
    install_browser(service, sessions, clock, browser)
    service.activity.recover()
    send_one(service, clock)
    browser.result = {"outcome": "pending", "phase": "watch", "progress": 120.0}
    claimed = tick(service)["task_id"]

    install_browser(service, sessions, clock, browser)
    service.activity.recover()
    clock.advance(hours=1)
    assert service.call("outreach.next_internal", {}) is None
    assert tick(service)["task_id"] == claimed
    assert browser.calls[-1][3:] == ("open", {"progress": 120.0})
    with sessions() as session:
        assert session.get(InstagramActivityState, sender).sent_in_batch == 5


def test_pause_keeps_progress_and_cancel_stops_the_break(activity):
    service, sessions, clock, sender, browser, cid = activity
    send_batch(service, clock)
    browser.result = {"outcome": "pending", "phase": "watch", "progress": 40.0}
    task_id = tick(service)["task_id"]
    service.call("outreach.campaign_control", {"id": cid, "action": "pause"})
    assert sender in browser.closed
    assert not tick(service)["worked"]
    with sessions() as session:
        task = session.get(InstagramActivityTask, task_id)
        assert (task.status, task.phase, task.payload["progress"]) == ("pending", "open", 40.0)
    service.call("outreach.campaign_control", {"id": cid, "action": "resume"})
    assert tick(service)["task_id"] == task_id
    service.call("outreach.workspace_stop", {})
    assert not tick(service)["worked"]
    with sessions() as session:
        assert session.get(InstagramActivityTask, task_id).status == "interrupted"
        assert session.get(InstagramActivityState, sender).stage == "send"


def test_tray_pause_suspends_browsing_and_retains_the_senders_break(activity):
    service, sessions, clock, sender, browser, _ = activity
    send_batch(service, clock)
    browser.result = {"outcome": "pending", "phase": "watch", "progress": 40.0}
    task_id = tick(service)["task_id"]
    service.call("outreach.activity_suspend_internal", {})
    assert sender in browser.closed
    with sessions() as session:
        assert session.get(InstagramActivityTask, task_id).status == "pending"
    assert service.call("outreach.next_internal", {}) is None
    assert tick(service)["task_id"] == task_id


@pytest.mark.parametrize("reason", ["checkpoint", "rate_limited", "login", "action_blocked"])
def test_platform_refusal_stops_the_sender_without_a_resume_timer(activity, reason):
    service, sessions, clock, sender, browser, _ = activity
    send_batch(service, clock)
    browser.result = {"outcome": "error", "reason": reason}
    assert tick(service)["outcome"] == "error"
    clock.advance(days=1)
    assert not tick(service)["worked"]
    assert service.call("outreach.next_internal", {}) is None
    with sessions() as session:
        health = session.get(OutreachSender, sender)
        assert health.status != "active" and health.until is None
        assert session.get(InstagramActivityState, sender).stopped


def test_unavailable_browsing_stages_skip_and_release_the_next_dm(activity):
    service, _, clock, _, browser, _ = activity
    send_batch(service, clock)
    browser.result = {"outcome": "skipped", "reason": "unsupported"}
    assert tick(service)["outcome"] == "skipped"
    assert service.call("outreach.next_internal", {}) is None
    assert tick(service)["outcome"] == "skipped"
    assert [call[1] for call in browser.calls] == ["reels", "timeline"]
    assert service.call("outreach.next_internal", {})


def test_a_like_interrupted_by_restart_is_not_replayed(activity):
    service, sessions, clock, _, browser, _ = activity
    send_batch(service, clock)
    tick(service)
    tick(service)
    browser.result = {"outcome": "pending", "phase": "confirm"}
    interrupted = tick(service)["task_id"]
    install_browser(service, sessions, clock, browser)
    service.activity.recover()
    browser.result = None
    assert tick(service)["task_id"] != interrupted
    with sessions() as session:
        assert session.get(InstagramActivityTask, interrupted).status == "interrupted"
    assert [call[2] for call in browser.calls if call[1] == "like"] == POSTS[:2]


def test_another_activity_worker_cannot_advance_a_claim(activity):
    service, sessions, clock, _, browser, _ = activity
    send_batch(service, clock)
    first = service.activity._pick()
    assert first
    other = ActivityEngine(sessions, browser, now=clock)
    assert not other.tick()["worked"]
    assert service.activity._pick().id == first.id


def test_due_followup_waits_for_the_same_break(activity):
    service, sessions, clock, sender, browser, cid = activity
    send_batch(service, clock)
    with sessions.begin() as session:
        message = session.scalar(select(Message).order_by(Message.id).limit(1))
        run = FollowUpRun(
            conversation_id=message.conversation_id,
            origin_message_id=message.id,
            sender_account_id=sender,
            campaign_id=cid,
            steps=[{"text": "hello", "delay_days": 1}],
            status="active",
            last_sent_at=message.sent_at,
        )
        session.add(run)
        session.flush()
        session.add(
            FollowUpJob(
                run_id=run.id,
                conversation_id=message.conversation_id,
                step_index=0,
                idempotency_key=f"followup:{run.id}:0",
                sender_account_id=sender,
                body="hello",
                scheduled_at=clock() - timedelta(minutes=1),
                status="pending",
            )
        )
    assert service.call("outreach.next_internal", {}) is None
    browser.result = {"outcome": "skipped", "reason": "unavailable"}
    tick(service)
    tick(service)
    job, _ = send_one(service, clock)
    assert job["kind"] == "followup"
    with sessions() as session:
        assert session.get(InstagramActivityState, sender).sent_in_batch == 1


def test_other_sender_can_send_during_the_break(app):
    service, sessions, clock, senders, windows = app
    browser = Browser(windows)
    install_browser(service, sessions, clock, browser)
    for sender, label in zip(senders, ("a", "b"), strict=True):
        ids = [add_lead(sessions, f"artist{label}{i}") for i in range(8)]
        cid = campaign(service, ids, [sender])
        service.call("outreach.campaign_start", {"id": cid})
    send_batch(service, clock)
    assert tick(service)["worked"] and browser.calls[-1][0] == senders[0]
    job, _ = send_one(service, clock)
    assert job["profile_id"] == senders[1]


def test_closed_window_keeps_the_break_pending(activity):
    service, _, clock, sender, browser, _ = activity
    send_batch(service, clock)
    browser.windows.remove(sender)
    assert not tick(service)["worked"]
    browser.windows.add(sender)
    assert service.call("outreach.next_internal", {}) is None
    assert tick(service)["worked"] and browser.calls[-1][1] == "reels"


def test_upgrade_cancels_old_background_work_and_keeps_history(activity, tmp_path):
    _, sessions, _, sender, _, cid = activity
    path = tmp_path / "schema31.sqlite3"
    engine = sessions.kw["bind"]
    with sqlite3.connect(engine.url.database) as original, sqlite3.connect(path) as copy:
        original.backup(copy)
        copy.execute("ALTER TABLE instagram_activity_state DROP COLUMN sent_in_batch")
        copy.execute("ALTER TABLE instagram_activity_state DROP COLUMN stage")
        copy.execute("DELETE FROM schema_migrations WHERE version=32")
        copy.execute(
            """INSERT INTO instagram_activity_state
                (sender_id,campaign_id,disabled,stopped,discovery_number) VALUES (?,?,'[]',0,1)""",
            (sender, cid),
        )
        copy.executemany(
            """INSERT INTO instagram_activity_tasks
            (campaign_id,sender_id,kind,target,payload,status,phase,created_at)
            VALUES (?,?,'like',?,'{}',?,'open',CURRENT_TIMESTAMP)""",
            [(cid, sender, POSTS[0], "pending"), (cid, sender, POSTS[1], "done")],
        )
    upgraded, upgraded_sessions = open_database(path)
    try:
        with upgraded_sessions() as session:
            row = session.get(InstagramActivityState, sender)
            assert (row.stage, row.sent_in_batch, row.batch_number) == ("send", 0, 1)
            assert {task.status for task in session.scalars(select(InstagramActivityTask))} == {
                "cancelled",
                "done",
            }
    finally:
        upgraded.dispose()
