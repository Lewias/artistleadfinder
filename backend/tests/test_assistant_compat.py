"""The assistant on any model behind an OpenAI-compatible API: the request it sends, the
answer translated back into the stored blocks, and the provider settings."""

import json
from datetime import date

import pytest

from artist_lead_finder.assistant.compat import from_chat, to_chat, to_functions
from artist_lead_finder.assistant.service import AssistantService
from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
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
