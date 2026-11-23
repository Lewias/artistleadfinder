"""Activity queue through the normal mailing RPC, with deterministic clock/browser fixtures."""

import random
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from test_outreach import add_lead, campaign, sent_ok
from test_outreach import app as outreach_app

from artist_lead_finder.models import (
    FollowUpJob,
    FollowUpRun,
    InstagramActivityState,
    InstagramActivityTask,
    Message,
    OutboundMessageJob,
    OutreachSender,
)
from artist_lead_finder.outreach.activity import MIN_SLACK, ActivityEngine

app = outreach_app


class Browser:
    def __init__(self, windows):
        self.windows, self.calls = windows, []
        self.result = None
        self.closed = []

    def activity_close(self, sender):
        self.closed.append(sender)

    def is_open(self, sender):
        return sender in self.windows

    def activity_step(self, task, guard):
        assert guard()
        self.calls.append((task.id, task.kind, task.target))
        if self.result is not None:
            return self.result
        if task.phase == "open":
            return {"outcome": "pending", "phase": "inspect"}
        return {
            "outcome": "done",
            "candidates": [
                {"kind": "feed", "url": "https://www.instagram.com/p/ONE/"},
                {"kind": "like", "url": "https://www.instagram.com/p/THREE/"},
                {"kind": "story", "url": "https://www.instagram.com/stories/artist/123/"},
                {
                    "kind": "navigate",
                    "url": "https://www.instagram.com/p/TWO/",
                    "from_url": "https://www.instagram.com/artist/",
                },
            ],
        }


@pytest.fixture
def activity(app):
    service, sessions, clock, senders, windows = app
    browser = Browser(windows)
    service.activity = ActivityEngine(
        sessions, browser, service.settings, now=clock, rng=random.Random(4)
    )
    service.handlers = service._handlers()
    ids = [add_lead(sessions, "artist" + str(i)) for i in range(5)]
    cid = campaign(service, ids, senders[:1])
    service.call("outreach.campaign_start", {"id": cid})
    return service, sessions, clock, senders[0], browser, cid


def send_first(service):
    job = service.call("outreach.next_internal", {})
    assert job
    service.call(
        "outreach.commit_internal",
        {"job_id": job["job_id"], "token": job["token"], "result": sent_ok(job)},
    )
    return job


def test_dm_priority_and_all_five_actions_with_random_alternation_and_dedup(activity, caplog):
    service, sessions, clock, sender, browser, _ = activity
    caplog.set_level("INFO", logger="artist_lead_finder.outreach.activity")
    assert service.call("outreach.activity_tick_internal", {}) == {"worked": False}
    assert browser.calls == []
    first = send_first(service)
    for _ in range(30):
        service.call("outreach.activity_tick_internal", {})
        clock.advance(seconds=3)
    with sessions() as session:
        tasks = list(
            session.scalars(
                select(InstagramActivityTask)
                .where(InstagramActivityTask.status == "done")
                .order_by(InstagramActivityTask.id)
            )
        )
        keys = [
            (row.sender_id, row.kind, row.target)
            for row in session.scalars(select(InstagramActivityTask))
        ]
    assert {"story", "feed", "profile", "like", "navigate"} <= {task.kind for task in tasks}
    assert len(keys) == len(set(keys))
    assert len(keys) == len({(sender, target) for sender, _, target in keys})
    started = [
        kind
        for i, (task_id, kind, _) in enumerate(browser.calls)
        if i == 0 or task_id != browser.calls[i - 1][0]
    ]
    assert len(set(started)) >= 5
    assert all(a != b for a, b in zip(started[:8], started[1:8]))
    assert "instagram_activity_done" in caplog.text
    clock.value = clock.value - timedelta(seconds=90) + timedelta(seconds=180) - MIN_SLACK
    calls = len(browser.calls)
    assert not service.call("outreach.activity_tick_internal", {})["worked"]
    assert len(browser.calls) == calls
    clock.advance(seconds=9)
    second = service.call("outreach.next_internal", {})
    assert second and second["job_id"] != first["job_id"]
    assert not service.call("outreach.activity_tick_internal", {})["worked"]


def test_restart_keeps_pending_and_does_not_replay_claimed_tasks(activity):
    service, sessions, clock, _, browser, _ = activity
    send_first(service)
    result = service.call("outreach.activity_tick_internal", {})
    claimed_id = result["task_id"]
    with sessions() as session:
        queued_before = session.scalar(select(func.count()).select_from(InstagramActivityTask))
    restarted = ActivityEngine(sessions, browser, service.settings, now=clock, rng=random.Random(4))
    restarted.recover()
    with sessions() as session:
        assert session.get(InstagramActivityTask, claimed_id).status == "interrupted"
        assert (
            session.scalar(select(func.count()).select_from(InstagramActivityTask)) == queued_before
        )
    for _ in range(4):
        restarted.tick()
    assert sum(task_id == claimed_id for task_id, _, _ in browser.calls) == 1


@pytest.mark.parametrize("reason", ["checkpoint", "rate_limited", "login", "action_blocked"])
def test_account_errors_stop_activity_and_sender_without_a_resume_timer(activity, reason):
    service, sessions, clock, sender, browser, _ = activity
    send_first(service)
    browser.result = {"outcome": "error", "reason": reason}
    service.call("outreach.activity_tick_internal", {})
    count = len(browser.calls)
    clock.advance(days=1)
    assert not service.call("outreach.activity_tick_internal", {})["worked"]
    assert len(browser.calls) == count
    with sessions() as session:
        assert session.get(InstagramActivityState, sender).stopped
        health = session.get(OutreachSender, sender)
        assert health.status != "active" and health.until is None


def test_unsupported_action_is_skipped_without_interrupting_dm(activity):
    service, sessions, clock, _, browser, _ = activity
    send_first(service)
    browser.result = {"outcome": "skipped", "reason": "unsupported"}
    result = service.call("outreach.activity_tick_internal", {})
    with sessions() as session:
        row = session.get(InstagramActivityTask, result["task_id"])
        assert row.status == "skipped"
        disabled_kind = row.kind
    for _ in range(5):
        service.call("outreach.activity_tick_internal", {})
    assert sum(kind == disabled_kind for _, kind, _ in browser.calls) == 1
    clock.advance(minutes=4)
    assert service.call("outreach.next_internal", {})


def test_pause_guard_cancel_and_content_survive_campaign_lifecycle(activity):
    service, sessions, _, sender, browser, cid = activity
    send_first(service)
    chosen = service.call("outreach.activity_tick_internal", {})["task_id"]
    with sessions() as session:
        token = session.get(InstagramActivityTask, chosen).claim_token
    service.call("outreach.campaign_control", {"id": cid, "action": "pause"})
    assert sender in browser.closed
    assert not service.activity.guard(chosen, token)
    count = len(browser.calls)
    service.call("outreach.activity_tick_internal", {})
    assert len(browser.calls) == count
    with sessions() as session:
        assert session.get(InstagramActivityTask, chosen).status == "interrupted"
    service.call("outreach.campaign_control", {"id": cid, "action": "cancel"})
    service.call("outreach.activity_tick_internal", {})
    with sessions() as session:
        assert not session.scalar(
            select(InstagramActivityTask.id).where(
                InstagramActivityTask.status.in_(("pending", "running"))
            )
        )


def test_separate_workers_cannot_execute_each_others_claims(activity):
    service, sessions, clock, sender, browser, _ = activity
    first = service.call("outreach.next_internal", {})
    assert not service.call("outreach.activity_tick_internal", {})["worked"]
    assert browser.calls == []
    service.call(
        "outreach.commit_internal",
        {"job_id": first["job_id"], "token": first["token"], "result": sent_ok(first)},
    )
    one = service.activity._pick()
    other = ActivityEngine(sessions, browser, service.settings, now=clock)
    assert other._pick() is None
    assert service.activity._pick().id == one.id
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(InstagramActivityTask)
                .where(
                    InstagramActivityTask.status == "running",
                    InstagramActivityTask.sender_id == sender,
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboundMessageJob)
                .where(OutboundMessageJob.status == "sent")
            )
            == 1
        )


def test_start_mailing_entry_point_automatically_enables_and_stops_activity(app):
    service, sessions, clock, senders, windows = app
    browser = Browser(windows)
    service.activity = ActivityEngine(sessions, browser, service.settings, now=clock)
    service.handlers = service._handlers()
    service.call(
        "outreach.workspace_update",
        {
            "usernames": ["artistone", "artisttwo"],
            "messages": ["hello"],
            "sender_ids": senders[:1],
        },
    )
    service.call("outreach.workspace_start", {})
    send_first(service)
    assert service.call("outreach.activity_tick_internal", {})["worked"]
    service.call("outreach.workspace_stop", {})
    assert senders[0] in browser.closed
    assert not service.call("outreach.activity_tick_internal", {})["worked"]


def test_due_followup_wins_over_activity_in_the_existing_sender_gap(activity):
    service, sessions, clock, sender, browser, cid = activity
    send_first(service)
    clock.advance(minutes=4)
    with sessions.begin() as session:
        session.execute(
            update(OutboundMessageJob)
            .where(OutboundMessageJob.status == "ready")
            .values(status="pending", scheduled_at=clock() + timedelta(days=1))
        )
        message = session.scalar(select(Message))
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
                scheduled_at=clock(),
                status="pending",
            )
        )
    assert not service.call("outreach.activity_tick_internal", {})["worked"]
    assert browser.calls == []
    with sessions.begin() as session:
        session.execute(update(FollowUpJob).values(scheduled_at=clock() + timedelta(hours=1)))
    assert service.call("outreach.activity_tick_internal", {})["worked"]


def test_parser_and_closed_windows_are_never_used_for_background_activity(activity):
    service, _, _, sender, browser, _ = activity
    send_first(service)
    browser.windows.remove(sender)
    assert not service.call("outreach.activity_tick_internal", {})["worked"]
    browser.windows.add(sender)
    job = service.call(
        "scout.start_internal",
        {
            "profile_id": sender,
            "sources": ["https://www.instagram.com/rapdaily/"],
        },
    )["id"]
    assert not service.call("outreach.activity_tick_internal", {})["worked"]
    service.call("jobs.control", {"id": job, "action": "cancel"})
    assert service.call("outreach.activity_tick_internal", {})["worked"]


def test_due_dm_preempts_a_background_claim_and_closes_its_tab(activity):
    service, sessions, clock, sender, browser, _ = activity
    send_first(service)
    chosen = service.call("outreach.activity_tick_internal", {})["task_id"]
    clock.advance(minutes=4)
    assert service.call("outreach.next_internal", {})
    assert sender in browser.closed
    with sessions() as session:
        task = session.get(InstagramActivityTask, chosen)
        assert task.status == "interrupted" and task.reason == "dm_priority"


def test_late_api_refusal_on_preemption_prevents_dm_and_keeps_sender_stopped(activity):
    service, sessions, clock, sender, browser, _ = activity
    send_first(service)
    service.call("outreach.activity_tick_internal", {})
    browser.activity_close = lambda sender: "rate_limited"
    clock.advance(minutes=4)
    assert service.call("outreach.next_internal", {}) is None
    with sessions() as session:
        health = session.get(OutreachSender, sender)
        assert health.status == "rate_limited" and health.until is None
        assert not session.scalar(
            select(OutboundMessageJob.id).where(OutboundMessageJob.status == "sending")
        )


def test_multiple_sender_campaigns_both_receive_background_work(app):
    service, sessions, clock, senders, windows = app
    browser = Browser(windows)
    browser.result = {"outcome": "done"}
    service.activity = ActivityEngine(sessions, browser, service.settings, now=clock)
    service.handlers = service._handlers()
    ids = [add_lead(sessions, "artist" + str(i)) for i in range(4)]
    cid = campaign(service, ids, senders)
    service.call("outreach.campaign_start", {"id": cid})
    send_first(service)
    send_first(service)
    for _ in range(6):
        service.call("outreach.activity_tick_internal", {})
        clock.advance(seconds=3)
    with sessions() as session:
        worked = set(
            session.scalars(
                select(InstagramActivityTask.sender_id).where(
                    InstagramActivityTask.status == "done"
                )
            )
        )
    assert worked == set(senders)
