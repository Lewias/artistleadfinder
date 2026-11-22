"""The assistant in Telegram: the link (a code, the state, unlink) and the bridge that takes
the linked chat's questions from the server, asks the assistant and writes the answer back.
The account server is a loopback fake, Claude a stand-in that plays a script; the app's
core, CRM and assistant are the real ones."""

from datetime import date

import pytest
from fake_supabase import FakeSupabase
from test_assistant import CHATS, JAY, KEY, FakeClaude, chat_db, reply, text, tool

from artist_lead_finder.account.client import SupabaseClient
from artist_lead_finder.account.runtime import Runtime
from artist_lead_finder.account.session import AccountService
from artist_lead_finder.assistant.service import AssistantService
from artist_lead_finder.assistant.telegram import plain
from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.service import ApplicationService


@pytest.fixture
def server():
    fake = FakeSupabase()
    yield fake
    fake.close()


class App:
    """A signed-in account with its real core; `script` is what Claude will say."""

    def __init__(self, server, tmp_path, email="a@example.com", role="admin", script=()):
        self.server = server
        self.claude = FakeClaude(script)
        self.engines = []
        self.user = server.add_user(email, role=role)
        server.add_key(f"KEY-{email}", self.user)
        account = AccountService(
            tmp_path / email,
            SupabaseClient(server.url, "anon"),
            protect=lambda data, decrypt=False: data,
        )
        self.runtime = Runtime(
            tmp_path / email, account, self.open, lambda: {"version": "test"}, background=False
        )
        self.runtime.start()
        self.runtime.call("account.login", {"email": email, "password": "secret-pass"})
        self.runtime.call("account.activate", {"key": f"KEY-{email}"})

    def open(self, folder):
        engine, sessions = open_database(folder / "app.db")
        service = ApplicationService(sessions, folder / "data")
        service.crm.save(
            {"crm": "imessage", "name": "Jay", "channels": [{"kind": "phone", "value": JAY}]}
        )
        service.assistant = AssistantService(
            sessions,
            folder / "data",
            service.crm,
            service.imessage,
            messages_db=chat_db(folder / "chat.db", CHATS),
            client_factory=lambda key: self.claude,
            error_text=lambda error: f"failed: {type(error).__name__}",
            today=lambda: date(2026, 11, 12),
            core=service.call,
            add_usernames=service.workspace.add_usernames,
        )
        service.assistant.set_key({"key": KEY})
        self.engines.append(engine)

        def close():
            service.shutdown()
            engine.dispose()

        return service, close

    @property
    def bridge(self):
        return self.runtime.bridge

    def link(self):
        self.server.tg_links[self.user] = "artist_hunter"

    def close(self):
        self.runtime.shutdown()


@pytest.fixture
def make(server, tmp_path):
    apps = []

    def build(**options):
        apps.append(App(server, tmp_path, **options))
        return apps[-1]

    yield build
    for app in apps:
        app.close()


def test_telegram_is_linked_with_a_code_and_unlinked(server, make):
    app = make()
    assert app.runtime.call("telegram.state", {})["linked"] is False
    started = app.runtime.call("telegram.link", {})
    assert started["url"] == f"https://t.me/alf_test_bot?start={started['code']}"
    app.link()
    assert app.runtime.call("telegram.state", {})["username"] == "artist_hunter"
    assert app.runtime.call("telegram.unlink", {})["linked"] is False
    # The cloud parser's calls are gone.
    for method in ("cloud.jobs", "cloud.start", "cloud.telegram"):
        with pytest.raises(UserError, match="Неизвестный метод"):
            app.runtime.call(method, {})


def test_without_the_account_server_there_is_no_telegram(tmp_path):
    account = AccountService(tmp_path, None, protect=lambda data, decrypt=False: data)
    runtime = Runtime(tmp_path, account, lambda folder: (None, lambda: None), lambda: {})
    with pytest.raises(UserError, match="сервер аккаунтов"):
        runtime.call("telegram.state", {})


def test_a_question_from_telegram_is_answered_by_the_assistant(server, make):
    app = make(script=[reply(text("**Ждут двое:**\n- Jay\n- Dre"), stop="end_turn")])
    app.link()
    request = server.tg_ask(app.user, "Кто ждёт ответа в iMessage?")
    assert app.bridge.run_once() == 1
    assert request["status"] == "answered"
    # Markdown the chat would show as asterisks is flattened.
    assert request["answer"] == "Ждут двое:\n• Jay\n• Dre"
    # Claude got the question, and the app's own chat shows the same conversation.
    asked = app.claude.requests[0]["messages"][-1]["content"][0]["text"]
    assert asked.endswith("Кто ждёт ответа в iMessage?")
    items = app.runtime.call("assistant.state", {})["items"]
    assert [item["kind"] for item in items] == ["user", "assistant"]
    # Nothing left to take.
    assert app.bridge.run_once() == 0


def test_imessage_drafts_wait_but_crm_actions_run_without_the_button(server, make):
    app = make(
        script=[
            reply(
                tool("draft_imessage", handle=JAY, text="yo want the pack", reason="ответ"),
                tool(
                    "propose_crm_contact",
                    crm="imessage",
                    name="Lil Test",
                    channels=[{"kind": "phone", "value": "+79994445566"}],
                    notes="",
                ),
            ),
            reply(text("Подготовил ответ Jay и создал контакт Lil Test."), stop="end_turn"),
        ]
    )
    app.link()
    request = server.tg_ask(app.user, "ответь Jay и заведи Lil Test")
    app.bridge.run_once()
    answer = request["answer"]
    assert answer.startswith("Подготовил ответ Jay и создал контакт Lil Test.")
    assert f"✉️ Черновик для Jay ({JAY}):\nyo want the pack" in answer
    assert "Черновики не отправлены" in answer
    assert "«Выполнить»" not in answer and "Lil Test" in answer
    # The contact is created at once; the iMessage draft remains unsent.
    state = app.runtime.call("assistant.state", {})
    assert [draft["text"] for draft in state["drafts"]] == ["yo want the pack"]
    assert [action["status"] for action in state["actions"]] == ["done"]
    service = app.runtime.service
    assert not service.imessage.state()["workspace"]["recipients"]
    assert service.crm.contacts({"crm": "imessage", "search": "Lil"})["items"]


def test_telegram_send_command_queues_imessage_for_iphone_without_a_button(server, make):
    app = make(
        script=[
            reply(tool("send_imessage", handle=JAY, text="yo want the pack", reason="ответ")),
            reply(text("Сообщение для Jay в очереди iPhone."), stop="end_turn"),
        ]
    )
    assistant = app.runtime.service.assistant
    assistant.mac_send.transport.enabled = False
    app.link()
    request = server.tg_ask(app.user, "Отправь Jay ответ в iMessage")

    assert app.bridge.run_once() == 1

    assert request["answer"] == "Сообщение для Jay в очереди iPhone."
    state = assistant.state({})
    assert state["drafts"] == []
    [message] = state["chat_drafts"]
    assert message["status"] == "queued" and message["campaign_id"] is not None
    [job] = app.runtime.service.imessage.state()["jobs"]
    assert (job["phone"], job["message"], job["status"]) == (JAY, "yo want the pack", "pending")


def test_new_chat_and_a_failed_run_are_reported(server, make):
    app = make(script=[reply(text("Раз."), stop="end_turn")])
    app.link()
    first = server.tg_ask(app.user, "раз")
    fresh = server.tg_ask(app.user, "/new")
    broken = server.tg_ask(app.user, "два")  # the script is over: Claude fails
    assert app.bridge.run_once() == 3
    assert first["answer"] == "Раз."
    assert "новый чат" in fresh["answer"]
    assert broken["answer"] == "⚠️ failed: IndexError"
    # The new chat holds only what came after «/new».
    items = app.runtime.call("assistant.state", {})["items"]
    assert [item["text"] for item in items] == ["два"]


def test_nothing_is_taken_without_a_link(server, make):
    app = make(script=[reply(text("Ответ."), stop="end_turn")])
    request = server.tg_ask(app.user, "вопрос")
    assert app.bridge.run_once() == 0 and request["status"] == "new"
    # «Подключить Telegram» was pressed: the link is noticed at the next round.
    app.runtime.call("telegram.link", {})
    app.link()
    assert app.bridge.run_once() == 1 and request["answer"] == "Ответ."

    # A plain user has the assistant too.
    answer = [reply(text("Да."), stop="end_turn")]
    plain_user = make(email="b@example.com", role="user", script=answer)
    plain_user.link()
    asked = server.tg_ask(plain_user.user, "вопрос")
    assert plain_user.bridge.run_once() == 1 and asked["answer"] == "Да."


def test_an_answer_the_server_missed_is_sent_when_it_is_back(server, make):
    app = make()
    app.link()

    def answer_while_the_server_goes_down(**request):
        server.down = True
        return reply(text("Ответ."), stop="end_turn")

    app.claude.messages.create = answer_while_the_server_goes_down
    request = server.tg_ask(app.user, "вопрос")
    app.bridge.run_once()
    assert request["status"] == "taken" and app.bridge.unsent
    server.down = False
    app.runtime.call("account.retry", {})
    app.runtime.tick()
    app.bridge.run_once()
    assert request["answer"] == "Ответ." and not app.bridge.unsent


def test_markdown_becomes_plain_text():
    source = "## Итог\n**Jay** ждёт `2 дня`\n- написать\n  * позвонить\n__важно__"
    assert plain(source) == "Итог\nJay ждёт 2 дня\n• написать\n  • позвонить\nважно"
