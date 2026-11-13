"""«Диалоги» iMessage: the Mac's chats read from a Messages database like the Mac's, and a
reply typed by the user sent once through a stand-in of Messages, with the same refusals as
an assistant draft. No browser window is involved."""

import pytest
import test_assistant
from test_assistant import CHATS, JAY, KID, STRANGER, chat_db
from test_send import FakeTransport

from artist_lead_finder.assistant.dialogs import REASON
from artist_lead_finder.assistant.messages_db import NOT_MAC, MessagesDb
from artist_lead_finder.assistant.service import AssistantService
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import AssistantDraft

app = test_assistant.app  # the real CRM: Jay (phone) and Kid («Не связываться»)

STRANGER_CHAT = [(False, "who is this", 30, False)]


def dialogs(app, transport=None, messages_db=None) -> tuple[AssistantService, FakeTransport]:
    service, sessions, tmp_path = app
    transport = transport or FakeTransport()
    helper = AssistantService(
        sessions,
        tmp_path / "data",
        service.crm,
        service.imessage,
        messages_db=messages_db
        or chat_db(tmp_path / "chat.db", {**CHATS, STRANGER: STRANGER_CHAT}),
        mac_transport=transport,
        send_background=lambda work: work(),
    )
    return helper, transport


def test_every_one_to_one_chat_is_listed_with_its_crm_contact(app):
    helper, _ = dialogs(app)
    state = helper.call("assistant.dialogs", {})
    assert state["messages"]["available"] is True
    threads = {item["handle"]: item for item in state["threads"]}
    assert set(threads) == {JAY, KID, STRANGER}  # no group chats
    assert threads[JAY]["contact"]["name"] == "Jay" and threads[JAY]["reply_block"] == ""
    assert "Не связываться" in threads[KID]["reply_block"]
    assert threads[STRANGER]["contact"] is None and "нет в CRM" in threads[STRANGER]["reply_block"]
    assert threads[JAY]["last_text"] == "И какие гарантии?" and not threads[JAY]["last_from_me"]
    chat = helper.call("assistant.dialog", {"handle": JAY})
    assert [m["text"] for m in chat["messages"]] == [
        "Привет! Послушал твой новый трек",
        "Спасибо) а сколько стоит продвижение?",
        "И какие гарантии?",
    ]
    assert chat["contact"]["name"] == "Jay"


def test_a_reply_goes_out_once_through_messages_and_refusals_send_nothing(app):
    _, sessions, _ = app
    helper, transport = dialogs(app)
    result = helper.call("assistant.dialog_reply", {"handle": JAY, "text": "  lets talk  "})
    assert transport.sent == [(JAY, "lets talk")]
    assert result["send"]["handle"] == JAY and result["send"]["text"] == "lets talk"
    with sessions() as session:
        [draft] = session.query(AssistantDraft).all()
        assert (draft.reason, draft.status, draft.model) == (REASON, "queued", "user")
    # The reply does not wait in the assistant's drafts.
    assert helper.state({})["drafts"] == []
    assert helper.call("assistant.dialogs", {})["sends"][0]["text"] == "lets talk"
    for handle, error in ((KID, "Не связываться"), (STRANGER, "нет в CRM"), ("@nick", "телефон")):
        with pytest.raises(UserError, match=error):
            helper.call("assistant.dialog_reply", {"handle": handle, "text": "yo"})
    with pytest.raises(UserError, match="Введите текст"):
        helper.call("assistant.dialog_reply", {"handle": JAY, "text": "   "})
    assert transport.sent == [(JAY, "lets talk")]


def test_a_reply_refused_by_the_send_limit_leaves_no_draft_behind(app, monkeypatch):
    _, sessions, _ = app
    helper, transport = dialogs(app)
    monkeypatch.setattr("artist_lead_finder.assistant.mac_send.SEND_LIMIT_PER_HOUR", 1)
    helper.call("assistant.dialog_reply", {"handle": JAY, "text": "one"})
    with pytest.raises(UserError, match="За последний час"):
        helper.call("assistant.dialog_reply", {"handle": JAY, "text": "two"})
    assert transport.sent == [(JAY, "one")]
    with sessions() as session:
        statuses = sorted(draft.status for draft in session.query(AssistantDraft))
    assert statuses == ["queued", "rejected"]
    assert helper.state({})["drafts"] == []


def test_off_the_mac_the_section_says_why_and_sends_nothing(app):
    helper, transport = dialogs(
        app, transport=FakeTransport(enabled=False), messages_db=MessagesDb(platform="win32")
    )
    state = helper.call("assistant.dialogs", {})
    assert state["messages"] == {"available": False, "reason": NOT_MAC}
    assert state["threads"] == [] and state["sending"]["enabled"] is False
    with pytest.raises(UserError, match="Только на Mac"):
        helper.call("assistant.dialog_reply", {"handle": JAY, "text": "yo"})
    assert transport.sent == []
