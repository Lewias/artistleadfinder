"""The producer's sales skills in the assistant, and sending a draft through Messages on
this Mac: confirmation, one send per draft, refusals and the status read back from a
Messages database like the Mac's. The transport is a stand-in: the real Messages app is
never used."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import test_assistant
from test_assistant import JAY, KEY, STRANGER, FakeClaude, reply, run, text, tool

from artist_lead_finder.assistant import mac_send, skills
from artist_lead_finder.assistant.mac_send import SEND_LIMIT_PER_HOUR, confirmation_token
from artist_lead_finder.assistant.messages_db import (
    MessagesDb,
    apple_value,
    handle_key,
)
from artist_lead_finder.assistant.service import AssistantService, system_prompt
from artist_lead_finder.errors import UserError
from artist_lead_finder.imessage.mac_sender import SendFailed, SendUnknown
from artist_lead_finder.models import AssistantDraft, AssistantSend

app = test_assistant.app  # the fixture: the real CRM with Jay and Kid

TEXT = "yo the hook on late nights stuck with me\nwant me to send a few that fit that sound"


# ---------- the skills ----------


def test_the_skill_library_is_the_producers_playbook():
    library = skills.library()
    names = {name for name, skill in library.items() if skill.kind == "skill"}
    assert names == {
        "closing",
        "cold-outreach",
        "conversation-analysis",
        "follow-up",
        "lead-qualification",
        "negotiation",
        "objection-handling",
    }
    references = {name for name, skill in library.items() if skill.kind == "reference"}
    assert references == {
        "deal-stages",
        "guardrails",
        "message-examples",
        "offers",
        "qualification",
        "voice",
    }
    # Plugin paths to references become load_skill names.
    closing = library["closing"].text
    assert "../../reference/" not in closing and "`offers` (load_skill)" in closing
    assert not closing.startswith("---")
    # The guardrails always go into the prompt; the rest is only listed.
    prompt = system_prompt()
    assert library["guardrails"].text in prompt
    assert "- closing (skill): " in prompt and "- offers (reference): " in prompt
    assert library["offers"].text not in prompt


def test_the_assistant_loads_a_skill_on_demand(app):
    service, sessions, tmp_path = app
    claude = FakeClaude(
        [
            reply(tool("load_skill", name="objection-handling")),
            reply(tool("load_skill", name="no-such-skill")),
            reply(text("Вот как ответить."), stop="end_turn"),
        ]
    )
    helper = AssistantService(
        sessions,
        tmp_path / "data",
        service.crm,
        service.imessage,
        messages_db=MessagesDb(tmp_path / "none.db"),
        client_factory=lambda key: claude,
        mac_transport=FakeTransport(),
    )
    helper.set_key({"key": KEY})
    state = run(helper, "он пишет too expensive, что ответить?")
    assert state["error"] == ""
    first = claude.requests[0]
    assert first["system"] == system_prompt()
    [load] = [item for item in first["tools"] if item["name"] == "load_skill"]
    assert "negotiation" in load["input_schema"]["properties"]["name"]["enum"]
    loaded = claude.requests[1]["messages"][-1]["content"][0]
    assert json.loads(loaded["content"])["text"] == skills.library()["objection-handling"].text
    missing = claude.requests[2]["messages"][-1]["content"][0]
    assert missing["is_error"] and "Нет скилла no-such-skill" in missing["content"]
    assert any(item["text"] == "Открываю скилл продаж" for item in state["items"])


# ---------- a Messages database and Messages itself, as stand-ins ----------


class ChatDb:
    """A chat.db with the flags Messages keeps for a message sent from this Mac."""

    def __init__(self, path: Path, flags: bool = True):
        self.path = path
        db = sqlite3.connect(path)
        columns = (
            ", is_sent integer, is_delivered integer, error integer, date_delivered integer"
            if flags
            else ""
        )
        db.executescript(
            f"""
            create table message (ROWID integer primary key, text text, attributedBody blob,
              date integer, is_from_me integer, service text, item_type integer default 0
              {columns});
            create table chat (ROWID integer primary key, chat_identifier text, style integer);
            create table chat_message_join (chat_id integer, message_id integer);
            """
        )
        db.commit()
        db.close()
        self.flags = flags
        # An older message with the same text must not be taken for the new one.
        self.add(JAY, TEXT, sent=True, delivered=True)

    def add(self, handle, body, sent=False, delivered=False, error=0) -> int:
        db = sqlite3.connect(self.path)
        chat = db.execute("select ROWID from chat where chat_identifier = ?", (handle,)).fetchone()
        chat_id = (
            chat[0]
            if chat
            else db.execute(
                "insert into chat (chat_identifier, style) values (?, 45)", (handle,)
            ).lastrowid
        )
        now = apple_value(datetime.now(timezone.utc))
        if self.flags:
            rowid = db.execute(
                "insert into message (text, date, is_from_me, service, is_sent, is_delivered, "
                "error, date_delivered) values (?, ?, 1, 'iMessage', ?, ?, ?, ?)",
                (body, now, int(sent), int(delivered), error, now if delivered else 0),
            ).lastrowid
        else:
            rowid = db.execute(
                "insert into message (text, date, is_from_me, service) "
                "values (?, ?, 1, 'iMessage')",
                (body, now),
            ).lastrowid
        db.execute("insert into chat_message_join values (?, ?)", (chat_id, rowid))
        db.commit()
        db.close()
        return rowid

    def update(self, rowid: int, **flags) -> None:
        db = sqlite3.connect(self.path)
        for column, value in flags.items():
            db.execute(f"update message set {column} = ? where ROWID = ?", (value, rowid))
        db.commit()
        db.close()


class FakeTransport:
    """Messages: records each send and, like the real app, writes the row to chat.db."""

    def __init__(self, chat: ChatDb | None = None, enabled=True, fail=None):
        self.chat = chat
        self.enabled = enabled
        self.fail = fail
        self.sent: list[tuple[str, str]] = []
        self.rows: list[int] = []

    def describe(self) -> str:
        return "Отправка через Сообщения." if self.enabled else "Только на Mac."

    def send(self, recipient: str, text: str) -> None:
        self.sent.append((recipient, text))
        if self.fail is not None:
            raise self.fail
        if self.chat is not None:
            # Messages writes the row with chat_identifier as it likes (no «+»).
            self.rows.append(self.chat.add(recipient.lstrip("+"), text))


@pytest.fixture
def mac(app):
    service, sessions, tmp_path = app
    chat = ChatDb(tmp_path / "chat.db")
    transport = FakeTransport(chat)
    helper = AssistantService(
        sessions,
        tmp_path / "data",
        service.crm,
        service.imessage,
        messages_db=MessagesDb(chat.path),
        mac_transport=transport,
        send_background=lambda work: work(),
    )
    return helper, transport, chat, sessions


def add_draft(sessions, handle=JAY, body=TEXT, status="pending") -> int:
    with sessions.begin() as session:
        draft = AssistantDraft(
            handle=handle,
            contact_id=1,
            contact_name="Jay",
            text=body,
            model="claude-haiku-5-5",
            status=status,
        )
        session.add(draft)
        session.flush()
        return draft.id


def confirm(helper, draft_id) -> dict:
    preview = helper.call("assistant.send_preview", {"id": draft_id})
    return helper.call("assistant.send_confirm", {"id": draft_id, "token": preview["token"]})


def draft_status(sessions, draft_id) -> str:
    with sessions() as session:
        return session.get(AssistantDraft, draft_id).status


# ---------- sending ----------


def test_a_confirmed_draft_goes_out_once_and_its_status_comes_from_chat_db(mac):
    helper, transport, chat, sessions = mac
    draft_id = add_draft(sessions)
    preview = helper.call("assistant.send_preview", {"id": draft_id})
    assert preview == {
        "draft_id": draft_id,
        "contact": "Jay",
        "recipient": JAY,
        "text": TEXT,
        "token": confirmation_token(JAY, TEXT),
    }
    assert transport.sent == []  # the preview sends nothing

    result = helper.call("assistant.send_confirm", {"id": draft_id, "token": preview["token"]})
    assert transport.sent == [(JAY, TEXT)]
    # Messages wrote the row but has not sent it yet; the old identical message is ignored.
    assert result["send"]["state"] == "dispatched"
    assert draft_status(sessions, draft_id) == "queued"
    assert result["drafts"] == []

    chat.update(transport.rows[0], is_sent=1)
    assert helper.state({})["sends"][0]["state"] == "sent"
    chat.update(
        transport.rows[0], is_delivered=1, date_delivered=apple_value(datetime.now(timezone.utc))
    )
    [send] = helper.state({})["sends"]
    assert send["state"] == "delivered" and send["delivered_at"]
    assert send["contact_name"] == "Jay" and send["handle"] == JAY

    # One send per draft, by either route.
    with pytest.raises(UserError, match="уже отправлялся"):
        confirm(helper, draft_id)
    with pytest.raises(UserError, match="уже отправлена или отклонена"):
        helper.call("assistant.drafts_send", {"ids": [draft_id]})
    assert len(transport.sent) == 1


def test_an_edited_text_needs_a_new_confirmation(mac):
    helper, transport, _chat, sessions = mac
    draft_id = add_draft(sessions)
    token = helper.call("assistant.send_preview", {"id": draft_id})["token"]
    helper.call("assistant.draft_update", {"id": draft_id, "text": "yo\nlmk"})
    with pytest.raises(UserError, match="изменился после подтверждения"):
        helper.call("assistant.send_confirm", {"id": draft_id, "token": token})
    with pytest.raises(UserError, match="изменился"):
        helper.call("assistant.send_confirm", {"id": draft_id, "token": ""})
    assert transport.sent == []


def test_who_may_not_be_written_to_is_refused(mac, app):
    helper, transport, _chat, sessions = mac
    service, _, _ = app
    # Not in the CRM, marked «Не связываться», not a phone or an email.
    stranger = add_draft(sessions, handle=STRANGER)
    with pytest.raises(UserError, match="нет в CRM"):
        helper.call("assistant.send_preview", {"id": stranger})
    service.crm.save(
        {
            "crm": "imessage",
            "id": 1,
            "name": "Jay",
            "channels": [{"kind": "phone", "value": JAY}],
            "statuses": ["Не связываться"],
        }
    )
    opted_out = add_draft(sessions)
    with pytest.raises(UserError, match="Не связываться"):
        helper.call("assistant.send_preview", {"id": opted_out})
    short_code = add_draft(sessions, handle="12345")
    with pytest.raises(UserError, match="не телефон и не email"):
        helper.call("assistant.send_preview", {"id": short_code})
    # Already in the iPhone queue, or rejected.
    queued = add_draft(sessions, status="queued")
    with pytest.raises(UserError, match="уже отправлен или отклонён"):
        helper.call("assistant.send_preview", {"id": queued})
    assert transport.sent == []


def test_at_most_twenty_sends_an_hour(mac):
    helper, transport, _chat, sessions = mac
    with sessions.begin() as session:
        for number in range(SEND_LIMIT_PER_HOUR):
            session.add(
                AssistantSend(
                    draft_id=1000 + number,
                    handle=JAY,
                    text="x",
                    token="t",
                    state="delivered",
                )
            )
    with pytest.raises(UserError, match="За последний час"):
        confirm(helper, add_draft(sessions))
    assert transport.sent == []


def test_a_failed_send_reopens_the_draft_and_may_be_retried(mac):
    helper, transport, _chat, sessions = mac
    draft_id = add_draft(sessions)
    transport.fail = SendFailed("macOS не разрешил приложению управлять Сообщениями.")
    result = confirm(helper, draft_id)
    assert result["send"]["state"] == "failed"
    assert "не разрешил" in result["send"]["note"]
    assert draft_status(sessions, draft_id) == "pending"
    transport.fail = None
    assert confirm(helper, draft_id)["send"]["state"] == "dispatched"
    assert len(transport.sent) == 2
    with sessions() as session:
        assert session.query(AssistantSend).count() == 1


def test_an_error_in_chat_db_fails_the_send(mac):
    helper, transport, chat, sessions = mac
    draft_id = add_draft(sessions)
    confirm(helper, draft_id)
    chat.update(transport.rows[0], error=22)
    [send] = helper.state({})["sends"]
    assert send["state"] == "failed" and "код 22" in send["note"]
    assert draft_status(sessions, draft_id) == "pending"


def test_no_answer_from_messages_is_uncertain_and_never_resent(mac):
    helper, transport, _chat, sessions = mac
    draft_id = add_draft(sessions)
    transport.fail = SendUnknown("Сообщения не ответили за 30 секунд.")
    result = confirm(helper, draft_id)
    assert result["send"]["state"] == "uncertain"
    assert "Проверьте переписку" in result["send"]["note"]
    assert draft_status(sessions, draft_id) == "queued"
    transport.fail = None
    with pytest.raises(UserError, match="уже отправлялся"):
        confirm(helper, draft_id)
    assert len(transport.sent) == 1


def test_a_send_missing_from_chat_db_becomes_uncertain(mac):
    helper, transport, _chat, sessions = mac
    transport.chat = None  # Messages took it but wrote nothing
    draft_id = add_draft(sessions)
    assert confirm(helper, draft_id)["send"]["state"] == "dispatched"
    assert helper.state({})["sends"][0]["state"] == "dispatched"
    later = datetime.now(timezone.utc) + mac_send.CONFIRM_WINDOW + timedelta(seconds=5)
    helper.mac_send.clock = lambda: later
    [send] = helper.state({})["sends"]
    assert send["state"] == "uncertain" and "не появилось" in send["note"]


def test_a_send_cut_off_while_queued_becomes_uncertain(mac):
    helper, transport, _chat, sessions = mac
    helper.mac_send.background = lambda work: None  # the app closed before Messages answered
    draft_id = add_draft(sessions)
    assert confirm(helper, draft_id)["send"]["state"] == "queued"
    later = datetime.now(timezone.utc) + mac_send.STALE_QUEUED + timedelta(seconds=5)
    helper.mac_send.clock = lambda: later
    assert helper.state({})["sends"][0]["state"] == "uncertain"
    assert transport.sent == []


def test_without_the_mac_or_chat_db_nothing_is_sent(mac, tmp_path):
    helper, transport, _chat, sessions = mac
    draft_id = add_draft(sessions)
    transport.enabled = False
    with pytest.raises(UserError, match="Только на Mac"):
        confirm(helper, draft_id)
    transport.enabled = True
    helper.mac_send.outbox = MessagesDb(tmp_path / "missing.db")
    token = confirmation_token(JAY, TEXT)
    with pytest.raises(UserError, match="Полный доступ к диску"):
        helper.call("assistant.send_confirm", {"id": draft_id, "token": token})
    assert transport.sent == [] and draft_status(sessions, draft_id) == "pending"
    assert helper.state({})["mac_send"] == {
        "enabled": True,
        "description": "Отправка через Сообщения.",
    }


def test_old_chat_db_without_flags_counts_the_row_as_sent(tmp_path):
    chat = ChatDb(tmp_path / "old.db", flags=False)
    db = MessagesDb(chat.path)
    after = db.max_rowid()
    rowid = chat.add("+7 999 123-45-67", "  yo   the  hook on " + TEXT[15:])
    found = db.find_outgoing(handle_key(JAY), TEXT, after)
    assert found is not None and found.rowid == rowid
    assert found.is_sent and not found.is_delivered and found.error == 0
    assert db.find_outgoing(handle_key("+79990000000"), TEXT, after) is None
    assert db.find_outgoing(handle_key(JAY), "something else", after) is None
    assert handle_key("Kid@Example.com") == "kid@example.com"


def test_a_draft_shows_as_a_card_under_the_answer_that_made_it(app):
    service, sessions, tmp_path = app
    claude = FakeClaude(
        [
            reply(tool("draft_imessage", handle=JAY, text=TEXT, reason="первое сообщение")),
            reply(tool("draft_imessage", handle=STRANGER, text="yo", reason="чужой")),
            reply(text("Готов черновик для Jay."), stop="end_turn"),
        ]
    )
    helper = AssistantService(
        sessions,
        tmp_path / "data",
        service.crm,
        service.imessage,
        messages_db=MessagesDb(tmp_path / "none.db"),
        client_factory=lambda key: claude,
        mac_transport=FakeTransport(),
    )
    helper.set_key({"key": KEY})
    state = run(helper, "напиши Jay")
    kinds = [item["kind"] for item in state["items"]]
    # Only the draft that was made gets a card, right after its step.
    assert kinds == ["user", "tool", "draft", "tool", "assistant"]
    draft_id = state["items"][2]["draft_id"]
    [card] = state["chat_drafts"]
    assert card["id"] == draft_id and card["status"] == "pending" and card["text"] == TEXT
    helper.call("assistant.draft_reject", {"id": draft_id})
    assert helper.state({})["chat_drafts"][0]["status"] == "rejected"
