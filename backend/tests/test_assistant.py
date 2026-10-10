"""«Ассистент»: Claude Haiku with tools over the real CRM, a Messages database like the
Mac's and the real iMessage queue; Claude itself is a stand-in that plays a script."""

import copy
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from artist_lead_finder.assistant.messages_db import (
    NOT_MAC,
    MessagesDb,
    apple_value,
    attributed_text,
)
from artist_lead_finder.assistant.service import (
    MODEL,
    STRONG_MODEL,
    AssistantService,
    cost,
)
from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import AssistantUsage, IMessageJob
from artist_lead_finder.service import ApplicationService

JAY = "+79991234567"
KID = "kid@example.com"
STRANGER = "+79990000000"
KEY = "sk-ant-api03-" + "x" * 40


# ---------- a Messages database like the Mac's ----------


def archived(text: str) -> bytes:
    """An NSAttributedString archive as newer macOS keeps it in message.attributedBody."""
    data = text.encode("utf-8")
    length = bytes([len(data)]) if len(data) < 0x80 else b"\x81" + len(data).to_bytes(2, "little")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
        + length
        + data
        + b"\x86\x84\x02iI\x01"
    )


def chat_db(path, chats: dict) -> MessagesDb:
    """chats: handle -> [(from_me, text, minutes ago, kept only in attributedBody)]."""
    db = sqlite3.connect(path)
    db.executescript(
        """
        create table message (ROWID integer primary key, text text, attributedBody blob,
          date integer, is_from_me integer, service text, item_type integer default 0);
        create table chat (ROWID integer primary key, chat_identifier text, style integer);
        create table chat_message_join (chat_id integer, message_id integer);
        """
    )
    now = datetime.now(timezone.utc)
    for handle, messages in chats.items():
        style = 43 if handle.startswith("group") else 45
        chat = db.execute(
            "insert into chat (chat_identifier, style) values (?, ?)", (handle, style)
        ).lastrowid
        for from_me, text, minutes, in_body in messages:
            message = db.execute(
                "insert into message (text, attributedBody, date, is_from_me, service) "
                "values (?, ?, ?, ?, 'iMessage')",
                (
                    None if in_body else text,
                    archived(text) if in_body else None,
                    apple_value(now - timedelta(minutes=minutes)),
                    int(from_me),
                ),
            ).lastrowid
            db.execute("insert into chat_message_join values (?, ?)", (chat, message))
    db.commit()
    db.close()
    return MessagesDb(path)


CHATS = {
    JAY: [
        (True, "Привет! Послушал твой новый трек", 300, False),
        (False, "Спасибо) а сколько стоит продвижение?", 120, True),
        (False, "И какие гарантии?", 100, False),
    ],
    KID: [(False, "hey", 50, False), (True, "Hi! Got your demo", 40, False)],
    "group-1": [(False, "всем привет", 10, False)],
}


def test_the_mac_history_is_read_with_texts_from_either_column(tmp_path):
    db = chat_db(tmp_path / "chat.db", CHATS)
    threads = db.threads(days=7)
    assert [item["handle"] for item in threads] == [KID, JAY]  # newest first, no groups
    jay = threads[1]
    assert jay["last_text"] == "И какие гарантии?" and not jay["last_from_me"]
    # Waiting since their first message after our last one.
    assert jay["waiting_since"] < jay["last_at"]
    assert threads[0]["waiting_since"] is None
    assert [item["handle"] for item in db.threads(days=7, unanswered=True)] == [JAY]
    thread = db.thread(JAY)
    assert [m["text"] for m in thread] == [
        "Привет! Послушал твой новый трек",
        "Спасибо) а сколько стоит продвижение?",
        "И какие гарантии?",
    ]
    assert [m["from_me"] for m in thread] == [True, False, False]
    long = "длинное сообщение " * 20
    assert attributed_text(archived(long)) == long
    assert attributed_text(b"no archive here") == ""


def test_off_the_mac_the_history_is_unavailable(tmp_path):
    db = MessagesDb(platform="win32")
    assert db.available() == {"available": False, "reason": NOT_MAC}
    with pytest.raises(UserError, match="только в приложении на Mac"):
        db.threads()


# ---------- Claude as a stand-in ----------


class Block(SimpleNamespace):
    def model_dump(self, mode=None, exclude_none=False):
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


def text(value):
    return Block(type="text", text=value, citations=None)


def tool(tool_name, /, **arguments):
    tool.count += 1
    return Block(type="tool_use", id=f"toolu_{tool.count}", name=tool_name, input=arguments)


tool.count = 0


def reply(*blocks, stop="tool_use", tokens=(1000, 200)):
    usage = SimpleNamespace(
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


class FakeClaude:
    def __init__(self, script, strong=None):
        self.script = list(script)
        self.strong = list(strong or [])
        self.requests: list[dict] = []
        self.strong_requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._strong))

    def _create(self, **request):
        self.requests.append(copy.deepcopy(request))
        return self.script.pop(0)

    def _strong(self, **request):
        self.strong_requests.append(copy.deepcopy(request))
        return self.strong.pop(0)


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "assistant.db")
    service = ApplicationService(sessions, tmp_path / "data")
    service.crm.save(
        {"crm": "imessage", "name": "Jay", "channels": [{"kind": "phone", "value": JAY}]}
    )
    service.crm.save(
        {
            "crm": "imessage",
            "name": "Kid",
            "channels": [{"kind": "email", "value": KID}],
            "statuses": ["Не связываться"],
        }
    )
    yield service, sessions, tmp_path
    service.shutdown()
    engine.dispose()


def assistant(app, script, strong=None) -> tuple[AssistantService, FakeClaude]:
    service, sessions, tmp_path = app
    claude = FakeClaude(script, strong)
    helper = AssistantService(
        sessions,
        tmp_path / "data",
        service.crm,
        service.imessage,
        messages_db=chat_db(tmp_path / f"chat-{tool.count}.db", CHATS),
        client_factory=lambda key: claude,
        error_text=lambda error: f"failed: {type(error).__name__}",
        today=lambda: date(2026, 10, 10),
        core=service.call,
        add_usernames=service.workspace.add_usernames,
    )
    helper.set_key({"key": KEY})
    return helper, claude


def run(helper, question):
    helper.ask({"text": question})
    helper.thread.join(10)
    assert not helper.running()
    return helper.state({})


def test_haiku_hands_a_hard_reply_to_sonnet_and_drafts_its_text(app):
    sonnet_text = "Понимаю вопрос про гарантии. Давай созвонимся на 10 минут — покажу кейсы."
    helper, claude = assistant(
        app,
        [
            reply(
                Block(type="thinking", thinking="", signature="sig-1"),
                tool("imessage_threads", days=7, only_waiting=True, limit=10),
            ),
            reply(tool("imessage_read", handle=JAY, limit=20)),
            reply(
                text("Тут вопрос про деньги и гарантии — отдам сильной модели."),
                tool(
                    "ask_stronger_model",
                    task="Ответить на вопрос о цене и гарантиях продвижения",
                    handle=JAY,
                    contact_id=1,
                ),
            ),
            reply(tool("draft_imessage", handle=JAY, text=sonnet_text, reason="ответ про цену")),
            reply(text("Готов черновик для Jay, его писала сильная модель."), stop="end_turn"),
        ],
        strong=[
            reply(text(sonnet_text + "\n---\nСнимаем страх, ведём к звонку."), stop="end_turn")
        ],
    )
    state = run(helper, "кто ждёт ответа? подготовь ответы")
    assert state["error"] == ""

    # The everyday model ran the loop; the stronger one was asked once, with the context.
    assert {request["model"] for request in claude.requests} == {MODEL}
    first = claude.requests[0]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["output_config"] == {"effort": "medium"}
    assert first["messages"][0]["content"][0]["text"].startswith("[Сегодня 2026-10-10]\n")
    [strong] = claude.strong_requests
    assert strong["model"] == STRONG_MODEL and strong["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in strong["betas"]
    context = strong["messages"][0]["content"]
    assert "гарантии" in context and "Jay" in context and "сколько стоит" in context
    # The thinking block went back unchanged, the tool results with it.
    second = claude.requests[1]["messages"]
    assert second[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
    threads = second[2]["content"][0]["content"]
    assert '"handle": "+79991234567"' in threads and '"name": "Jay"' in threads
    assert "group-1" not in threads

    [draft] = state["drafts"]
    assert draft["text"] == sonnet_text and draft["strong"] and draft["model"] == "Sonnet 5.5"
    assert draft["handle"] == JAY and draft["contact_name"] == "Jay"
    kinds = [item["kind"] for item in state["items"]]
    assert kinds[0] == "user" and "escalation" in kinds and kinds[-1] == "assistant"
    assert state["items"][0]["text"] == "кто ждёт ответа? подготовь ответы"
    usage = state["usage"]
    assert set(usage["by_model"]) == {"Haiku 5.5", "Sonnet 5.5"}
    assert usage["month"] == pytest.approx(5 * (1000 * 0.1 + 200 * 0.5) / 1e6 + 0.004, abs=1e-5)


def test_crm_changes_and_drafts_go_only_to_crm_contacts(app):
    service, _, _ = app
    helper, claude = assistant(
        app,
        [
            reply(tool("crm_search", crm="imessage", query="jay", status="", limit=5)),
            reply(
                tool(
                    "crm_update",
                    contact_id=1,
                    add_statuses=["Интересуется", "Горячий"],
                    remove_statuses=[],
                    note="",
                    next_action="",
                    next_action_date="",
                )
            ),
            reply(
                tool(
                    "crm_update",
                    contact_id=1,
                    add_statuses=["Интересуется"],
                    remove_statuses=[],
                    note="Спрашивал про цену",
                    next_action="Созвон",
                    next_action_date="2026-10-12",
                ),
                tool("draft_imessage", handle=STRANGER, text="Привет!", reason="знакомство"),
                tool("draft_imessage", handle=KID, text="Hi!", reason="follow-up"),
                tool("draft_imessage", handle="8 999 123-45-67", text="Созвонимся?", reason="x"),
            ),
            reply(text("Сделал."), stop="end_turn"),
        ],
    )
    state = run(helper, "отметь Jay как заинтересованного")
    results = {
        block["tool_use_id"]: block
        for request in claude.requests
        for message in request["messages"]
        if message["role"] == "user"
        for block in message["content"]
        if block.get("type") == "tool_result"
    }
    texts = [block["content"] for block in results.values()]
    assert any('"statuses_available"' in item and "Интересуется" in item for item in texts)
    assert any("Таких статусов нет: Горячий" in item for item in texts)
    assert any("нет в CRM" in item for item in texts)
    assert any("Не связываться" in item for item in texts)
    jay = service.crm.contacts({"crm": "imessage", "search": "jay"})["items"][0]
    assert jay["statuses"] == ["Интересуется"]
    assert jay["notes"] == "🤖 10.10.2026: Спрашивал про цену"
    assert jay["next_action"] == "Созвон" and jay["next_action_at"].startswith("2026-10-12")
    # A local number without the country code is not taken for a CRM phone.
    assert state["drafts"] == []


def test_sent_drafts_go_through_the_imessage_queue_once(app):
    service, sessions, _ = app
    helper, _ = assistant(
        app,
        [
            reply(tool("draft_imessage", handle=JAY, text="Созвонимся завтра?", reason="звонок")),
            reply(text("Черновик готов."), stop="end_turn"),
        ],
    )
    state = run(helper, "напиши Jay")
    [draft] = state["drafts"]
    assert not draft["strong"] and draft["model"] == "Haiku 5.5"
    helper.draft_update({"id": draft["id"], "text": "Созвонимся завтра в 15:00?"})
    sent = helper.drafts_send({"ids": [draft["id"]]})
    assert sent["drafts"] == []
    with sessions() as session:
        [job] = session.query(IMessageJob).all()
    assert job.phone == JAY and job.message == "Созвонимся завтра в 15:00?"
    assert job.campaign_id == sent["campaign_id"]
    with pytest.raises(UserError, match="уже отправлена"):
        helper.drafts_send({"ids": [draft["id"]]})


def test_the_conversation_goes_on_and_a_new_chat_starts_clean(app):
    helper, claude = assistant(
        app,
        [
            reply(text("Привет!"), stop="end_turn"),
            reply(text("Ты спрашивал про Jay."), stop="end_turn"),
            reply(text("Новый разговор."), stop="end_turn"),
        ],
    )
    run(helper, "привет")
    run(helper, "о чём я спрашивал?")
    assert len(claude.requests[1]["messages"]) == 3
    helper.new_chat({})
    state = run(helper, "начнём заново")
    assert len(claude.requests[2]["messages"]) == 1
    assert [item["text"] for item in state["items"]] == ["начнём заново", "Новый разговор."]


def test_without_escalation_haiku_answers_itself_and_errors_are_plain(app):
    helper, claude = assistant(
        app,
        [
            reply(tool("ask_stronger_model", task="ответ", handle="", contact_id=0)),
            reply(text("Отвечу сам."), stop="end_turn"),
        ],
    )
    helper.save_settings({"escalation": False})
    run(helper, "ответь")
    result = claude.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "выключен" in result["content"]
    assert claude.strong_requests == []

    class Broken(FakeClaude):
        def _create(self, **request):
            raise ConnectionError("down")

    helper.client_factory = lambda key: Broken([])
    state = run(helper, "ещё раз")
    assert state["error"] == "failed: ConnectionError" and not state["running"]


def test_the_key_is_checked_and_kept_encrypted(app):
    _, _, tmp_path = app
    helper, _ = assistant(app, [])
    assert helper.state({})["configured"]
    vault = (tmp_path / "data" / "anthropic-key.vault").read_bytes()
    assert KEY.encode() not in vault and helper.keys.load() == KEY
    with pytest.raises(UserError, match="sk-ant-"):
        helper.set_key({"key": "not-a-key"})
    helper.set_key({"key": ""})
    with pytest.raises(UserError, match="ключ Anthropic"):
        helper.ask({"text": "привет"})


def test_costs_follow_the_price_list():
    usage = SimpleNamespace(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=0,
    )
    # Past 100K tokens of prompt Haiku 5.5 costs more.
    assert cost(MODEL, usage)[1] == pytest.approx(0.5 + 0.05 + 0.25)
    small = SimpleNamespace(
        input_tokens=10_000,
        output_tokens=1_000,
        cache_read_input_tokens=50_000,
        cache_creation_input_tokens=10_000,
    )
    assert cost(MODEL, small)[1] == pytest.approx((1000 + 500 + 1250 + 500) / 1e6)
    assert cost(STRONG_MODEL, small)[1] == pytest.approx((20000 + 10000 + 25000 + 10000) / 1e6)
    assert AssistantUsage.__tablename__ == "assistant_usage"


# ---------- the rest of the app: reading and proposed actions ----------


class FakeCloud:
    """The signed-in account's cloud jobs, as the account runtime answers them."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "cloud.jobs":
            return [
                {
                    "kind": "scout",
                    "stage": "completed",
                    "created_at": "2026-10-10T10:00:00+00:00",
                    "params": {"target": 30, "sources": ["a", "b"]},
                    "counters": {"found": 12, "added": 9},
                    "progress": {"found": 12},
                    "error": None,
                }
            ]
        if method == "cloud.outreach_sources":
            return {"list": 0, "found": 7, "messages": 2}
        return {"job": {"params": {"usernames": ["a", "b", "c"]}}}


def accounts(service):
    proxy = {"scheme": "http", "host": "1.2.3.4", "port": 8000}
    main = service.call("browser.create", {"name": "Main", "proxy": proxy})["id"]
    bare = service.call("browser.create", {"name": "Bare"})["id"]
    for identifier in (main, bare):
        record = service.browser_sessions.read(identifier)
        record["cookies"] = [{"name": "sessionid", "value": "s", "domain": ".instagram.com"}]
        service.browser_sessions.write(identifier, record)
    return main, bare


def results_of(claude) -> list[dict]:
    return [
        block
        for message in claude.requests[-1]["messages"]
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]


def test_haiku_reads_the_app_and_proposes_actions_the_user_runs(app):
    service, _, _ = app
    main, _ = accounts(service)
    calls = [
        tool("accounts"),
        tool("app_stats"),
        tool("leads_search", query="", status="", min_followers=0, sort="score", limit=5),
        tool("search_history", limit=5),
        tool("cloud_jobs"),
        tool("imessage_campaigns"),
        tool("propose_cloud_search", account="main", target=40, categories=["ARTIST"]),
        tool("propose_cloud_outreach", account="Bare", source="found", limit=5),
        tool("propose_cloud_outreach", account="Main", source="found", limit=5),
        tool("propose_instagram_list_add", usernames=["@Jay.Carter", "bad name"], reason="тест"),
        tool("propose_imessage_list_add", handles=["+79991112233", "8 999"], reason=""),
        tool(
            "propose_crm_contact",
            crm="imessage",
            name="Lil Test",
            channels=[{"kind": "phone", "value": "+79994445566"}],
            notes="с сайта",
        ),
    ]
    helper, claude = assistant(app, [reply(*calls), reply(text("Предложил."), stop="end_turn")])
    cloud = FakeCloud()
    helper.cloud = cloud
    real_core = helper.core
    helper.core = lambda method, params: (
        [{"username": "rapgoat.tv", "enabled": True}, {"username": "off", "enabled": False}]
        if method == "scout.source_list"
        else real_core(method, params)
    )
    state = run(helper, "найди 40 артистов в облаке и напиши новым")
    assert state["error"] == ""
    results = results_of(claude)
    texts = [item["content"] for item in results]
    assert '"proxy": true' in texts[0] and '"name": "Bare"' in texts[0] and main not in texts[0]
    assert '"total": 0' in texts[1] and '"stage": "completed"' in texts[4]
    assert "нет прокси" in texts[7] and results[7]["is_error"]
    # Nothing ran yet: every action waits for «Выполнить».
    assert [call[0] for call in cloud.calls] == ["cloud.jobs", "cloud.outreach_sources"]
    actions = {action["kind"]: action for action in state["actions"]}
    assert set(actions) == {
        "cloud_search",
        "cloud_outreach",
        "instagram_list_add",
        "imessage_list_add",
        "crm_contact",
    }
    assert all(action["status"] == "pending" for action in actions.values())
    assert "40 лидов с аккаунта «Main», 1 источников" in actions["cloud_search"]["summary"]
    assert "5 новым лидам облачного парсера" in actions["cloud_outreach"]["summary"]

    state = helper.action_run({"id": actions["cloud_search"]["id"]})
    method, params = cloud.calls[-1]
    assert method == "cloud.start" and params["sources"] == "rapgoat.tv"
    assert params["profile_id"] == main and params["target"] == 40
    assert params["categories"] == ["ARTIST"] and params["request_id"].startswith("assistant-")
    helper.action_run({"id": actions["cloud_outreach"]["id"]})
    method, params = cloud.calls[-1]
    assert method == "cloud.outreach_start" and params["source"] == "found"
    assert params["limit"] == 5
    helper.action_run({"id": actions["instagram_list_add"]["id"]})
    assert [item["username"] for item in service.workspace.state()["usernames"]] == ["jay.carter"]
    helper.action_run({"id": actions["imessage_list_add"]["id"]})
    phones = [item["phone"] for item in service.imessage.state()["workspace"]["recipients"]]
    assert phones == ["+79991112233"]
    helper.action_reject({"id": actions["crm_contact"]["id"]})
    with pytest.raises(UserError, match="уже выполнено или отклонено"):
        helper.action_run({"id": actions["crm_contact"]["id"]})
    assert not service.crm.contacts({"crm": "imessage", "search": "Lil"})["items"]
    done = {action["kind"]: action for action in helper.state({})["actions"]}
    assert done["cloud_search"]["status"] == "done" and "запущен" in done["cloud_search"]["result"]
    assert "crm_contact" not in done  # rejected ones leave the list


def test_a_contact_proposal_creates_it_and_cloud_needs_a_sign_in(app):
    service, _, _ = app
    accounts(service)
    helper, claude = assistant(
        app,
        [
            reply(
                tool(
                    "propose_crm_contact",
                    crm="imessage",
                    name="Lil Test",
                    channels=[{"kind": "phone", "value": "+79994445566"}],
                    notes="",
                ),
                tool("propose_cloud_search", account="Main", target=10, categories=[]),
                tool("cloud_jobs"),
            ),
            reply(text("Ок."), stop="end_turn"),
        ],
    )
    state = run(helper, "добавь контакт")
    texts = [item["content"] for item in results_of(claude)]
    assert "вход в аккаунт" in texts[1] and "вход в аккаунт" in texts[2]
    [action] = state["actions"]
    helper.action_run({"id": action["id"]})
    [contact] = service.crm.contacts({"crm": "imessage", "search": "Lil"})["items"]
    assert contact["channels"] == [{"kind": "phone", "value": "+79994445566"}]
    # A second press does nothing more.
    with pytest.raises(UserError):
        helper.action_run({"id": action["id"]})


def test_a_failed_action_says_why(app):
    service, _, _ = app
    helper, _ = assistant(
        app,
        [
            reply(tool("propose_imessage_list_add", handles=[JAY], reason="")),
            reply(text("Ок."), stop="end_turn"),
        ],
    )
    state = run(helper, "добавь Jay в рассылку")
    [action] = state["actions"]

    def broken(values):
        raise UserError("Список получателей заполнен.")

    service.imessage.add_recipients = broken
    state = helper.action_run({"id": action["id"]})
    [action] = state["actions"]
    assert action["status"] == "failed" and action["result"] == "Список получателей заполнен."
