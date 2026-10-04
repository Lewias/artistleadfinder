"""Outreach: campaigns, eligibility, queue, worker, idempotency, CRM and follow-ups.

The sender is a mock of the browser send script; nothing reaches Instagram.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from artist_lead_finder.database import open_database
from artist_lead_finder.models import (
    CampaignRecipient,
    Conversation,
    FollowUpJob,
    Lead,
    LeadScoutProfile,
    Message,
    OutboundMessageJob,
    OutreachCampaign,
    OutreachEvent,
    utcnow,
)
from artist_lead_finder.outreach import reasons
from artist_lead_finder.outreach.renderer import LeadVariables, MessageTemplateRenderer
from artist_lead_finder.outreach.worker import OutreachWorker, client_context
from artist_lead_finder.service import ApplicationService

GREETING = "Hey {{firstName}}, checked your music out — wanted to reach out real quick."


class Clock:
    def __init__(self):
        self.value = utcnow()

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "outreach.db")
    service = ApplicationService(sessions, tmp_path / "data")
    clock = Clock()
    service.outreach.now = clock
    open_windows: set[str] = set()
    service.outreach.window_open = lambda profile_id: profile_id in open_windows
    senders = [
        service.call("browser.create", {"name": name})["id"] for name in ("Sender A", "Sender B")
    ]
    open_windows.update(senders)
    yield service, sessions, clock, senders, open_windows
    service.shutdown()
    engine.dispose()


def add_lead(sessions, username, name="", *, followers=2500, category="artist", email=None):
    with sessions.begin() as session:
        lead = Lead(
            platform="instagram",
            username=username,
            display_name=name,
            profile_url=f"https://www.instagram.com/{username}/",
            followers=followers,
        )
        session.add(lead)
        session.flush()
        session.add(
            LeadScoutProfile(
                lead_id=lead.id,
                profile_type=category,
                profile_confidence=80,
                emails=[email] if email else [],
                source_username="rapdaily",
                discovery_method="post",
            )
        )
        return lead.id


def template(service, body=GREETING):
    return service.call("outreach.template_save", {"name": "Intro", "body": body})["id"]


def campaign(service, lead_ids, senders, **extra):
    params = {
        "name": "Artist Outreach September",
        "lead_ids": lead_ids,
        "sender_ids": senders,
        "template_id": extra.pop("template_id", None) or template(service),
        **extra,
    }
    return service.call("outreach.campaign_create", params)["id"]


def sent_ok(job):
    return {
        "outcome": "sent",
        "message_id": f"m{job['job_id']}",
        "thread_id": "t1",
        "user_id": "",
    }


def drive(service, clock, sender=sent_ok, limit=50):
    """The desktop shell's loop: next job → mock send → commit. Returns the handed jobs."""
    handed = []
    for _ in range(limit):
        job = service.call("outreach.next_internal", {})
        if job is None:
            clock.advance(minutes=10)
            job = service.call("outreach.next_internal", {})
            if job is None:
                break
        handed.append(job)
        service.call(
            "outreach.commit_internal",
            {"job_id": job["job_id"], "token": job["token"], "result": sender(job)},
        )
        clock.advance(minutes=5)
    return handed


def recipients(sessions, campaign_id):
    with sessions() as session:
        return {
            row.username: row
            for row in session.scalars(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
            )
        }


# ---------- Template rendering ----------


def test_template_rendering_and_fallbacks():
    renderer = MessageTemplateRenderer()
    result = renderer.render(GREETING, LeadVariables("jaycarter", "Jay Carter"))
    assert result.text == "Hey Jay, checked your music out — wanted to reach out real quick."
    assert result.valid and result.fallbacks == []
    missing = renderer.render(GREETING, LeadVariables("jaycarter", "🎤✨"))
    assert missing.text.startswith("Hey there, checked") and missing.fallbacks == ["firstName"]
    own = renderer.render("Yo {{ firstName | friend }}!", LeadVariables("x", ""))
    assert own.text == "Yo friend!"
    full = renderer.render(
        "{{artistName}} · {{followers}} · {{source}} · {{profileType}} · @{{username}}",
        LeadVariables("jay.c", "Jay Carter | Official", 12500, "rapdaily", "producer"),
    )
    assert full.text == "Jay Carter · 12.5K · @rapdaily · producer · @jay.c"
    empty = renderer.render("Hi {{firstName|}} , your {{followers}} fans", LeadVariables("a", ""))
    assert empty.text == "Hi, your fans"
    for value in ("None", "undefined", "{{", "}}"):
        assert value not in empty.text
    unknown = renderer.render("Hi {{nickname}}", LeadVariables("a", "A"))
    assert not unknown.valid and "nickname" in unknown.errors[0]
    long = renderer.render("x" * 1001, LeadVariables("a"))
    assert not long.valid and "1000" in long.errors[0]


def test_template_rpc_validates_and_previews(app):
    service, *_ = app
    with pytest.raises(ValueError):
        service.call("outreach.template_save", {"name": "Bad", "body": "Hi {{nick}}"})
    preview = service.call("outreach.template_render", {"body": GREETING})
    assert preview["valid"] and preview["text"].startswith("Hey Jay,")


# ---------- Campaign creation and audience ----------


def test_campaign_creation_is_a_draft_that_sends_nothing(app):
    service, sessions, clock, senders, _ = app
    ids = [add_lead(sessions, "artistone", "Artist One"), add_lead(sessions, "artisttwo")]
    campaign_id = campaign(service, ids + ids[:1], senders[:1])
    detail = service.call("outreach.campaign", {"id": campaign_id})
    assert detail["status"] == "draft" and detail["total_recipients"] == 2
    assert detail["sender_strategy"] == "single"
    assert service.call("outreach.next_internal", {}) is None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutboundMessageJob)) == 0
        types = [row.type for row in session.scalars(select(OutreachEvent))]
    assert types == ["campaign:created"]


def test_audience_filters(app):
    service, sessions, *_ = app
    add_lead(sessions, "withmail", "A", email="a@mail.com", followers=5000)
    add_lead(sessions, "producer1", "P", category="producer", followers=800)
    dnc = add_lead(sessions, "blocked", "B")
    contacted = add_lead(sessions, "already", "C")
    with sessions.begin() as session:
        session.get(Lead, dnc).do_not_contact = True
        session.get(Lead, contacted).status = "contacted"
    names = lambda params: {  # noqa: E731
        item["username"] for item in service.call("outreach.audience", params)["items"]
    }
    assert names({}) == {"withmail", "producer1"}
    assert names({"profile_types": ["producer"]}) == {"producer1"}
    assert names({"has_email": True}) == {"withmail"}
    assert names({"min_followers": 1000}) == {"withmail"}
    assert names({"source_username": "@RapDaily", "min_confidence": 70}) == {
        "withmail",
        "producer1",
    }
    assert "blocked" in names({"include_dnc": True})
    assert "already" in names({"include_contacted": True})
    assert len(service.call("outreach.audience", {})["ids"]) == 2


def test_preview_shows_sender_recipient_and_message(app):
    service, sessions, clock, senders, _ = app
    ids = [add_lead(sessions, f"artist{n}", f"Name{n} Last") for n in range(4)]
    dnc = add_lead(sessions, "nope", "No")
    with sessions.begin() as session:
        session.get(Lead, dnc).do_not_contact = True
    preview = service.call(
        "outreach.preview",
        {"lead_ids": ids + [dnc], "sender_ids": senders, "template_id": template(service)},
    )
    assert preview["total"] == 5 and preview["eligible"] == 4
    assert preview["skipped"] == {reasons.DO_NOT_CONTACT: 1}
    assert preview["accounts"] == 2 and preview["estimated_queued"] == 4
    first = preview["examples"][0]
    assert first["sender_name"] == "Sender A" and first["message"].startswith("Hey Name0,")
    assert [item["sender_name"] for item in preview["examples"]] == [
        "Sender A",
        "Sender B",
        "Sender A",
        "Sender B",
    ]
    assert service.call("outreach.next_internal", {}) is None


# ---------- Eligibility ----------


def test_do_not_contact_and_already_contacted_are_skipped(app):
    service, sessions, clock, senders, _ = app
    fresh = add_lead(sessions, "fresh", "Fresh One")
    dnc = add_lead(sessions, "dnc", "Do Not")
    marked = add_lead(sessions, "marked", "Marked")
    service.call("leads.do_not_contact", {"id": dnc, "value": True})
    service.call("leads.status", {"id": marked, "status": "contacted"})
    first = campaign(service, [fresh, dnc, marked], senders[:1])
    service.call("outreach.campaign_start", {"id": first})
    rows = recipients(sessions, first)
    assert rows["dnc"].status == "skipped" and rows["dnc"].skip_reason == reasons.DO_NOT_CONTACT
    assert rows["marked"].skip_reason == reasons.ALREADY_CONTACTED
    assert rows["fresh"].status == "queued"
    drive(service, clock)
    # Global history: a second campaign does not message the same lead again.
    second = campaign(service, [fresh], senders)
    service.call("outreach.campaign_start", {"id": second})
    assert recipients(sessions, second)["fresh"].skip_reason == reasons.ALREADY_CONTACTED
    assert service.call("outreach.campaign", {"id": second})["status"] == "completed"


def test_dnc_set_after_queueing_is_rechecked_before_sending(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "later", "Later")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    service.call("leads.do_not_contact", {"id": lead, "value": True})
    assert drive(service, clock) == []
    row = recipients(sessions, campaign_id)["later"]
    assert row.status == "skipped" and row.skip_reason == reasons.DO_NOT_CONTACT
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 0


# ---------- Queue, send, CRM ----------


def test_queue_send_history_conversation_and_crm(app):
    service, sessions, clock, senders, _ = app
    ids = [add_lead(sessions, "artistone", "Jay Carter"), add_lead(sessions, "artisttwo", "Kim")]
    campaign_id = campaign(service, ids, senders[:1])
    started = service.call("outreach.campaign_start", {"id": campaign_id})
    assert started["status"] == "running" and started["queued_count"] == 2
    with sessions() as session:
        jobs = session.scalars(select(OutboundMessageJob).order_by(OutboundMessageJob.id)).all()
    assert [job.idempotency_key for job in jobs] == [
        f"initial-outreach:{campaign_id}:{ids[0]}",
        f"initial-outreach:{campaign_id}:{ids[1]}",
    ]
    assert {job.status for job in jobs} == {"ready"}
    handed = drive(service, clock)
    assert [job["args"]["username"] for job in handed] == ["artistone", "artisttwo"]
    assert handed[0]["args"]["text"].startswith("Hey Jay, checked")
    assert handed[0]["args"]["client_context"] == client_context(jobs[0].idempotency_key)
    detail = service.call("outreach.campaign", {"id": campaign_id})
    assert detail["status"] == "completed" and detail["sent_count"] == 2
    assert detail["queued_count"] == 0 and detail["finished_at"]
    with sessions() as session:
        lead = session.get(Lead, ids[0])
        assert lead.status == "contacted" and lead.last_contacted_at is not None
        conversation = session.scalar(select(Conversation).where(Conversation.lead_id == ids[0]))
        assert (
            conversation.status == "waiting_reply" and conversation.sender_account_id == senders[0]
        )
        assert conversation.first_outbound_at and conversation.platform_thread_id == "t1"
        message = session.scalar(select(Message).where(Message.conversation_id == conversation.id))
        assert (message.direction, message.type, message.campaign_id) == (
            "outbound",
            "initial",
            campaign_id,
        )
        assert message.body.startswith("Hey Jay") and message.platform_message_id
        row = session.scalar(select(CampaignRecipient).where(CampaignRecipient.lead_id == ids[0]))
        assert row.status == "sent" and row.message_id == message.id and row.sent_at
    card = service.call("leads.detail", {"id": ids[0]})["outreach"]
    assert card["contacted"] and card["sender_name"] == "Sender A"
    assert card["campaign"]["name"] == "Artist Outreach September"
    assert card["conversation_status"] == "waiting_reply" and len(card["messages"]) == 1
    types = [
        event["type"] for event in service.call("outreach.events", {"campaign_id": campaign_id})
    ]
    for expected in ("campaign:started", "recipient:queued", "recipient:sending", "recipient:sent"):
        assert expected in types
    assert types[-1] == "campaign:completed"


def test_failed_send_is_structured_and_not_retried(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "gone", "Gone")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    handed = drive(service, clock, lambda job: {"outcome": "error", "error": "not_found"})
    assert len(handed) == 1
    row = recipients(sessions, campaign_id)["gone"]
    assert row.status == "failed" and row.failure_reason == reasons.RECIPIENT_UNAVAILABLE
    assert service.call("outreach.campaign", {"id": campaign_id})["status"] == "failed"


def test_closed_messages_fail_the_recipient_and_keep_the_sender(app):
    service, sessions, clock, senders, _ = app
    closed, open_ = add_lead(sessions, "followonly", "F"), add_lead(sessions, "open", "O")
    campaign_id = campaign(service, [closed, open_], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    handed = drive(
        service,
        clock,
        lambda job: (
            {"outcome": "error", "error": "messages_closed", "status": None}
            if job["args"]["username"] == "followonly"
            else sent_ok(job)
        ),
    )
    assert len(handed) == 2
    rows = recipients(sessions, campaign_id)
    assert rows["followonly"].status == "failed"
    assert rows["followonly"].failure_reason == reasons.MESSAGES_CLOSED
    assert rows["open"].status == "sent"
    status = service.call("outreach.senders", {})
    assert next(item for item in status if item["id"] == senders[0])["status"] == "active"


def test_refusal_details_keep_the_http_status(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "refused", "R")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    drive(service, clock, lambda job: {"outcome": "error", "error": "rejected", "status": 401})
    row = recipients(sessions, campaign_id)["refused"]
    assert row.failure_reason == reasons.MESSAGE_REJECTED
    assert row.reason_details.endswith("(HTTP 401)")


def test_network_errors_retry_with_backoff_then_fail(app):
    service, sessions, clock, senders, _ = app
    first, second = add_lead(sessions, "flaky", "F"), add_lead(sessions, "down", "D")
    campaign_id = campaign(service, [first], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    outcomes = iter([{"outcome": "error", "error": "network"}])
    handed = drive(service, clock, lambda job: next(outcomes, None) or sent_ok(job))
    assert len(handed) == 2 and recipients(sessions, campaign_id)["flaky"].status == "sent"
    with sessions() as session:
        assert session.scalar(select(OutboundMessageJob.attempt_count)) == 2
    other = campaign(service, [second], senders[:1])
    service.call("outreach.campaign_start", {"id": other})
    handed = drive(service, clock, lambda job: {"outcome": "error", "error": "network"})
    assert len(handed) == 3  # outreach_max_attempts
    row = recipients(sessions, other)["down"]
    assert row.status == "failed" and row.failure_reason == reasons.NETWORK_ERROR


def test_rate_limit_stops_the_sender_without_switching_accounts(app):
    service, sessions, clock, senders, _ = app
    ids = [add_lead(sessions, f"artist{n}", "A") for n in range(2)]
    campaign_id = campaign(service, ids, senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    handed = drive(service, clock, lambda job: {"outcome": "error", "error": "rate_limited"})
    assert len(handed) == 1
    status = {row["id"]: row for row in service.call("outreach.senders", {})}
    assert status[senders[0]]["status"] == "rate_limited" and status[senders[0]]["until"]
    rows = recipients(sessions, campaign_id)
    assert {row.status for row in rows.values()} == {"queued"}
    events = service.call("outreach.events", {"campaign_id": campaign_id})
    assert any(
        e["type"] == "sender:unavailable" and e["payload"]["reason"] == reasons.RATE_LIMITED
        for e in events
    )
    # The break ends on its own; the same jobs continue through the same sender.
    clock.advance(hours=7)
    handed = drive(service, clock)
    assert {job["profile_id"] for job in handed} == {senders[0]}
    assert service.call("outreach.campaign", {"id": campaign_id})["sent_count"] == 2


def test_checkpoint_waits_for_the_user(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    drive(service, clock, lambda job: {"outcome": "error", "error": "checkpoint"})
    clock.advance(days=3)
    assert drive(service, clock) == []
    service.call("outreach.sender_status", {"id": senders[0], "status": "active"})
    assert len(drive(service, clock)) == 1
    assert recipients(sessions, campaign_id)["artist"].status == "sent"


def test_closed_sender_window_holds_the_queue(app):
    service, sessions, clock, senders, windows = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    windows.clear()
    assert drive(service, clock) == []
    unavailable = [
        e
        for e in service.call("outreach.events", {"campaign_id": campaign_id})
        if e["type"] == "sender:unavailable"
    ]
    assert len(unavailable) == 1  # not repeated on every poll
    windows.add(senders[0])
    assert len(drive(service, clock)) == 1


def test_pacing_between_messages_of_one_sender(app):
    service, sessions, clock, senders, _ = app
    ids = [add_lead(sessions, f"artist{n}", "A") for n in range(2)]
    campaign_id = campaign(service, ids, senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    job = service.call("outreach.next_internal", {})
    service.call(
        "outreach.commit_internal",
        {"job_id": job["job_id"], "token": job["token"], "result": sent_ok(job)},
    )
    assert service.call("outreach.next_internal", {}) is None
    clock.advance(seconds=181)
    assert service.call("outreach.next_internal", {}) is not None


# ---------- Pause, resume, cancel, schedule ----------


def test_pause_resume_and_cancel(app):
    service, sessions, clock, senders, _ = app
    ids = [add_lead(sessions, f"artist{n}", "A") for n in range(3)]
    campaign_id = campaign(service, ids, senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    assert len(drive(service, clock, limit=1)) == 1
    service.call("outreach.campaign_control", {"id": campaign_id, "action": "pause"})
    assert drive(service, clock) == []
    with sessions() as session:
        pending = session.scalars(
            select(OutboundMessageJob.status).where(OutboundMessageJob.status != "sent")
        ).all()
    assert pending == ["ready", "ready"]
    service.call("outreach.campaign_control", {"id": campaign_id, "action": "resume"})
    assert len(drive(service, clock, limit=1)) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutreachCampaign)) == 1
    service.call("outreach.campaign_control", {"id": campaign_id, "action": "cancel"})
    detail = service.call("outreach.campaign", {"id": campaign_id})
    assert detail["status"] == "cancelled" and detail["sent_count"] == 2
    rows = recipients(sessions, campaign_id)
    assert rows["artist2"].status == "cancelled"
    assert rows["artist2"].failure_reason == reasons.CAMPAIGN_CANCELLED
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 2
        assert (
            session.scalar(
                select(OutboundMessageJob.status).where(
                    OutboundMessageJob.recipient_id == rows["artist2"].id
                )
            )
            == "cancelled"
        )
    assert drive(service, clock) == []


def test_cancel_while_sending_keeps_the_sent_message(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    job = service.call("outreach.next_internal", {})
    service.call("outreach.campaign_control", {"id": campaign_id, "action": "cancel"})
    service.call(
        "outreach.commit_internal",
        {"job_id": job["job_id"], "token": job["token"], "result": sent_ok(job)},
    )
    assert recipients(sessions, campaign_id)["artist"].status == "sent"
    assert service.call("outreach.campaign", {"id": campaign_id})["status"] == "cancelled"


def test_scheduled_campaign_starts_at_its_time(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    at = (utcnow() + timedelta(hours=2)).isoformat()
    campaign_id = campaign(service, [lead], senders[:1], scheduled_at=at)
    assert service.call("outreach.campaign_start", {"id": campaign_id})["status"] == "scheduled"
    assert service.call("outreach.next_internal", {}) is None
    clock.advance(hours=3)
    assert len(drive(service, clock)) == 1
    assert service.call("outreach.campaign", {"id": campaign_id})["status"] == "completed"


# ---------- Idempotency and recovery ----------


def test_duplicate_results_and_workers_send_once(app, tmp_path):
    service, sessions, clock, senders, windows = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    second = OutreachWorker(sessions, service.settings, lambda profile_id: True, now=clock)
    job = service.call("outreach.next_internal", {})
    assert job is not None and second.next_job() is None
    commit = {"job_id": job["job_id"], "token": job["token"], "result": sent_ok(job)}
    service.call("outreach.commit_internal", commit)
    assert service.call("outreach.commit_internal", commit)["ignored"] is True
    assert second.commit({**commit, "token": "forged"})["ignored"] is True
    clock.advance(hours=1)
    assert service.call("outreach.next_internal", {}) is None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 1
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1


def test_crash_during_send_is_never_resent(app, tmp_path):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    assert service.call("outreach.next_internal", {}) is not None
    # App restart while the job was with the browser: no result ever arrives.
    restarted = ApplicationService(sessions, tmp_path / "data")
    try:
        row = recipients(sessions, campaign_id)["artist"]
        assert row.status == "failed" and row.needs_review
        assert row.failure_reason == reasons.SEND_ERROR
        restarted.outreach.window_open = lambda profile_id: True
        clock.advance(hours=1)
        restarted.outreach.now = clock
        assert restarted.outreach.next_job() is None
        # The lead stays blocked for new campaigns until the user checks the thread.
        other = campaign(restarted, [lead], senders[:1])
        restarted.call("outreach.campaign_start", {"id": other})
        assert recipients(sessions, other)["artist"].skip_reason == reasons.ALREADY_CONTACTED
        restarted.call("outreach.resolve_review", {"recipient_id": row.id, "sent": True})
        row = recipients(sessions, campaign_id)["artist"]
        assert row.status == "sent" and not row.needs_review
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(Message)) == 1
    finally:
        restarted.shutdown()


def test_crash_after_message_record_is_repaired_not_resent(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    job = service.call("outreach.next_internal", {})
    with sessions.begin() as session:
        conversation = Conversation(lead_id=lead, sender_account_id=senders[0])
        session.add(conversation)
        session.flush()
        key = session.get(OutboundMessageJob, job["job_id"]).idempotency_key
        session.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                type="initial",
                idempotency_key=key,
            )
        )
    assert service.outreach.recover() == 1
    assert recipients(sessions, campaign_id)["artist"].status == "sent"


def test_unconfirmed_send_is_not_retried(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    handed = drive(service, clock, lambda job: {"outcome": "error", "error": "unconfirmed"})
    assert len(handed) == 1
    row = recipients(sessions, campaign_id)["artist"]
    assert row.status == "failed" and row.needs_review


def test_send_transaction_failure_leaves_no_partial_crm_state(app, monkeypatch):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    steps = service.call("outreach.template_save", {"name": "Bump", "body": "Bump {{firstName}}"})
    sequence = service.call(
        "outreach.sequence_save",
        {"name": "Two bumps", "steps": [{"delay_days": 3, "template_id": steps["id"]}]},
    )
    campaign_id = campaign(service, [lead], senders[:1], followup_sequence_id=sequence["id"])
    service.call("outreach.campaign_start", {"id": campaign_id})

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service.outreach.followups, "start", broken)
    assert len(drive(service, clock)) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 0
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert session.scalar(select(func.count()).select_from(FollowUpJob)) == 0
        assert session.get(Lead, lead).status == "new"
    row = recipients(sessions, campaign_id)["artist"]
    assert row.status == "failed" and row.needs_review
    clock.advance(hours=2)
    assert service.call("outreach.next_internal", {}) is None


# ---------- Multiple senders ----------


def test_round_robin_senders_and_conversation_affinity(app):
    service, sessions, clock, senders, _ = app
    ids = [add_lead(sessions, f"artist{n}", "A") for n in range(4)]
    # artist3 already talks to Sender B: it keeps that sender.
    with sessions.begin() as session:
        session.add(Conversation(lead_id=ids[3], sender_account_id=senders[1]))
    service.call("settings.save", {"outreach_skip_previously_contacted": True})
    campaign_id = campaign(service, ids, senders)
    detail = service.call("outreach.campaign_start", {"id": campaign_id})
    assert detail["sender_strategy"] == "round_robin"
    rows = recipients(sessions, campaign_id)
    assert [rows[f"artist{n}"].sender_account_id for n in range(4)] == [
        senders[0],
        senders[1],
        senders[0],
        senders[1],
    ]
    handed = drive(service, clock)
    assert {job["profile_id"] for job in handed} == set(senders)
    assert service.call("outreach.campaign", {"id": campaign_id})["sent_count"] == 4


def test_no_active_sender_blocks_start(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.sender_status", {"id": senders[0], "status": "disabled"})
    with pytest.raises(ValueError):
        service.call("outreach.campaign_start", {"id": campaign_id})


# ---------- Follow-ups and replies ----------


def test_followups_are_planned_after_send_and_cancelled_on_reply(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "Jay Carter")
    bump = service.call("outreach.template_save", {"name": "Bump", "body": "Bump {{firstName}}"})
    sequence = service.call(
        "outreach.sequence_save",
        {
            "name": "Bumps",
            "steps": [
                {"delay_days": 3, "template_id": bump["id"]},
                {"delay_days": 4, "template_id": bump["id"]},
            ],
        },
    )
    campaign_id = campaign(service, [lead], senders[:1], followup_sequence_id=sequence["id"])
    service.call("outreach.campaign_start", {"id": campaign_id})
    assert drive(service, clock)
    with sessions() as session:
        jobs = session.scalars(select(FollowUpJob).order_by(FollowUpJob.step_index)).all()
        sent_at = session.scalar(select(Message.sent_at))
    assert [job.status for job in jobs] == ["pending", "pending"]
    assert jobs[0].scheduled_at == sent_at + timedelta(days=3)
    assert jobs[1].scheduled_at == sent_at + timedelta(days=7)
    result = service.call("outreach.mark_replied", {"lead_id": lead, "body": "yo, send it"})
    assert result["followups_cancelled"] == 2
    with sessions() as session:
        assert set(session.scalars(select(FollowUpJob.status))) == {"cancelled"}
        conversation = session.scalar(select(Conversation))
        assert conversation.status == "replied" and conversation.last_inbound_at
        inbound = session.scalar(select(Message).where(Message.direction == "inbound"))
        assert inbound.type == "reply" and inbound.body == "yo, send it"
    assert service.call("outreach.campaign", {"id": campaign_id})["replied_count"] == 1
    replied = service.call("outreach.recipients", {"id": campaign_id, "status": "replied"})
    assert replied["total"] == 1


def test_no_followups_without_a_sequence(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    drive(service, clock)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(FollowUpJob)) == 0


def test_stopped_conversation_is_never_contacted(app):
    service, sessions, clock, senders, _ = app
    lead = add_lead(sessions, "artist", "A")
    with sessions.begin() as session:
        session.add(Conversation(lead_id=lead, sender_account_id=senders[0]))
    service.call("outreach.stop_conversation", {"lead_id": lead})
    service.call("settings.save", {"outreach_skip_previously_contacted": False})
    campaign_id = campaign(service, [lead], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    assert recipients(sessions, campaign_id)["artist"].skip_reason == reasons.DO_NOT_CONTACT


def test_recipient_table_filters_by_status(app):
    service, sessions, clock, senders, _ = app
    ok, dnc = add_lead(sessions, "ok", "Ok Name"), add_lead(sessions, "dnc", "D")
    service.call("leads.do_not_contact", {"id": dnc, "value": True})
    campaign_id = campaign(service, [ok, dnc], senders[:1])
    service.call("outreach.campaign_start", {"id": campaign_id})
    drive(service, clock)
    table = service.call("outreach.recipients", {"id": campaign_id})
    assert table["total"] == 2
    first = table["items"][0]
    assert first["sender_name"] == "Sender A" and first["profile_type"] == "artist"
    assert first["rendered_message"].startswith("Hey Ok,")
    skipped = service.call("outreach.recipients", {"id": campaign_id, "status": "skipped"})
    assert [item["username"] for item in skipped["items"]] == ["dnc"]


# ---------- Migration ----------


def test_schema_9_database_migrates_to_11(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    engine, sessions = open_database(path)
    add_lead(sessions, "keeper", "Keeper")
    engine.dispose()
    # Back to schema 9: no outreach tables, no lead outreach columns.
    raw = sqlite3.connect(path)
    for table in (
        "outreach_followup_jobs",
        "outbound_message_jobs",
        "campaign_recipients",
        "messages",
        "conversations",
        "outreach_events",
        "outreach_campaigns",
        "outreach_followup_sequences",
        "outreach_templates",
        "outreach_senders",
        "outreach_workspace",
    ):
        raw.execute(f"DROP TABLE {table}")
    raw.execute("ALTER TABLE leads DROP COLUMN do_not_contact")
    raw.execute("ALTER TABLE leads DROP COLUMN last_contacted_at")
    raw.execute("DELETE FROM schema_migrations WHERE version >= 10")
    raw.commit()
    raw.close()
    engine, sessions = open_database(path)
    with sessions() as session:
        lead = session.scalar(select(Lead))
        assert lead.username == "keeper" and lead.do_not_contact is False
        assert lead.last_contacted_at is None
        assert session.scalar(select(func.count()).select_from(OutreachCampaign)) == 0
    engine.dispose()
    raw = sqlite3.connect(path)
    assert raw.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 14
    raw.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (15, '2030-01-01')")
    raw.commit()
    raw.close()
    with pytest.raises(RuntimeError):
        open_database(path)


# ---------- Primary outreach list ----------


def test_workspace_list_sends_variants_and_skips_done(app):
    service, sessions, clock, senders, _ = app
    known = add_lead(sessions, "known_artist", "Jay Carter")
    state = service.call(
        "outreach.workspace_update",
        {
            "usernames": [
                "@Known_Artist",
                "https://www.instagram.com/newface/",
                "third",
                "newface",
            ],
            "messages": ["Yo {{firstName}}, got beats for u", "Second variant", "  "],
            "sender_ids": senders[:1],
        },
    )
    assert [item["username"] for item in state["usernames"]] == ["known_artist", "newface", "third"]
    assert state["messages"] == ["Yo {{firstName}}, got beats for u", "Second variant"]
    assert {item["status"] for item in state["usernames"]} == {"new"}
    with pytest.raises(ValueError):
        service.call("outreach.workspace_update", {"usernames": ["bad name!"]})
    with pytest.raises(ValueError):
        service.call("outreach.workspace_update", {"messages": ["Hi {{unknown}}"]})

    state = service.call("outreach.workspace_start", {})
    assert state["running"] and state["campaign"]["total_recipients"] == 3
    with pytest.raises(ValueError, match="уже идёт"):
        service.call("outreach.workspace_start", {})
    drive(service, clock)
    state = service.call("outreach.workspace", {})
    assert not state["running"]
    assert {item["status"] for item in state["usernames"]} == {"sent"}
    rows = recipients(sessions, state["campaign"]["id"])
    assert rows["known_artist"].rendered_message == "Yo Jay, got beats for u"
    assert rows["newface"].rendered_message == "Second variant"
    assert rows["third"].rendered_message.startswith("Yo there")
    with sessions() as session:
        assert session.get(Lead, known).status == "contacted"
        assert session.scalar(select(func.count()).select_from(Lead)) == 3
    # Hidden list template is not offered as a template.
    assert service.call("outreach.templates", {}) == []

    # Everyone got a message: a second run has nobody to write to; new names go alone.
    with pytest.raises(ValueError, match="нет аккаунтов"):
        service.call("outreach.workspace_start", {})
    service.call(
        "outreach.workspace_update",
        {"usernames": [*(i["username"] for i in state["usernames"]), "fourth"]},
    )
    state = service.call("outreach.workspace_start", {})
    assert state["campaign"]["total_recipients"] == 1
    stopped = service.call("outreach.workspace_stop", {})
    assert not stopped["running"] and stopped["campaign"]["status"] == "cancelled"
    assert stopped["usernames"][-1]["status"] == "cancelled"


def test_workspace_start_without_active_sender_leaves_no_campaign(app):
    service, sessions, _, senders, _ = app
    service.call("outreach.sender_status", {"id": senders[0], "status": "paused"})
    service.call(
        "outreach.workspace_update",
        {"usernames": ["one"], "messages": ["Hi"], "sender_ids": senders[:1]},
    )
    with pytest.raises(ValueError, match="активных"):
        service.call("outreach.workspace_start", {})
    state = service.call("outreach.workspace", {})
    assert state["campaign"] is None and not state["running"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutreachCampaign)) == 0


def test_workspace_adds_leads_from_crm(app):
    service, sessions, _, _, _ = app
    first, second = add_lead(sessions, "first_one"), add_lead(sessions, "second_one")
    blocked = add_lead(sessions, "blocked_one")
    service.call("leads.do_not_contact", {"id": blocked, "value": True})
    with sessions.begin() as session:
        session.get(Lead, second).status = "qualified"
    assert service.call("outreach.workspace_add_leads", {"statuses": ["qualified"]})["added"] == 1
    added = service.call("outreach.workspace_add_leads", {"lead_ids": [first, second, blocked]})
    assert added["added"] == 1
    names = [item["username"] for item in service.call("outreach.workspace", {})["usernames"]]
    assert names == ["second_one", "first_one"]
