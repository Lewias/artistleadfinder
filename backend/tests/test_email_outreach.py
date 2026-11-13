"""«Почта»: Gmail mailboxes, the email queue, follow-ups, replies and bounces, over a
stand-in Gmail that records what was sent and serves a scripted inbox."""

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from artist_lead_finder.database import open_database
from artist_lead_finder.email_outreach import addresses, mailer
from artist_lead_finder.email_outreach.mailer import Incoming, MailError, Outgoing
from artist_lead_finder.email_outreach.service import EmailService, daily_cap, in_window
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import (
    EmailAccount,
    EmailJob,
    EmailSuppression,
    Lead,
    LeadScoutProfile,
)
from artist_lead_finder.service import ApplicationService

PASSWORD = "abcdabcdabcdabcd"
# 10:00 in New York: inside the default 9–18 sending hours.
START = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = START

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


class FakeGmail:
    """Every mailbox of the test: the sent mail, the inbox and errors to raise next."""

    def __init__(self):
        self.passwords: dict[str, str] = {}
        self.sent: list[Outgoing] = []
        self.inbox: dict[str, list[Incoming]] = {}
        self.send_errors: list[MailError] = []
        self.counter = 0

    def __call__(self, address: str, password: str):
        gmail = self

        class Transport:
            def verify(self):
                if gmail.passwords.get(address, PASSWORD) != password:
                    raise MailError("auth", "Gmail не принял пароль приложения.")

            def send(self, message: Outgoing) -> str:
                self.verify()
                if gmail.send_errors:
                    raise gmail.send_errors.pop(0)
                gmail.counter += 1
                gmail.sent.append(message)
                return f"<m{gmail.counter}@gmail.com>"

            def fetch(self, uidvalidity, last_uid, since):
                self.verify()
                return 7, [item for item in gmail.inbox.get(address, []) if item.uid > last_uid]

        return Transport()

    def deliver(self, mailbox: str, **fields) -> None:
        box = self.inbox.setdefault(mailbox, [])
        defaults = dict(
            uid=len(box) + 1,
            sender="",
            subject="",
            in_reply_to="",
            references=[],
            failed_recipients=[],
            auto_reply=False,
            bounce=False,
            text="",
        )
        box.append(Incoming(**{**defaults, **fields}))


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "db.sqlite3")
    clock, gmail, crm = Clock(), FakeGmail(), []
    service = EmailService(
        sessions,
        crm_merge=lambda name, items: crm.extend((name, item) for item in items),
        transport=gmail,
        now=clock,
        rng=random.Random(1),
    )
    # The tests drive the worker by hand.
    service._ensure_thread = lambda: None
    yield service, sessions, clock, gmail, crm
    engine.dispose()


def ready(service, recipients="jay@example.com, Jay Carter", followups=None, limit=20):
    service.account_add(
        {
            "address": "beats@gmail.com",
            "password": PASSWORD,
            "display_name": "Matt",
            "daily_limit": limit,
        }
    )
    service.add_text({"text": recipients})
    service.workspace_update(
        {
            "subjects": ["beats for {{firstName}}"],
            "bodies": ["yo {{firstName}} heard your stuff"],
            "followups": followups or [],
            "signature": "matt",
            "interval_min_seconds": 60,
            "interval_max_seconds": 60,
        }
    )


def jobs(sessions) -> list[EmailJob]:
    with sessions() as session:
        return list(session.scalars(select(EmailJob).order_by(EmailJob.position, EmailJob.step)))


# ---------- addresses ----------


def test_addresses_from_text_and_table():
    found = addresses.from_text(
        "Jay Carter <JAY@Example.com>\nno email here\nnoreply@label.com\nmia@site.io, Mia"
    )
    assert found == [
        {"email": "jay@example.com", "name": "Jay Carter"},
        {"email": "mia@site.io", "name": "Mia"},
    ]
    rows = [["Name", "Email"], ["Jay", "jay@example.com"], ["Bad", "not-an-email"]]
    assert addresses.from_table(rows) == [{"email": "jay@example.com", "name": "Jay"}]
    assert addresses.from_table([["x", "mia@site.io"]]) == [{"email": "mia@site.io", "name": ""}]
    assert addresses.normalize("mailto:a..b@x.com") is None


def test_message_is_plain_text_with_thread_and_unsubscribe_headers():
    msg, message_id = mailer.build(
        Outgoing("beats@gmail.com", "Matt", "jay@example.com", "Re: hi", "yo", "<a@x>", ["<0@x>"])
    )
    assert msg["In-Reply-To"] == "<a@x>" and msg["References"] == "<0@x> <a@x>"
    assert msg["List-Unsubscribe"] == "<mailto:beats@gmail.com?subject=unsubscribe>"
    assert msg.get_content_type() == "text/plain" and message_id.endswith("@gmail.com>")


def test_parse_bounce_and_auto_reply():
    bounce = mailer.parse(
        1,
        b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\n"
        b"X-Failed-Recipients: gone@example.com\r\nSubject: Delivery Status Notification\r\n\r\n"
        b"Address not found",
    )
    assert bounce.bounce and bounce.failed_recipients == ["gone@example.com"]
    auto = mailer.parse(
        2, b"From: jay@example.com\r\nAuto-Submitted: auto-replied\r\nSubject: Away\r\n\r\nbrb"
    )
    assert auto.auto_reply and not auto.bounce
    reply = mailer.parse(
        3,
        b"From: Jay <Jay@Example.com>\r\nIn-Reply-To: <m1@gmail.com>\r\n"
        b"Subject: Re: beats\r\n\r\nsend em",
    )
    assert reply.sender == "jay@example.com" and reply.in_reply_to == "<m1@gmail.com>"


# ---------- mailboxes ----------


def test_mailbox_needs_a_working_app_password(app):
    service, sessions, _, gmail, _ = app
    with pytest.raises(UserError, match="16 букв"):
        service.account_add({"address": "beats@gmail.com", "password": "my-real-password"})
    gmail.passwords["beats@gmail.com"] = "zzzzzzzzzzzzzzzz"
    with pytest.raises(UserError, match="пароль приложения"):
        service.account_add({"address": "beats@gmail.com", "password": PASSWORD})
    state = service.account_add(
        {"address": "beats@gmail.com", "password": "zzzz zzzz zzzz zzzz", "daily_limit": 20}
    )
    account = state["accounts"][0]
    assert account["status"] == "active" and account["cap_today"] == 5
    with sessions() as session:
        stored = session.scalar(select(EmailAccount))
        assert b"zzzz" not in stored.password_box
    with pytest.raises(UserError, match="уже добавлен"):
        service.account_add({"address": "Beats@gmail.com", "password": "zzzzzzzzzzzzzzzz"})


def test_warmup_grows_to_the_daily_limit():
    account = EmailAccount(daily_limit=20, warmup=True, warmup_started_at=START)
    assert daily_cap(account, START) == 5
    assert daily_cap(account, START + timedelta(days=3)) == 14
    assert daily_cap(account, START + timedelta(days=30)) == 20
    account.warmup = False
    assert daily_cap(account, START) == 20


def test_sending_hours_follow_the_recipients_zone():
    assert in_window("America/New_York", 9, 18, START)
    assert not in_window("America/New_York", 9, 18, START.replace(hour=4))


# ---------- the queue ----------


def test_campaign_sends_rendered_email_with_pause_and_warmup_cap(app):
    service, sessions, clock, gmail, _ = app
    people = "\n".join(f"artist{i}@example.com, Artist{i} Name" for i in range(8))
    ready(service, people)
    result = service.start({})
    assert result["queued"] == 8 and service.state()["workspace"]["recipients"] == []
    assert service.send_next()
    first = gmail.sent[0]
    assert (
        first.subject == "beats for Artist0" and first.body == "yo Artist0 heard your stuff\n\nmatt"
    )
    assert first.sender == "beats@gmail.com" and first.in_reply_to is None
    # The random pause between two emails of one mailbox.
    assert not service.send_next()
    for _ in range(10):
        clock.advance(seconds=61)
        service.send_next()
    # Warm-up day one: five emails.
    assert len(gmail.sent) == 5
    clock.advance(hours=24)
    service.send_next()
    assert len(gmail.sent) == 6


def test_nothing_is_sent_outside_sending_hours(app):
    service, _, clock, gmail, _ = app
    ready(service)
    service.start({})
    clock.value = START.replace(hour=3)
    assert not service.send_next()
    clock.value = START
    assert service.send_next() and len(gmail.sent) == 1


def test_followup_goes_in_the_same_thread_and_a_reply_stops_the_rest(app):
    service, sessions, clock, gmail, crm = app
    ready(
        service, followups=[{"days": 2, "body": "bump {{firstName}}"}, {"days": 3, "body": "last"}]
    )
    service.start({})
    service.send_next()
    clock.advance(days=1)
    assert not service.send_next()
    clock.advance(days=1, seconds=1)
    assert service.send_next()
    followup = gmail.sent[1]
    assert followup.subject == "Re: beats for Jay" and followup.body == "bump Jay\n\nmatt"
    assert followup.in_reply_to == "<m1@gmail.com>"
    gmail.deliver(
        "beats@gmail.com",
        sender="jay@example.com",
        in_reply_to="<m2@gmail.com>",
        subject="Re: beats for Jay",
        text="yeah send me some\nOn Mon Matt wrote:\n> bump",
    )
    totals = service.check_inbox()
    assert totals["replies"] == 1
    states = [job.status for job in jobs(sessions)]
    assert states == ["sent", "sent", "cancelled"]
    assert all(job.replied_at for job in jobs(sessions)[:2])
    name, contact = crm[0]
    assert name == "email" and contact["statuses"] == ["Ответил"]
    assert contact["channels"] == [{"kind": "email", "value": "jay@example.com"}]
    assert "yeah send me some" in contact["notes"] and "bump" not in contact["notes"]
    # The same reply read again is not a second CRM entry.
    service.inbox_checked_at = None
    assert service.check_inbox()["replies"] == 0 and len(crm) == 1
    # «Проверить ответы» leaves the reading to the worker and shows its result.
    assert service.request_inbox_check()["inbox_pending"]
    service.tick()
    state = service.state()
    assert not state["inbox_pending"] and state["inbox"]["replies"] == 0


def test_auto_reply_does_not_stop_followups(app):
    service, sessions, clock, gmail, crm = app
    ready(service, followups=[{"days": 1, "body": "bump"}])
    service.start({})
    service.send_next()
    gmail.deliver("beats@gmail.com", sender="jay@example.com", auto_reply=True, subject="Away")
    service.check_inbox()
    assert [job.status for job in jobs(sessions)] == ["sent", "pending"] and not crm


def test_bounce_suppresses_the_address_and_pauses_a_bouncing_mailbox(app):
    service, sessions, clock, gmail, _ = app
    people = "\n".join(f"gone{i}@example.com" for i in range(4))
    ready(service, people, followups=[{"days": 2, "body": "bump"}])
    service.start({})
    for _ in range(4):
        service.send_next()
        clock.advance(seconds=61)
    for index in range(3):
        gmail.deliver(
            "beats@gmail.com",
            sender="mailer-daemon@googlemail.com",
            bounce=True,
            failed_recipients=[f"gone{index}@example.com"],
        )
    assert service.check_inbox()["bounces"] == 3
    with sessions() as session:
        suppressed = set(session.scalars(select(EmailSuppression.address)))
        assert suppressed == {f"gone{i}@example.com" for i in range(3)}
        assert session.scalar(select(EmailAccount)).status == "bounces"
    followups = [job for job in jobs(sessions) if job.step == 1]
    assert [job.status for job in followups] == ["cancelled"] * 3 + ["pending"]
    # A suppressed address never comes back into the list.
    assert service.add_text({"text": "gone0@example.com"})["skipped"] == 1


def test_unsubscribe_reply_goes_to_the_stop_list_not_the_crm(app):
    service, sessions, _, gmail, crm = app
    ready(service)
    service.start({})
    service.send_next()
    gmail.deliver("beats@gmail.com", sender="jay@example.com", text="please unsubscribe me")
    assert service.check_inbox()["unsubscribed"] == 1
    with sessions() as session:
        assert session.get(EmailSuppression, "jay@example.com").reason == "unsubscribed"
    assert not crm


def test_previously_emailed_and_do_not_contact_are_skipped(app):
    service, sessions, _, _, _ = app
    with sessions.begin() as session:
        lead = Lead(
            platform="instagram", username="mia", display_name="Mia Rose", do_not_contact=True
        )
        session.add(lead)
        session.flush()
        session.add(
            LeadScoutProfile(
                lead_id=lead.id,
                emails=["mia@site.io"],
                profile_type="artist",
                source_username="src",
                discovery_method="post",
            )
        )
        keep = Lead(platform="instagram", username="kay", display_name="Kay Beats")
        session.add(keep)
        session.flush()
        session.add(
            LeadScoutProfile(
                lead_id=keep.id,
                emails=["kay@site.io"],
                profile_type="artist",
                source_username="src",
                discovery_method="post",
            )
        )
    ready(service)
    assert service.add_leads({"all": True})["added"] == 1
    assert {item["email"] for item in service.state()["workspace"]["recipients"]} == {
        "jay@example.com",
        "kay@site.io",
    }
    preview = service.preview({"index": 1})
    assert preview["subject"] == "beats for Kay"
    service.start({})
    service.add_text({"text": "jay@example.com"})
    assert service.state()["workspace"]["recipients"] == []
    with pytest.raises(UserError, match="Список адресов пуст"):
        service.start({})


def test_mailbox_errors_never_lose_or_repeat_an_email(app):
    service, sessions, clock, gmail, _ = app
    ready(service, "a@example.com\nb@example.com\nc@example.com\nd@example.com")
    service.start({})
    gmail.send_errors = [MailError("limited", "550 5.4.5 Daily user sending limit exceeded")]
    service.send_next()
    with sessions() as session:
        account = session.scalar(select(EmailAccount))
        assert account.status == "limited"
    assert [job.status for job in jobs(sessions)][0] == "pending"
    clock.advance(hours=24, seconds=1)
    gmail.send_errors = [MailError("recipient", "550 no such user")]
    service.send_next()
    clock.advance(seconds=61)
    gmail.send_errors = [MailError("uncertain", "lost")]
    service.send_next()
    clock.advance(seconds=61)
    service.send_next()
    statuses = {job.address: job.status for job in jobs(sessions)}
    assert statuses == {
        "a@example.com": "failed",
        "b@example.com": "uncertain",
        "c@example.com": "sent",
        "d@example.com": "pending",
    }
    with sessions() as session:
        assert session.get(EmailSuppression, "a@example.com").reason == "invalid"
    # A wrong password stops the mailbox; the email waits for it.
    gmail.passwords["beats@gmail.com"] = "yyyyyyyyyyyyyyyy"
    clock.advance(seconds=61)
    service.send_next()
    assert service.state()["accounts"][0]["status"] == "auth_failed"
    assert jobs(sessions)[3].status == "pending"
    service.account_update({"id": 1, "password": "yyyyyyyyyyyyyyyy"})
    clock.advance(seconds=61)
    assert service.send_next() and jobs(sessions)[3].status == "sent"


def test_restart_marks_a_cut_off_send_uncertain(app):
    service, sessions, _, gmail, _ = app
    ready(service, followups=[{"days": 1, "body": "bump"}])
    service.start({})
    with sessions.begin() as session:
        session.scalar(select(EmailJob).where(EmailJob.step == 0)).status = "sending"
    service.restore()
    assert [job.status for job in jobs(sessions)] == ["uncertain", "cancelled"]
    assert not service.send_next() and not gmail.sent


def test_stop_cancels_the_rest_and_finishes(app):
    service, sessions, _, _, _ = app
    ready(service, "a@example.com\nb@example.com")
    campaign = service.start({})["campaign_id"]
    service.send_next()
    state = service.control({"campaign_id": campaign, "action": "stop"})
    assert state["campaigns"] == []
    assert [job.status for job in jobs(sessions)] == ["sent", "cancelled"]
    history = service.campaigns()["campaigns"][0]
    assert history["status"] == "stopped" and history["sent"] == 1


def test_application_routes_email_methods(tmp_path):
    engine, sessions = open_database(tmp_path / "db.sqlite3")
    service = ApplicationService(sessions, tmp_path / "data")
    try:
        state = service.call("email.state", {})
        assert state["accounts"] == [] and state["workspace"]["window_zone"] == "America/New_York"
        assert service.email.thread is None
    finally:
        service.shutdown()
        engine.dispose()
