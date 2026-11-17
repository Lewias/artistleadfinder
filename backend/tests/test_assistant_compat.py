"""The assistant on any model behind an OpenAI-compatible API: the request it sends, the
answer translated back into the stored blocks, and the provider settings."""

import json
import sys
from datetime import date
from types import SimpleNamespace

import pytest

from artist_lead_finder.assistant.bundled_provider import GO_URL, key_for
from artist_lead_finder.assistant.compat import CompatClient, from_chat, to_chat, to_functions
from artist_lead_finder.assistant.service import AssistantService
from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import Setting
from artist_lead_finder.service import ApplicationService

JAY = "+79991234567"
BASE = "https://openrouter.ai/api/v1"


class FakeApi:
    """A chat completions server that plays a script of answers."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.requests.append({"url": url, "headers": headers, "body": json.loads(body)})
        return json.dumps(self.answers.pop(0))


def answer(content=None, calls=(), finish="stop"):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
            for call_id, name, arguments in calls
        ]
    return {
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 50},
    }


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "assistant.db")
    service = ApplicationService(sessions, tmp_path / "data")
    service.crm.save(
        {"crm": "imessage", "name": "Jay", "channels": [{"kind": "phone", "value": JAY}]}
    )
    yield service, sessions, tmp_path
    service.shutdown()
    engine.dispose()


def helper_with(app, api):
    service, sessions, tmp_path = app
    return AssistantService(
        sessions,
        tmp_path / "data",
        service.crm,
        service.imessage,
        client_factory=lambda key: pytest.fail("Anthropic must not be called"),
        today=lambda: date(2026, 10, 10),
        core=service.call,
        compat_transport=api,
    )


def run(helper, question):
    helper.ask({"text": question})
    helper.thread.join(10)
    assert not helper.running()
    return helper.state({})


def test_any_model_runs_the_tool_loop_through_chat_completions(app):
    api = FakeApi(
        [
            answer(
                "Ищу Jay.",
                [("call_1", "crm_search", {"query": "Jay", "crm": "imessage", "limit": 5})],
                finish="tool_calls",
            ),
            answer("Jay есть в CRM iMessage."),
        ]
    )
    helper = helper_with(app, api)
    state = helper.set_model(
        {"provider": "compatible", "base_url": BASE, "main": "openai/gpt-5", "key": "sk-or-1"}
    )
    assert state["configured"] and state["models"]["main"] == "openai/gpt-5"
    assert state["provider"]["compatible_key"] and not state["provider"]["anthropic_key"]

    state = run(helper, "есть ли Jay?")
    assert state["error"] == ""
    assert [item["kind"] for item in state["items"]] == ["user", "assistant", "tool", "assistant"]

    first, second = (request["body"] for request in api.requests)
    assert api.requests[0]["url"] == f"{BASE}/chat/completions"
    assert api.requests[0]["headers"]["Authorization"] == "Bearer sk-or-1"
    assert first["model"] == "openai/gpt-5"
    assert first["messages"][0]["role"] == "system"
    assert first["messages"][1]["content"].startswith("[Сегодня 2026-10-10]\n")
    names = {tool["function"]["name"] for tool in first["tools"]}
    # No stronger model is set, so the hand-over tool is not offered.
    assert "crm_search" in names and "ask_stronger_model" not in names
    assert "Сильная модель не подключена" in first["messages"][0]["content"]
    # The call and its result went back as an assistant tool call and a tool message.
    assistant, result = second["messages"][2], second["messages"][3]
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert result["role"] == "tool" and result["tool_call_id"] == "call_1"
    assert "Jay" in result["content"]


def test_provider_settings_are_checked_and_a_switch_starts_a_new_chat(app):
    helper = helper_with(app, FakeApi([]))
    assert not helper.state({})["configured"]
    with pytest.raises(UserError, match="https://"):
        helper.set_model({"provider": "compatible", "base_url": "openrouter.ai", "main": "x"})
    with pytest.raises(UserError, match="модель"):
        helper.set_model({"provider": "compatible", "base_url": BASE, "main": ""})
    first = helper.conversation()
    helper.set_model({"provider": "compatible", "base_url": BASE, "main": "deepseek/deepseek-chat"})
    assert helper.conversation() == first + 1
    # Without a key a remote API is not ready; a local server needs none.
    assert not helper.state({})["configured"]
    helper.set_model(
        {"provider": "compatible", "base_url": "http://localhost:11434/v1", "main": "qwen3"}
    )
    assert helper.state({})["configured"]
    # Changing only the model keeps the conversation.
    current = helper.conversation()
    helper.set_model(
        {"provider": "compatible", "base_url": "http://localhost:11434/v1", "main": "llama3"}
    )
    assert helper.conversation() == current


def test_blocks_translate_both_ways():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "tool_use", "id": "t1", "name": "crm_get", "input": {"contact_id": 1}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "нет", "is_error": True}
            ],
        },
    ]
    chat = to_chat("system", messages)
    assert chat[0] == {"role": "system", "content": "system"}
    assert chat[2]["content"] is None and chat[2]["tool_calls"][0]["function"]["name"] == "crm_get"
    assert chat[3] == {"role": "tool", "tool_call_id": "t1", "content": "Ошибка: нет"}
    tools = to_functions([{"name": "x", "description": "d", "input_schema": {"type": "object"}}])
    assert tools[0]["function"] == {
        "name": "x",
        "description": "d",
        "parameters": {"type": "object"},
    }

    back = from_chat(answer("ok", [("c9", "crm_get", {"contact_id": 2})]), "test")
    assert back.stop_reason == "tool_use"
    assert back.content[1] == {
        "type": "tool_use",
        "id": "c9",
        "name": "crm_get",
        "input": {"contact_id": 2},
    }
    assert back.content[0].type == "text" and back.usage.input_tokens == 900
    with pytest.raises(UserError, match="unexpected|неожиданный"):
        from_chat({"error": {"message": "bad"}}, "test")


def test_opencode_session_survives_tools_turns_and_restart_but_changes_for_new_chat(app):
    api = FakeApi([
        answer(None, [("t1", "crm_search", {"query": "Jay", "crm": "imessage"})]),
        answer("Found Jay"), answer("Second turn"), answer("After restart"), answer("New chat"),
    ])
    helper = helper_with(app, api)
    helper.set_model({
        "provider": "compatible", "base_url": "https://opencode.ai/inference/go/openai/v1",
        "main": "claude-haiku-5-5", "key": "test-opencode-key",
    })
    for question in ["Find Jay", "Hello again"]:
        assert run(helper, question)["error"] == ""
    helper = helper_with(app, api)
    assert run(helper, "Hello after restart")["error"] == ""
    helper.new_chat({})
    assert run(helper, "Hello in new chat")["error"] == ""
    sessions = [r["headers"]["x-opencode-session"] for r in api.requests]
    assert len(sessions) == 5 and len(set(sessions[:4])) == 1
    assert sessions[4] != sessions[0]
    assert all(r["headers"]["User-Agent"].startswith("ArtistLeadFinder/") for r in api.requests)
    assert all(r["url"].endswith("/go/openai/v1/chat/completions") for r in api.requests)
    assert all(r["body"]["model"] == "claude-haiku-5-5" for r in api.requests)


def test_opencode_session_is_not_forwarded_to_other_services():
    api = FakeApi([answer("OK"), answer("OK"), answer("OK")])
    for url in [
        "https://opencode.ai/inference/go/openai/v1",
        "https://openrouter.ai/api/v1",
        "https://opencode.ai.example.org/v1",
    ]:
        CompatClient(url, "key", api, session_id="ses_test").create(
            model="test", max_tokens=16, messages=[{"role": "user", "content": "Hi"}],
        )
    assert api.requests[0]["headers"]["x-opencode-session"] == "ses_test"
    assert all("x-opencode-session" not in r["headers"] for r in api.requests[1:])


def test_new_user_can_use_bundled_go_without_a_key_and_state_never_exposes_it(app, monkeypatch):
    secret = "test-shared-credential"
    monkeypatch.setitem(sys.modules, "alf_bundled_provider", SimpleNamespace(OPENCODE_KEY=secret))
    api = FakeApi([answer("OK")])
    helper = helper_with(app, api)
    state = helper.state({})
    assert state["configured"] and state["provider"]["shared_key_available"]
    assert state["provider"]["base_url"] == GO_URL
    assert not state["provider"]["compatible_key"]
    assert secret not in json.dumps(state)
    assert run(helper, "Hello")["error"] == ""
    assert api.requests[0]["headers"]["Authorization"] == "Bearer " + secret
    # An old user's provider/model selection cannot override the ready connection.
    _, sessions, _ = app
    with sessions.begin() as session:
        session.merge(Setting(key="assistant_model", value={
            "provider": "compatible", "base_url": BASE, "main": "other", "strong": "paid-model"
        }))
    helper.compat_keys.save("old-personal-key")
    api.answers.append(answer("Still built in"))
    assert run(helper, "Hello again")["error"] == ""
    assert api.requests[-1]["headers"]["Authorization"] == "Bearer " + secret
    assert api.requests[-1]["body"]["model"] == "claude-haiku-5-5"
    assert helper.config()["base_url"] == GO_URL and helper.config()["strong"] == ""
    for method in ("assistant.model", "assistant.set_key"):
        with pytest.raises(UserError, match="подключён автоматически"):
            helper.call(method, {"provider": "anthropic", "key": "ignored"})


@pytest.mark.parametrize("url", [
    "https://opencode.ai.evil.example/inference/go/openai/v1",
    "http://opencode.ai/inference/go/openai/v1",
    "https://opencode.ai/inference/openai/v1",
    GO_URL + "?redirect=other",
])
def test_bundled_key_is_restricted_to_the_exact_go_endpoint(monkeypatch, url):
    monkeypatch.setitem(sys.modules, "alf_bundled_provider", SimpleNamespace(OPENCODE_KEY="secret"))
    assert key_for({"provider": "compatible", "base_url": url}) == ""
