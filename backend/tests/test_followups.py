"""Follow-up chains through the outreach queue: start, pace, stop, review, recovery,
replies and the migration of follow-ups planned by earlier versions.

The browser is a stand-in: each job the queue hands out gets the result a test gives it.
Nothing reaches Instagram.
"""

import json
import random
import sqlite3
from datetime import timedelta

import pytest
import test_outreach
from sqlalchemy import func, select
from test_outreach import add_lead, campaign, recipients

from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import (
    Conversation,
    FollowUpJob,
    FollowUpRun,
    FollowUpSequence,
    Lead,
    Message,
    OutboundMessageJob,
    OutreachCampaign,
    OutreachSender,
    OutreachTemplate,
    utcnow,
)
from artist_lead_finder.outreach import reasons
from artist_lead_finder.outreach.campaigns import VariantPicker
from artist_lead_finder.outreach.eligibility import can_send_followup, can_send_initial_outreach
from artist_lead_finder.outreach.followups import new_replies
from artist_lead_finder.outreach.senders import as_utc
from artist_lead_finder.service import ApplicationService

INITIAL = "Hey {{firstName}}, checked your music out"
STEPS = [(2, "Bump {{firstName}}"), (3, "Last one from me")]


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "followups.db")
    service = ApplicationService(sessions, tmp_path / "data")
    clock = test_outreach.Clock()
    windows: set[str] = set()
    service.outreach.now = service.campaigns.now = service.inbox.now = clock
    service.outreach.window_open = service.inbox.window_open = lambda profile: profile in windows
    service.inbox.jitter = lambda low, high: low
    senders = [service.call("browser.create", {"name": name})["id"] for name in ("A", "B")]
    windows.update(senders)
    yield service, sessions, clock, senders, windows
    service.shutdown()
    engine.dispose()


def SENT(job):
    return {"outcome": "sent", "thread_id": "340282366", "confirmed": "interface"}


def error(kind, **extra):
    return lambda job: {"outcome": "error", "error": kind, **extra}


def take(service):
    return service.call("outreach.next_internal", {})


def commit(service, job, result):
    return service.call(
        "outreach.commit_internal",
        {"job_id": job["job_id"], "token": job["token"], "kind": job["kind"], "result": result},
    )


def send(service, respond=SENT):
    job = take(service)
    assert job is not None, "no job was handed out"
    commit(service, job, respond(job))
    return job


def sequence(service, steps=STEPS, name="Bumps"):
    return service.call(
        "outreach.sequence_save",
        {"name": name, "steps": [{"delay_days": d, "text": t} for d, t in steps]},
    )


def start_chain(service, sessions, clock, senders, username="artist", steps=STEPS, sender=0):
    """A lead, a campaign with a sequence and its confirmed initial message."""
    lead = add_lead(sessions, username, "Jay Carter")
    chain = sequence(service, steps)
    template = test_outreach.template(service, INITIAL)
    campaign_id = campaign(
        service,
        [lead],
        [senders[sender]],
        template_id=template,
        followup_sequence_id=chain["id"],
    )
    service.call("outreach.campaign_start", {"id": campaign_id})
    job = send(service)
    assert job["kind"] == "initial"
    clock.advance(minutes=5)
    return lead, chain, campaign_id


def the_run(sessions, lead=None) -> FollowUpRun:
    with sessions() as session:
        query = select(FollowUpRun).order_by(FollowUpRun.id.desc())
        if lead is not None:
            query = query.join(Conversation, Conversation.id == FollowUpRun.conversation_id).where(
                Conversation.lead_id == lead
            )
        return session.scalar(query)


def steps_of(sessions, run_id) -> list[FollowUpJob]:
    with sessions() as session:
        return list(
            session.scalars(
                select(FollowUpJob).where(FollowUpJob.run_id == run_id).order_by(FollowUpJob.id)
            )
        )


# ---------- the chain ----------


def test_a_chain_goes_step_by_step_from_confirmed_sends(app):
    service, sessions, clock, senders, _ = app
    lead, chain, campaign_id = start_chain(service, sessions, clock, senders)
    run = the_run(sessions)
    assert run.status == "active" and run.sender_account_id == senders[0]
    assert run.steps == [
        {"delay_days": 2, "text": "Bump Jay"},
        {"delay_days": 3, "text": "Last one from me"},
    ]
    with sessions() as session:
        initial = session.scalar(select(Message))
    [first] = steps_of(sessions, run.id)
    assert first.idempotency_key == f"followup:{run.id}:0"
    assert as_utc(first.scheduled_at) == as_utc(initial.sent_at) + timedelta(days=2)

    clock.advance(days=1, hours=23)
    assert take(service) is None
    clock.advance(hours=2)
    job = take(service)
    assert job["kind"] == "followup" and job["profile_id"] == senders[0]
    assert job["args"]["mode"] == "followup" and job["args"]["text"] == "Bump Jay"
    assert job["args"]["previous"] == ["Hey Jay, checked your music out"]
    assert job["args"]["url"] == "https://www.instagram.com/direct/t/340282366/"
    sent_at = clock()
    commit(service, job, SENT(job))

    # The next step counts from this step's real send, not from the plan.
    second = steps_of(sessions, run.id)[-1]
    assert as_utc(second.scheduled_at) == sent_at + timedelta(days=3)
    clock.advance(days=3)
    job = take(service)
    assert job["args"]["previous"] == ["Hey Jay, checked your music out", "Bump Jay"]
    commit(service, job, SENT(job))
    assert the_run(sessions).status == "completed"
    with sessions() as session:
        assert [m.type for m in session.scalars(select(Message).order_by(Message.id))] == [
            "initial",
            "followup",
            "followup",
        ]
    clock.advance(days=30)
    assert take(service) is None
    listed = service.call("outreach.followups", {"group": "finished"})["items"]
    assert listed[0]["status"] == "completed" and listed[0]["step"] == 2


def test_steps_due_after_downtime_are_not_sent_back_to_back(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders, steps=[(1, "A"), (1, "B"), (1, "C")])
    clock.advance(days=30)  # the app was closed for a month
    assert send(service)["args"]["text"] == "A"
    clock.advance(minutes=10)
    assert take(service) is None
    clock.advance(days=1)
    assert send(service)["args"]["text"] == "B"


def test_a_followup_waits_for_its_own_sender(app):
    service, sessions, clock, senders, windows = app
    start_chain(service, sessions, clock, senders)
    windows.discard(senders[0])
    clock.advance(days=3)
    assert take(service) is None  # never moved to the other open account
    with sessions.begin() as session:
        session.get(OutreachSender, senders[0]).status = "checkpoint"
    windows.add(senders[0])
    assert take(service) is None
    service.call("outreach.sender_status", {"id": senders[0], "status": "active"})
    assert take(service)["profile_id"] == senders[0]


def test_initial_messages_and_followups_share_the_pace_and_limits(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders)
    service.call("settings.save", {"outreach_daily_limit_per_sender": 2})
    others = [add_lead(sessions, f"new{i}", "New") for i in range(3)]
    clock.advance(days=2)
    service.call("outreach.campaign_start", {"id": campaign(service, others, senders[:1])})
    # The due follow-up goes first, then the pause between messages holds the rest.
    assert send(service)["kind"] == "followup"
    assert take(service) is None
    clock.advance(minutes=4)
    assert send(service)["kind"] == "initial"
    # Two messages in 24 hours (the follow-up and an initial one): the limit is reached.
    clock.advance(hours=1)
    assert take(service) is None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 3


# ---------- stops ----------


def test_a_reply_seen_by_the_browser_stops_the_chain(app):
    service, sessions, clock, senders, _ = app
    lead, _, campaign_id = start_chain(service, sessions, clock, senders)
    clock.advance(days=2)
    send(service, error("reply_detected", replies=["yo send it"]))
    run = the_run(sessions)
    assert run.status == "stopped" and run.stop_reason == reasons.REPLY_RECEIVED
    assert [job.status for job in steps_of(sessions, run.id)] == ["cancelled"]
    with sessions() as session:
        conversation = session.scalar(select(Conversation))
        assert conversation.status == "replied"
        reply = session.scalar(select(Message).where(Message.direction == "inbound"))
        assert reply.body == "yo send it"
    assert service.call("outreach.campaign", {"id": campaign_id})["replied_count"] == 1
    clock.advance(days=10)
    assert take(service) is None


def test_do_not_contact_stop_and_pause(app):
    service, sessions, clock, senders, _ = app
    dnc, _, _ = start_chain(service, sessions, clock, senders, "dnc")
    stopped, _, _ = start_chain(service, sessions, clock, senders, "stopped")
    paused, _, _ = start_chain(service, sessions, clock, senders, "paused")
    service.call("leads.do_not_contact", {"id": dnc, "value": True})
    service.call("outreach.stop_conversation", {"lead_id": stopped})
    assert the_run(sessions, dnc).stop_reason == reasons.DO_NOT_CONTACT
    assert the_run(sessions, stopped).stop_reason == reasons.CONVERSATION_STOPPED
    run = the_run(sessions, paused)
    service.call("outreach.followup_control", {"run_id": run.id, "action": "pause"})
    clock.advance(days=5)
    assert take(service) is None
    service.call("outreach.followup_control", {"run_id": run.id, "action": "resume"})
    [step] = steps_of(sessions, run.id)
    assert as_utc(step.scheduled_at) == clock()  # due long ago: now, not in a burst
    job = take(service)
    assert job["kind"] == "followup" and job["args"]["username"] == "paused"
    commit(service, job, SENT(job))
    service.call("outreach.followup_control", {"run_id": run.id, "action": "stop"})
    run = the_run(sessions, paused)
    assert run.status == "stopped" and run.stop_reason == reasons.USER_STOPPED
    assert steps_of(sessions, run.id)[-1].status == "cancelled"
    with pytest.raises(UserError):
        service.call("outreach.followup_control", {"run_id": run.id, "action": "resume"})


def test_dnc_set_while_due_is_checked_when_claimed(app):
    service, sessions, clock, senders, _ = app
    lead, _, _ = start_chain(service, sessions, clock, senders)
    with sessions.begin() as session:
        session.get(Lead, lead).do_not_contact = True  # not through the service
    clock.advance(days=3)
    assert take(service) is None
    assert the_run(sessions).stop_reason == reasons.DO_NOT_CONTACT


def test_a_step_in_the_browser_keeps_its_real_result_after_a_reply(app):
    service, sessions, clock, senders, _ = app
    lead, _, _ = start_chain(service, sessions, clock, senders)
    clock.advance(days=2)
    job = take(service)
    assert service.outreach.guard("followup", job["job_id"], job["token"]) is None
    service.call("outreach.mark_replied", {"lead_id": lead, "body": "yo"})
    # Right before Enter the browser is told to stop...
    assert service.outreach.guard("followup", job["job_id"], job["token"]) is not None
    # ...but a step already sent is recorded as sent, and nothing more is planned.
    commit(service, job, SENT(job))
    run = the_run(sessions)
    assert run.status == "stopped"
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Message).where(Message.type == "followup")
            )
            == 1
        )
    assert [step.status for step in steps_of(sessions, run.id)] == ["sent"]


# ---------- unconfirmed, review, recovery ----------


def test_an_unconfirmed_step_holds_the_chain_until_resolved(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders)
    clock.advance(days=2)
    attempted = clock()
    job = send(service, error("unconfirmed", evidence="no_message"))
    run = the_run(sessions)
    assert run.status == "review"
    [step] = steps_of(sessions, run.id)
    assert step.status == "unconfirmed" and "нового сообщения в чате нет" in step.last_error
    clock.advance(days=10)
    assert take(service) is None  # never resent, the chain does not move
    listed = service.call("outreach.followups", {})["items"][0]
    assert listed["status"] == "review" and listed["review_job_id"] == job["job_id"]
    service.call("outreach.followup_resolve", {"job_id": job["job_id"], "sent": True})
    with sessions() as session:
        message = session.scalar(select(Message).where(Message.type == "followup"))
        assert as_utc(message.sent_at) == attempted
    run = the_run(sessions)
    assert run.status == "active"
    assert as_utc(steps_of(sessions, run.id)[-1].scheduled_at) == attempted + timedelta(days=3)


def test_an_unsent_step_is_retried_only_by_the_user(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders)
    clock.advance(days=2)
    job = send(service, error("unconfirmed"))
    with pytest.raises(UserError):
        service.call("outreach.followup_resolve", {"job_id": job["job_id"] + 99, "sent": True})
    service.call(
        "outreach.followup_resolve", {"job_id": job["job_id"], "sent": False, "retry": True}
    )
    again = take(service)
    assert again["job_id"] == job["job_id"] and again["token"] != job["token"]
    commit(service, again, SENT(again))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 2
    # Or the user ends the chain there.
    clock.advance(minutes=5)
    other, _, _ = start_chain(service, sessions, clock, senders, "other")
    clock.advance(days=2)
    job = send(service, error("unconfirmed"))
    service.call("outreach.followup_resolve", {"job_id": job["job_id"], "sent": False})
    assert the_run(sessions, other).status == "stopped"


def test_restart_and_lost_results_never_resend(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders)
    clock.advance(days=2)
    take(service)
    assert service.outreach.recover() == 1
    assert the_run(sessions).status == "review"
    # A claim without a result for too long is settled the same way.
    other, _, _ = start_chain(service, sessions, clock, senders, "other")
    clock.advance(days=2)
    job = take(service)
    clock.advance(minutes=11)
    take(service)
    assert the_run(sessions, other).status == "review"
    # The message was recorded but not the job: repaired, not resent.
    third, _, _ = start_chain(service, sessions, clock, senders, "third")
    clock.advance(days=2)
    job = take(service)
    with sessions.begin() as session:
        step = session.get(FollowUpJob, job["job_id"])
        conversation = session.get(Conversation, step.conversation_id)
        session.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                type="followup",
                body=step.body,
                sender_account_id=step.sender_account_id,
                sent_at=clock(),
                idempotency_key=step.idempotency_key,
            )
        )
    service.outreach.recover()
    run = the_run(sessions, third)
    assert run.status == "active" and run.next_step == 1


def test_repeated_and_stale_results_are_ignored(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders)
    clock.advance(days=2)
    job = take(service)
    assert commit(service, job, SENT(job))["outcome"] == "sent"
    assert commit(service, job, SENT(job))["ignored"]
    clock.advance(days=3)
    job = take(service)
    assert commit(service, {**job, "token": "stale"}, SENT(job))["ignored"]
    with sessions() as session:
        assert session.get(FollowUpJob, job["job_id"]).status == "sending"
        assert session.scalar(select(func.count()).select_from(Message)) == 2


def test_no_chain_without_a_confirmed_initial_message(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "Jay Carter")
    chain = sequence(service)
    campaign_id = campaign(service, [lead], senders[:1], followup_sequence_id=chain["id"])
    service.call("outreach.campaign_start", {"id": campaign_id})
    attempted = clock()
    send(service, error("unconfirmed"))
    assert the_run(sessions) is None
    recipient = recipients(sessions, campaign_id)["artist"]
    assert recipient.needs_review
    clock.advance(hours=6)
    service.call("outreach.resolve_review", {"recipient_id": recipient.id, "sent": True})
    run = the_run(sessions)
    assert run.status == "active" and as_utc(run.last_sent_at) == attempted
    assert as_utc(steps_of(sessions, run.id)[0].scheduled_at) == attempted + timedelta(days=2)


def test_an_account_problem_after_enter_pauses_the_sender_and_keeps_the_doubt(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders)
    clock.advance(days=2)
    job = send(service, error("unconfirmed", account="action_blocked"))
    with sessions() as session:
        sender = session.get(OutreachSender, senders[0])
        assert sender.status == "rate_limited" and sender.until is not None
        step = session.get(FollowUpJob, job["job_id"])
        assert step.status == "unconfirmed" and step.sender_account_id == senders[0]
    # The same for an initial message: the account pauses, the message waits for review.
    lead = add_lead(sessions, "second", "Second")
    service.call("outreach.sender_status", {"id": senders[1], "status": "active"})
    campaign_id = campaign(service, [lead], senders[1:])
    service.call("outreach.campaign_start", {"id": campaign_id})
    send(service, error("unconfirmed", account="rate_limited"))
    assert recipients(sessions, campaign_id)["second"].needs_review
    with sessions() as session:
        assert session.get(OutreachSender, senders[1]).status == "rate_limited"


def test_a_stop_before_enter_uses_no_attempt(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "Jay")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    assert commit(service, take(service), error("stopped", reason="X")(None))["outcome"] == "held"
    with sessions() as session:
        job = session.scalar(select(OutboundMessageJob))
        assert job.status == "ready" and job.attempt_count == 0
    assert take(service)["job_id"] == job.id


def test_the_guard_before_enter(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "Jay")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    job = take(service)
    guard = service.outreach.guard
    assert guard("initial", job["job_id"], job["token"]) is None
    assert guard("initial", job["job_id"], "other") == "CLAIM_LOST"
    service.call("leads.do_not_contact", {"id": lead, "value": True})
    assert guard("initial", job["job_id"], job["token"]) == reasons.DO_NOT_CONTACT
    service.call("outreach.campaign_control", {"id": campaign_id, "action": "cancel"})
    assert guard("initial", job["job_id"], job["token"]) == reasons.CAMPAIGN_CANCELLED


# ---------- history rules ----------


def test_initial_outreach_and_followups_have_their_own_history_rules(app):
    service, sessions, clock, senders, _ = app
    lead, _, _ = start_chain(service, sessions, clock, senders)
    clock.advance(days=2)
    with sessions() as session:
        # A new initial message to this lead is refused: they were written to.
        check = can_send_initial_outreach(session, lead, 999)
        assert not check.allowed and check.reason == reasons.ALREADY_CONTACTED
        # The chain's next step is allowed into the very same thread.
        step = session.scalar(select(FollowUpJob))
        assert can_send_followup(session, step).allowed
    # Even with «skip previously contacted» off, a new campaign does not touch the chain
    # and its initial message goes only to the profile (the browser refuses an existing
    # thread for an initial message).
    service.call("settings.save", {"outreach_skip_previously_contacted": False})
    again = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": again})
    jobs = [take(service)]
    commit(service, jobs[0], SENT(jobs[0]))
    clock.advance(minutes=5)
    jobs.append(take(service))
    kinds = {job["kind"]: job for job in jobs}
    assert set(kinds) == {"initial", "followup"}
    assert kinds["initial"]["args"]["mode"] == "initial" and "url" not in kinds["initial"]["args"]
    assert kinds["followup"]["args"]["mode"] == "followup"


def test_a_followup_needs_the_confirmed_previous_message(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders)
    with sessions.begin() as session:
        run = session.scalar(select(FollowUpRun))
        run.next_step = 1  # pretend step 1 is next though step 0 never went out
        step = session.scalar(select(FollowUpJob))
        step.step_index = 1
        check = can_send_followup(session, step)
        assert not check.allowed and check.final and check.reason == reasons.ORIGIN_MISSING
        session.rollback()


# ---------- sequences ----------


def test_sequence_rules_preview_and_versions(app):
    service, sessions, clock, senders, _ = app
    for steps in ([], [(1, "x")] * 6, [(0, "x")], [(61, "x")], [(1, "Hi {{nope}}")], [(1, " ")]):
        with pytest.raises(UserError):
            sequence(service, steps)
    preview = service.call(
        "outreach.sequence_preview",
        {"steps": [{"delay_days": 2, "text": "Yo {{firstName}}"}, {"delay_days": 3, "text": "?"}]},
    )
    assert [(s["day"], s["text"]) for s in preview["steps"]] == [(2, "Yo Jay"), (5, "?")]
    lead, chain, _ = start_chain(service, sessions, clock, senders)
    listed = service.call("outreach.sequences", {})
    assert listed[0]["open_runs"] == 1
    edited = service.call(
        "outreach.sequence_save",
        {"id": chain["id"], "name": "Bumps", "steps": [{"delay_days": 2, "text": "New text"}]},
    )
    assert edited["version"] == 2
    # The started chain keeps the texts it started with.
    assert the_run(sessions).steps[0]["text"] == "Bump Jay"
    # Disabled sequences are hidden and cannot be chosen.
    service.call("outreach.sequence_save", {"id": chain["id"], "name": "Bumps", "enabled": False})
    assert service.call("outreach.sequences", {}) == []
    with pytest.raises(UserError):
        service.call("outreach.workspace_update", {"followup_sequence_id": chain["id"]})


def test_the_list_starts_with_the_chosen_sequence(app):
    service, sessions, clock, senders, _ = app
    chain = sequence(service)
    state = service.call(
        "outreach.workspace_update",
        {
            "usernames": ["listed"],
            "messages": ["Yo {{firstName}}"],
            "sender_ids": senders[:1],
            "followup_sequence_id": chain["id"],
        },
    )
    assert state["followup_sequence_id"] == chain["id"]
    state = service.call("outreach.workspace_start", {})
    assert state["campaign"]["followup_sequence_id"] == chain["id"]
    send(service)
    assert the_run(sessions).sequence_name == "Bumps"
    service.call("outreach.workspace_update", {"followup_sequence_id": None})
    assert service.call("outreach.workspace", {})["followup_sequence_id"] is None


# ---------- replies seen by the inbox ----------


def test_threads_waiting_for_a_step_are_read_for_replies(app):
    service, sessions, clock, senders, _ = app
    lead, _, _ = start_chain(service, sessions, clock, senders)
    watch = service.call("inbox.next_internal", {})
    assert watch["kind"] == "watch" and watch["profile_id"] == senders[0]
    assert watch["url"] == "https://www.instagram.com/direct/t/340282366/"
    ours = "Hey Jay, checked your music out"
    assert watch["outbound"] == [ours]
    quiet = [{"text": ours, "side": "out"}, {"text": "Seen", "side": "out"}]
    service.call(
        "inbox.commit_internal",
        {
            "item_id": watch["item_id"],
            "kind": "watch",
            "result": {"outcome": "read", "messages": quiet},
        },
    )
    assert the_run(sessions).status == "active"
    clock.advance(minutes=1)
    assert service.call("inbox.next_internal", {}) is None  # read recently
    clock.advance(hours=7)
    watch = service.call("inbox.next_internal", {})
    answer = [{"text": ours, "side": "out"}, {"text": "yo who dis", "side": "in"}]
    service.call(
        "inbox.commit_internal",
        {
            "item_id": watch["item_id"],
            "kind": "watch",
            "result": {"outcome": "read", "messages": answer},
        },
    )
    run = the_run(sessions)
    assert run.status == "stopped" and run.stop_reason == reasons.REPLY_RECEIVED
    with sessions() as session:
        assert session.scalar(select(Conversation)).status == "replied"
    clock.advance(days=5)
    assert take(service) is None


def test_a_watch_read_that_meets_a_checkpoint_stops_the_account(app):
    service, sessions, clock, senders, _ = app
    start_chain(service, sessions, clock, senders)
    watch = service.call("inbox.next_internal", {})
    service.call(
        "inbox.commit_internal",
        {
            "item_id": watch["item_id"],
            "kind": "watch",
            "result": {"outcome": "error", "error": "code_required"},
        },
    )
    with sessions() as session:
        assert session.get(OutreachSender, senders[0]).status == "checkpoint"


def test_what_counts_as_a_new_reply():
    ours = ["Hey Jay", "Bump Jay"]
    thread = [
        {"text": "old hello", "side": "in"},
        {"text": "Hey Jay", "side": "out"},
        {"text": "Bump Jay", "side": "out"},
    ]
    assert new_replies(thread, ours, []) == []
    assert new_replies([*thread, {"text": "ok", "side": "in"}], ours, ["ok"]) == []
    assert new_replies([*thread, {"text": "Today", "side": "middle"}], ours, []) == []
    assert new_replies([*thread, {"text": "yo", "side": "in"}], ours, []) == ["yo"]
    # None of our messages in view: nothing can be said.
    assert new_replies([{"text": "yo", "side": "in"}], ours, []) is None


# ---------- message variants ----------


def test_variants_are_random_without_the_last_two():
    rng = random.Random(7)
    picker = VariantPicker(["a", "b", "c", "a", " ", ""], rng)
    assert picker.variants == ["a", "b", "c"]
    picks = []
    for _ in range(60):
        choice = picker.pick()
        assert choice not in picks[-2:]
        picker.used(choice)
        picks.append(choice)
    assert set(picks) == {"a", "b", "c"}
    two = VariantPicker(["a", "b"], rng)
    picks = []
    for _ in range(10):
        picks.append(two.pick())
        two.used(picks[-1])
    assert all(x != y for x, y in zip(picks, picks[1:], strict=False))
    one = VariantPicker(["a"], rng)
    assert [one.pick() for _ in range(3)] == ["a", "a", "a"]


def test_a_retry_sends_the_same_variant(app):
    service, sessions, clock, senders, _ = app
    service.call(
        "outreach.workspace_update",
        {
            "usernames": ["one", "two", "three"],
            "messages": ["First {{firstName}}", "Second", "Third"],
            "sender_ids": senders[:1],
        },
    )
    service.call("outreach.workspace_start", {})
    with sessions() as session:
        jobs = session.scalars(select(OutboundMessageJob).order_by(OutboundMessageJob.id)).all()
        assert len({job.variant for job in jobs}) == 3  # three recipients, no repeats
        assert all(job.body for job in jobs)
    job = take(service)
    text = job["args"]["text"]
    commit(service, job, error("dialog")(job))
    for _ in range(6):
        again = take(service)
        if again is None:
            clock.advance(minutes=3)
        elif again["job_id"] == job["job_id"]:
            break
        else:
            commit(service, again, error("dialog")(again))
    assert again["job_id"] == job["job_id"] and again["args"]["text"] == text


# ---------- migration ----------


def test_followups_planned_by_an_earlier_version_wait_for_the_user(tmp_path):
    path = tmp_path / "old.db"
    engine, sessions = open_database(path)
    with sessions.begin() as session:
        lead = Lead(platform="instagram", username="old", display_name="Old Timer", profile_url="")
        template = OutreachTemplate(name="Bump", body="Bump {{firstName}}")
        session.add_all([lead, template])
        session.flush()
        conversation = Conversation(lead_id=lead.id, sender_account_id="a" * 32)
        session.add(conversation)
        session.flush()
        sent_at = utcnow() - timedelta(days=20)
        session.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                type="initial",
                body="Hey Old",
                sender_account_id="a" * 32,
                sent_at=sent_at,
                idempotency_key="initial-outreach:1:1",
            )
        )
        chain = FollowUpSequence(name="Old chain", steps=[])
        session.add(chain)
        session.flush()
        ids = (conversation.id, chain.id, template.id)
    engine.dispose()
    raw = sqlite3.connect(path)
    # Schema 10 steps named a template instead of carrying their text.
    old_steps = [{"delay_days": 3, "template_id": ids[2]}, {"delay_days": 4, "template_id": ids[2]}]
    raw.execute(
        "UPDATE outreach_followup_sequences SET steps = ? WHERE id = ?",
        (json.dumps(old_steps), ids[1]),
    )
    raw.execute(
        "CREATE TABLE outreach_followup_jobs (id INTEGER PRIMARY KEY, conversation_id INTEGER"
        " NOT NULL, sequence_id INTEGER NOT NULL, step_index INTEGER NOT NULL, template_id"
        " INTEGER NOT NULL, scheduled_at DATETIME NOT NULL, status VARCHAR(20) NOT NULL,"
        " cancel_reason VARCHAR(40), created_at DATETIME NOT NULL)"
    )
    for step in (0, 1):
        raw.execute(
            "INSERT INTO outreach_followup_jobs (conversation_id, sequence_id, step_index,"
            " template_id, scheduled_at, status, created_at) VALUES (?, ?, ?, ?,"
            " '2026-01-01 00:00:00', 'pending', '2026-01-01 00:00:00')",
            (ids[0], ids[1], step, ids[2]),
        )
    raw.execute("DELETE FROM schema_migrations WHERE version = 28")
    raw.commit()
    raw.close()

    engine, sessions = open_database(path)
    with sessions() as session:
        chain = session.get(FollowUpSequence, ids[1])
        assert chain.steps == [
            {"delay_days": 3, "text": "Bump {{firstName}}"},
            {"delay_days": 4, "text": "Bump {{firstName}}"},
        ]
        run = session.scalar(select(FollowUpRun))
        assert run.status == "paused" and run.stop_reason == reasons.LEGACY
        assert run.steps[0]["text"] == "Bump Old" and run.next_step == 0
        # Nothing is due: no step job exists until the user starts the chain.
        assert session.scalar(select(func.count()).select_from(FollowUpJob)) == 0
    raw = sqlite3.connect(path)
    assert raw.execute(
        "SELECT count(*) FROM sqlite_master WHERE name = 'outreach_followup_jobs'"
    ).fetchone() == (0,)
    raw.close()
    engine.dispose()


def test_the_legacy_chain_starts_only_when_resumed(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "Jay Carter")
    chain = sequence(service)
    campaign_id = campaign(service, [lead], senders[:1], followup_sequence_id=chain["id"])
    service.call("outreach.campaign_start", {"id": campaign_id})
    # A campaign started before schema 28 has no copy of its sequence.
    with sessions.begin() as session:
        session.get(OutreachCampaign, campaign_id).followup_snapshot = {}
    send(service)
    run = the_run(sessions)
    assert run.status == "paused" and run.stop_reason == reasons.LEGACY
    clock.advance(days=5)
    assert take(service) is None
    service.call("outreach.followup_control", {"run_id": run.id, "action": "resume"})
    assert take(service)["kind"] == "followup"
