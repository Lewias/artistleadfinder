"""«Почта», part two: the email CRM and its sync, the thread and answers from the CRM, the
warm-up between the mailboxes and Nstbrowser partners. Gmail, IMAP, Nstbrowser and the
server are stand-ins; nothing touches the network."""

import io
import json
import random
import sqlite3
import urllib.error
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_email_outreach import PASSWORD, START, Clock, FakeGmail, jobs, ready

from artist_lead_finder.crm.service import CrmService
from artist_lead_finder.crm.sync import EMAIL_BLOCKED, CrmSync
from artist_lead_finder.database import open_database
from artist_lead_finder.email_outreach import mailer, warmup
from artist_lead_finder.email_outreach.mailer import Incoming, MailError
from artist_lead_finder.email_outreach.nst import NstClient, NstError, Profile
from artist_lead_finder.email_outreach.service import EmailService
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import (
    CrmContact,
    EmailAccount,
    EmailWarmupMessage,
    EmailWarmupPartner,
    Setting,
)


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "db.sqlite3")
    clock, gmail = Clock(), FakeGmail()
    crm = CrmService(sessions, lambda values: len(values), lambda values: len(values))
    service = EmailService(
        sessions, crm_merge=crm.merge, transport=gmail, now=clock, rng=random.Random(1)
    )
    crm.add_emails = service.add_values
    service._ensure_thread = lambda: None
    service.warmup._ensure_thread = lambda: None
    yield service, sessions, clock, gmail, crm
    engine.dispose()


def email_contacts(crm):
    return crm.contacts({"crm": "email"})["items"]


# ---------- the email CRM ----------


def test_the_crm_tables_of_an_older_database_take_the_email_crm(tmp_path):
    path = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE crm_contacts (id INTEGER NOT NULL, crm VARCHAR(12) NOT NULL, "
        "name VARCHAR(160) NOT NULL, statuses JSON NOT NULL, channels JSON NOT NULL, "
        "notes VARCHAR(5000) NOT NULL, last_contact_at DATETIME, next_action VARCHAR(300) "
        "NOT NULL, next_action_at DATETIME, earned FLOAT NOT NULL, potential FLOAT NOT NULL, "
        "lead_id INTEGER, deleted_at DATETIME, created_at DATETIME NOT NULL, updated_at "
        "DATETIME NOT NULL, PRIMARY KEY (id), CHECK (crm IN ('instagram','imessage')))"
    )
    raw.execute("CREATE INDEX ix_crm_contacts_lead ON crm_contacts (lead_id)")
    raw.execute(
        "INSERT INTO crm_contacts VALUES (7, 'imessage', 'Jay', '[]', '[]', 'note', NULL, '', "
        "NULL, 5, 0, NULL, NULL, '2026-10-01', '2026-10-01')"
    )
    raw.commit()
    raw.close()
    engine, sessions = open_database(path)
    with sessions.begin() as session:
        kept = session.get(CrmContact, 7)
        assert (kept.crm, kept.name, kept.notes, kept.earned) == ("imessage", "Jay", "note", 5)
        session.add(CrmContact(crm="email", name="Mia", channels=[]))
    engine.dispose()
    raw = sqlite3.connect(path)
    indexes = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_crm_contacts_lead" in indexes
    assert raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO crm_statuses (crm, label, color, position, emoji) "
            "VALUES ('telegram', 'x', 'violet', 0, '')"
        )


def test_a_reply_goes_to_the_email_crm_and_the_thread_goes_on_from_there(app):
    service, sessions, clock, gmail, crm = app
    ready(service, followups=[{"days": 3, "body": "bump"}])
    service.start({})
    assert service.send_next()
    clock.advance(hours=2)
    gmail.deliver(
        "beats@gmail.com",
        sender="jay@example.com",
        message_id="<r1@example.com>",
        in_reply_to="<m1@gmail.com>",
        subject="Re: beats for Jay",
        text="send me a pack\nOn Mon Matt wrote:\n> yo",
    )
    assert service.check_inbox()["replies"] == 1
    [contact] = email_contacts(crm)
    assert contact["statuses"] == ["Ответил"] and contact["channels"][0]["kind"] == "email"
    # The last contact comes from the email sent by the campaign.
    assert contact["last_contact_at"].startswith("2026-11-04T17:00")

    thread = service.conversation({"address": "jay@example.com"})
    assert thread["can_reply"] and thread["from"] == "beats@gmail.com"
    assert [(m["direction"], m["kind"]) for m in thread["messages"]] == [
        ("out", "campaign"),
        ("in", "reply"),
    ]
    assert thread["messages"][1]["body"] == "send me a pack"

    clock.advance(minutes=30)
    thread = service.reply({"address": "jay@example.com", "body": "bet, sending it now"})
    answer = gmail.sent[-1]
    assert answer.sender == "beats@gmail.com" and answer.to == "jay@example.com"
    assert answer.subject == "Re: beats for Jay" and not answer.unsubscribe
    assert answer.in_reply_to == "<r1@example.com>" and answer.references == ["<m1@gmail.com>"]
    assert [m["kind"] for m in thread["messages"]] == ["campaign", "reply", "manual"]
    assert [job.status for job in jobs(sessions)] == ["sent", "cancelled"]

    # Their answer to the email written from the CRM joins the thread too.
    clock.advance(hours=1)
    gmail.deliver(
        "beats@gmail.com",
        sender="jay@example.com",
        message_id="<r2@example.com>",
        in_reply_to="<m2@gmail.com>",
        subject="Re: beats for Jay",
        text="got it, fire",
    )
    service.inbox_checked_at = None
    assert service.check_inbox()["replies"] == 1
    thread = service.conversation({"address": "jay@example.com"})
    assert [m["body"] for m in thread["messages"]][-1] == "got it, fire"
    [contact] = email_contacts(crm)
    assert contact["last_contact_at"].startswith("2026-11-04T18:30")


def test_the_thread_says_why_an_answer_cannot_go(app):
    service, sessions, _, gmail, _ = app
    assert not service.conversation({"address": "new@example.com"})["can_reply"]
    with pytest.raises(UserError, match="ещё не было переписки"):
        service.reply({"address": "new@example.com", "body": "hi"})
    ready(service)
    service.start({})
    service.send_next()
    with sessions.begin() as session:
        session.scalar(select(EmailAccount)).status = "auth_failed"
    thread = service.conversation({"address": "jay@example.com"})
    assert not thread["can_reply"] and "без входа" in thread["blocker"]
    with pytest.raises(UserError, match="Напишите текст"):
        service.reply({"address": "jay@example.com", "body": "  "})


def test_write_from_the_email_crm_fills_the_next_campaign(app):
    service, _, _, _, crm = app
    crm.merge(
        "email",
        [
            {"name": "Jay", "channels": [{"kind": "email", "value": "jay@example.com"}]},
            {"name": "Ig only", "channels": [{"kind": "instagram", "value": "igonly"}]},
        ],
    )
    ids = [item["id"] for item in email_contacts(crm)]
    assert crm.write({"crm": "email", "ids": ids}) == {"added": 1, "found": 1}
    assert [item["email"] for item in service.state()["workspace"]["recipients"]] == [
        "jay@example.com"
    ]


def test_import_into_the_email_crm_keeps_only_contacts_with_an_email(app):
    _, _, _, _, crm = app
    crm.merge(
        "instagram",
        [
            {
                "name": "Jay",
                "channels": [
                    {"kind": "instagram", "value": "jaybeats"},
                    {"kind": "email", "value": "jay@example.com"},
                ],
            },
            {"name": "Mia", "channels": [{"kind": "instagram", "value": "mia"}]},
        ],
    )
    result = crm.import_verse({"crm": "email", "source": "instagram"})
    assert (result["added"], result["skipped"]) == (1, 1)
    [contact] = email_contacts(crm)
    # The email leads in the email CRM.
    assert contact["channels"][0] == {"kind": "email", "value": "jay@example.com"}


class Server:
    """The server before migration 0009: the crm check refuses 'email'."""

    def __init__(self):
        self.refuse_email = True
        self.contacts: list[dict] = []
        self.status_sets: list[dict] = []

    def upsert(self, table, rows, token, on_conflict=None):
        if self.refuse_email and any(row.get("crm") == "email" for row in rows):
            raise UserError('new row violates check constraint "crm_contacts_crm_check"')
        if table == "crm_contacts":
            self.contacts += rows
            return [{"id": row["id"], "owner_id": "user-1"} for row in rows]
        self.status_sets += rows
        return [{"updated_at": "2026-11-04T15:00:00+00:00"}]

    def select(self, table, query, token):
        return []

    def update(self, *args):
        return None


class Account:
    ready = True
    user_id = "user-1"
    sees_all_crm = False
    user = {"email": "me@example.com"}

    def __init__(self, client):
        self.client = client

    def token(self):
        return "token"


def test_the_email_crm_stays_local_until_the_server_takes_it(app):
    _, sessions, _, _, crm = app
    server = Server()
    sync = CrmSync(sessions, Account(server))
    crm.merge("instagram", [{"name": "Ig", "channels": [{"kind": "instagram", "value": "ig"}]}])
    crm.merge("email", [{"name": "Jay", "channels": [{"kind": "email", "value": "j@x.com"}]}])
    crm.contacts({"crm": "email"})  # seeds the email status list
    assert sync.run_once()
    assert [row["crm"] for row in server.contacts] == ["instagram"]
    with sessions() as session:
        email = session.scalar(select(CrmContact).where(CrmContact.crm == "email"))
        assert email.dirty and session.get(Setting, EMAIL_BLOCKED) is not None
    # After the migration the server is asked again once the pause is over.
    server.refuse_email = False
    sync.run_once()
    assert [row["crm"] for row in server.contacts] == ["instagram"]
    with sessions.begin() as session:
        session.delete(session.get(Setting, EMAIL_BLOCKED))
    assert sync.run_once()
    assert [row["crm"] for row in server.contacts] == ["instagram", "email"]
    assert any(row["crm"] == "email" for row in server.status_sets)
    with sessions() as session:
        assert not session.scalar(select(CrmContact).where(CrmContact.crm == "email")).dirty


# ---------- warm-up ----------


class Mailboxes:
    """Gmail for the warm-up: what is sent lands in the recipient's inbox (or spam for
    the addresses in `spam`); the receiving side takes it from there."""

    def __init__(self):
        self.folders: dict[str, dict[str, list[Incoming]]] = {}
        self.sent: list = []
        self.spam: set[str] = set()
        self.handled: list[tuple] = []
        self.send_errors: list[MailError] = []
        self.counter = 0

    def deliver(self, sender, to, subject, message_id, in_reply_to="", text=""):
        box = self.folders.setdefault(to, {"inbox": [], "spam": []})
        self.counter += 1
        box["spam" if to in self.spam else "inbox"].append(
            Incoming(
                uid=self.counter,
                sender=sender,
                subject=subject,
                in_reply_to=in_reply_to,
                references=[],
                failed_recipients=[],
                auto_reply=False,
                bounce=False,
                text=text,
                message_id=message_id,
            )
        )

    def __call__(self, address, password):
        world = self

        class Transport:
            def verify(self):
                if password != PASSWORD:
                    raise MailError("auth", "Gmail не принял пароль приложения.")

            def send(self, message):
                if world.send_errors:
                    raise world.send_errors.pop(0)
                message_id = f"<w{len(world.sent) + 1}@gmail.com>"
                world.sent.append((message, message_id))
                world.deliver(
                    address,
                    message.to,
                    message.subject,
                    message_id,
                    message.in_reply_to or "",
                    message.body,
                )
                return message_id

            def fetch(self, uidvalidity, last_uid, since):
                return 1, []

            def warm_handle(self, message_id, in_reply_to, star):
                box = world.folders.get(address, {"inbox": [], "spam": []})
                for where in ("inbox", "spam"):
                    for item in box[where]:
                        if (message_id and item.message_id == message_id) or (
                            not message_id and in_reply_to and item.in_reply_to == in_reply_to
                        ):
                            box[where].remove(item)
                            world.handled.append((address, item.message_id, where, star))
                            return {"placement": where, "message": item}
                return {"placement": "missing", "message": None}

        return Transport()


class Nst:
    """Nstbrowser with Gmail partners: profiles, and a Gmail tab per started profile."""

    def __init__(self, world: Mailboxes, profiles: dict[str, str]):
        self.world = world
        self.profiles = profiles  # profile id -> signed-in address
        self.offline = False
        self.broken: set[str] = set()
        self.opened: list[str] = []

    def client(self, key, url):
        nst = self

        class Client:
            api_key = key

            def profiles(self):
                if nst.offline:
                    raise NstError("offline", "Nstbrowser не отвечает")
                return [Profile(pid, f"Profile {pid}") for pid in nst.profiles]

        return Client()

    def session(self, client, profile_id):
        nst = self

        class Session:
            def __enter__(self):
                if nst.offline:
                    raise NstError("offline", "Nstbrowser не отвечает")
                if profile_id in nst.broken:
                    raise NstError("gmail", "В профиле не выполнен вход в Gmail.")
                nst.opened.append(profile_id)
                return Web(nst, nst.profiles[profile_id])

            def __exit__(self, *exc):
                return False

        return Session()


class Web:
    def __init__(self, nst: Nst, address: str):
        self.nst = nst
        self.own = address

    def address(self):
        return self.own

    def handle(self, message_id, star, reply):
        world = self.nst.world
        box = world.folders.get(self.own, {"inbox": [], "spam": []})
        for where in ("inbox", "spam"):
            for item in box[where]:
                if item.message_id == message_id:
                    box[where].remove(item)
                    world.handled.append((self.own, message_id, where, star))
                    if reply:
                        world.deliver(
                            self.own,
                            item.sender,
                            "Re: " + item.subject,
                            f"<web{world.counter}@gmail.com>",
                            in_reply_to=message_id,
                            text=reply,
                        )
                    return where
        return "missing"


@pytest.fixture
def warm(tmp_path, monkeypatch):
    monkeypatch.setattr(warmup, "REPLY_CHANCE", {0: 1.0, 1: 1.0})
    monkeypatch.setattr(warmup, "HANDLE_DELAY_MINUTES", (10, 10))
    engine, sessions = open_database(tmp_path / "db.sqlite3")
    clock, world = Clock(), Mailboxes()
    nst = Nst(world, {"p1": "partner.one@gmail.com", "p2": "partner.two@gmail.com"})
    service = EmailService(
        sessions,
        transport=world,
        now=clock,
        rng=random.Random(3),
        nst_client=nst.client,
        nst_session=nst.session,
    )
    service._ensure_thread = lambda: None
    service.warmup._ensure_thread = lambda: None
    yield service, sessions, clock, world, nst
    engine.dispose()


def add_boxes(service, *addresses):
    for address in addresses:
        service.account_add({"address": address, "password": PASSWORD})
    with service.sessions() as session:
        return [row.id for row in session.scalars(select(EmailAccount).order_by(EmailAccount.id))]


def rows(sessions) -> list[EmailWarmupMessage]:
    with sessions() as session:
        return list(session.scalars(select(EmailWarmupMessage).order_by(EmailWarmupMessage.id)))


def test_mailboxes_warm_each_other_up_and_spam_is_rescued(warm):
    service, sessions, clock, world, _ = warm
    first, second = add_boxes(service, "one@gmail.com", "two@gmail.com")
    warm_up = service.warmup
    warm_up.update({"id": first, "pool_warmup": True, "warm_target": 10})
    # Alone in the pool there is nobody to write to.
    assert not warm_up.send_next()
    warm_up.update({"id": second, "pool_warmup": True})
    with sessions.begin() as session:
        session.get(EmailAccount, first).warm_next_at = None
    world.spam.add("two@gmail.com")
    assert warm_up.send_next()
    note, note_id = world.sent[0]
    assert note.sender == "one@gmail.com" and note.to == "two@gmail.com"
    assert not note.unsubscribe and "http" not in note.body
    # The receiving side waits a little, like a person.
    assert warm_up.handle_mailboxes() == 0
    clock.advance(minutes=11)
    world.spam.clear()
    warm_up.handle_mailboxes()
    first_row = rows(sessions)[0]
    assert (first_row.status, first_row.placement, first_row.answered) == ("done", "spam", True)
    assert world.handled[0][:3] == ("two@gmail.com", note_id, "spam")
    answer, answer_id = world.sent[-1]
    assert (answer.sender, answer.to) == ("two@gmail.com", "one@gmail.com")
    assert answer.subject == "Re: " + note.subject and answer.in_reply_to == note_id
    clock.advance(minutes=11)
    warm_up.handle_mailboxes()
    clock.advance(minutes=11)
    warm_up.handle_mailboxes()
    # Three emails at most: the third is read and not answered.
    assert [(r.depth, r.status, r.answered) for r in rows(sessions)] == [
        (0, "done", True),
        (1, "done", True),
        (2, "done", False),
    ]
    state = warm_up.state()
    one = next(item for item in state["accounts"] if item["id"] == first)
    assert (one["inbox_7d"], one["spam_7d"], one["inbox_rate"]) == (1, 1, 50)


def test_the_volume_grows_a_little_every_day_and_rests_at_night(warm):
    service, sessions, clock, world, _ = warm
    first, second = add_boxes(service, "one@gmail.com", "two@gmail.com")
    for account_id in (first, second):
        service.warmup.update({"id": account_id, "pool_warmup": True, "warm_target": 20})
    with sessions() as session:
        account = session.get(EmailAccount, first)
        assert warmup.warm_cap(account, clock()) == warmup.WARM_START
        assert warmup.warm_cap(account, clock() + timedelta(days=2)) == warmup.WARM_START + 4
        assert warmup.warm_cap(account, clock() + timedelta(days=60)) == 20
    clock.value = START.replace(hour=4)  # 23:00 in New York
    assert not service.warmup.send_next()
    clock.value = START
    sent = 0
    for _ in range(20):
        with sessions.begin() as session:
            for account in session.scalars(select(EmailAccount)):
                account.warm_next_at = None
        sent += service.warmup.send_next()
    assert sent == 2 * warmup.WARM_START
    with pytest.raises(UserError):
        service.warmup.update({"id": first, "warm_target": 99})


def test_a_note_that_never_arrives_is_counted_as_missing(warm):
    service, sessions, clock, world, _ = warm
    ids = add_boxes(service, "one@gmail.com", "two@gmail.com")
    for account_id in ids:
        service.warmup.update({"id": account_id, "pool_warmup": True})
    service.warmup.send_next()
    world.folders["two@gmail.com"]["inbox"].clear()
    for _ in range(warmup.MAX_ATTEMPTS):
        clock.advance(minutes=30)
        service.warmup.handle_mailboxes()
    [row] = rows(sessions)
    assert (row.status, row.placement) == ("failed", "missing")


def test_a_wrong_password_found_by_the_warm_up_stops_the_mailbox(warm):
    service, sessions, _, world, _ = warm
    ids = add_boxes(service, "one@gmail.com", "two@gmail.com")
    for account_id in ids:
        service.warmup.update({"id": account_id, "pool_warmup": True})
    world.send_errors.append(MailError("auth", "Gmail не принял пароль приложения."))
    assert not service.warmup.send_next()
    with sessions() as session:
        statuses = {row.address: row.status for row in session.scalars(select(EmailAccount))}
    assert "auth_failed" in statuses.values()


def test_warm_up_mail_is_never_taken_for_a_campaign_reply(warm):
    service, _, _, world, _ = warm
    add_boxes(service, "one@gmail.com", "two@gmail.com")
    with service.sessions() as session:
        skip = service.warmup.addresses(session)
    assert {"one@gmail.com", "two@gmail.com"} <= skip


def connect_partners(service, nst):
    service.warmup.nst_save({"api_key": "nst-key"})
    assert service.warmup.partners_load()["added"] == 2
    service.warmup.request_detect()
    service.warmup.tick()


def test_nstbrowser_partners_are_found_and_take_part(warm):
    service, sessions, clock, world, nst = warm
    [box] = add_boxes(service, "one@gmail.com")
    with pytest.raises(UserError, match="API-ключ"):
        service.warmup.partners_load()
    connect_partners(service, nst)
    state = service.warmup.state()
    assert state["nst"]["configured"] and state["detect"] == {
        "running": False,
        "done": 2,
        "total": 2,
        "found": 2,
    }
    assert sorted(p["address"] for p in state["partners"]) == [
        "partner.one@gmail.com",
        "partner.two@gmail.com",
    ]
    with sessions() as session:
        stored = session.get(Setting, warmup.NST_SETTING).value
    assert "nst-key" not in json.dumps(stored)
    # A partner cannot be one of the working mailboxes.
    partner_id = state["partners"][0]["id"]
    with pytest.raises(UserError, match="рабочий ящик"):
        service.warmup.partner_update({"id": partner_id, "address": "one@gmail.com"})

    service.warmup.update({"id": box, "pool_warmup": True})
    world.spam.add("partner.one@gmail.com")
    world.spam.add("partner.two@gmail.com")
    assert service.warmup.send_next()
    note, note_id = world.sent[0]
    assert note.to.startswith("partner.")
    clock.advance(minutes=11)
    assert service.warmup.handle_partners() == 1
    first = rows(sessions)[0]
    assert (first.status, first.placement, first.answered) == ("done", "spam", True)
    # The partner's answer from the browser is found by our mailbox through In-Reply-To.
    answer = rows(sessions)[1]
    assert answer.message_id is None and answer.parent_message_id == note_id
    clock.advance(minutes=11)
    service.warmup.handle_mailboxes()
    assert rows(sessions)[1].status == "done"
    # Our answer back reaches the partner as the third and last email.
    assert rows(sessions)[2].depth == 2 and rows(sessions)[2].to_partner_id == first.to_partner_id


def test_a_closed_nstbrowser_postpones_and_a_signed_out_profile_is_marked(warm):
    service, sessions, clock, world, nst = warm
    [box] = add_boxes(service, "one@gmail.com")
    connect_partners(service, nst)
    service.warmup.update({"id": box, "pool_warmup": True})
    service.warmup.send_next()
    clock.advance(minutes=11)
    nst.offline = True
    assert service.warmup.handle_partners() == 0
    [row] = rows(sessions)
    assert row.status == "pending" and "Nstbrowser" in service.warmup.state()["nst"]["note"]
    nst.offline = False
    clock.advance(minutes=16)
    with sessions() as session:
        profile = session.get(EmailWarmupPartner, row.to_partner_id).profile_id
    nst.broken.add(profile)
    service.warmup.handle_partners()
    with sessions() as session:
        partner = session.get(EmailWarmupPartner, row.to_partner_id)
        assert partner.status == "error" and "вход" in partner.note
    assert rows(sessions)[0].status == "failed"


# ---------- transports ----------


class Imap:
    """IMAP of one Gmail account: INBOX, Spam and All Mail by special-use flags."""

    def __init__(self, spam: list[bytes]):
        self.folders = {"INBOX": [], "[Gmail]/Spam": list(spam), "[Gmail]/All Mail": []}
        self.current = None
        self.flags: list[str] = []

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"',
            b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
        ]

    def select(self, name, readonly=False):
        self.current = name.strip('"')
        return "OK", [b"1"]

    def uid(self, command, *args):
        box = self.folders[self.current]
        if command == "search":
            wanted = args[1].split('"')[1]
            hits = [str(i + 1) for i, raw in enumerate(box) if wanted.encode() in raw]
            return "OK", [" ".join(hits).encode()]
        index = int(args[0]) - 1
        if command == "move":
            self.folders[args[1].strip('"')].append(box.pop(index))
            return "OK", []
        if command == "fetch":
            return "OK", [(b"1 (BODY[] {1})", box[index])]
        if command == "store":
            self.flags.append(args[2])
            return "OK", []
        raise AssertionError(command)

    def logout(self):
        return "BYE", []


def test_imap_takes_a_warm_up_email_out_of_spam_and_archives_it(monkeypatch):
    raw = (
        b"From: One <one@gmail.com>\r\nTo: two@gmail.com\r\nSubject: quick one\r\n"
        b"Message-ID: <w1@gmail.com>\r\n\r\nhey there\r\n"
    )
    imap = Imap([raw])
    transport = mailer.GmailTransport("two@gmail.com", PASSWORD)
    monkeypatch.setattr(transport, "_imap", lambda: imap)
    result = transport.warm_handle("<w1@gmail.com>", None, star=True)
    assert result["placement"] == "spam" and result["message"].message_id == "<w1@gmail.com>"
    assert imap.folders["[Gmail]/Spam"] == [] and imap.folders["[Gmail]/All Mail"] == [raw]
    assert imap.flags == ["(\\Seen \\Flagged)"]
    assert transport.warm_handle("<nope@gmail.com>", None, star=False)["placement"] == "missing"


def test_nstbrowser_client_reads_profiles_and_errors():
    calls = []

    def opener(payload, status=200):
        def open_(request, timeout):
            calls.append((request.get_method(), request.full_url, request.headers))
            if status != 200:
                raise urllib.error.HTTPError(request.full_url, status, "no", {}, None)
            return io.BytesIO(json.dumps(payload).encode())

        return open_

    page = {"err": False, "data": {"docs": [{"profileId": "a1", "name": "Main"}]}}
    client = NstClient("secret", opener=opener(page))
    assert client.profiles() == [Profile("a1", "Main")]
    method, url, headers = calls[0]
    assert method == "GET" and url.startswith("http://localhost:8848/api/v2/profiles?page=1")
    assert headers["X-api-key"] == "secret"
    with pytest.raises(NstError) as error:
        NstClient("bad", opener=opener({}, status=401)).profiles()
    assert error.value.kind == "auth"
    started = NstClient(
        "k", opener=opener({"err": False, "data": {"webSocketDebuggerUrl": "ws://x"}})
    )
    assert started.start("a1") == "ws://x"
    with pytest.raises(NstError, match="Nstbrowser: busy"):
        NstClient("k", opener=opener({"err": True, "msg": "busy"})).start("a1")

    def offline(request, timeout):
        raise urllib.error.URLError("refused")

    with pytest.raises(NstError) as error:
        NstClient("k", opener=offline).profiles()
    assert error.value.kind == "offline"


def test_a_vpn_that_breaks_smtp_is_named_in_the_error(monkeypatch):
    class Broken:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, *args):
            raise mailer.smtplib.SMTPServerDisconnected(
                "Connection unexpectedly closed: [SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC]"
            )

        def close(self):
            pass

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", Broken)
    with pytest.raises(MailError) as error:
        mailer.GmailTransport("one@gmail.com", PASSWORD).verify()
    assert error.value.kind == "temporary" and "VPN" in error.value.detail
