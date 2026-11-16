"""The assistant as the operator: «Рассылка», the parser and «Автоведение» over the
real application service; Claude is the scripted stand-in of test_assistant."""

import json

import pytest
from test_assistant import accounts, assistant, reply, results_of, run, text, tool

from artist_lead_finder.assistant.app_tools import ToolError
from artist_lead_finder.assistant.duty import EVERY, SCOUT_EVERY
from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import AssistantAction, SearchJob
from artist_lead_finder.service import ApplicationService


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "operator.db")
    service = ApplicationService(sessions, tmp_path / "data")
    yield service, sessions, tmp_path
    service.shutdown()
    engine.dispose()


def target_of(service, profile_id) -> int:
    rows = {row["profile"]["id"]: row for row in service.call("scout.accounts", {})}
    return rows[profile_id]["target"]


def proposed(helper, *calls) -> dict[str, dict]:
    helper.client_factory("").script[:] = [reply(*calls), reply(text("Ок."), stop="end_turn")]
    state = run(helper, "сделай")
    assert state["error"] == ""
    return {action["kind"]: action for action in state["actions"]}


def test_the_operator_reads_and_runs_actions_without_confirmation(app):
    service, _, _ = app
    main, bare = accounts(service)
    helper, claude = assistant(app, [])
    actions = proposed(
        helper,
        tool("outreach_status"),
        tool("parser_status"),
        tool("parser_sources"),
        tool("app_settings"),
        tool("direct_findings"),
        tool("propose_parser_sources", action="add", urls=["@label.one", "instagram.com/two"]),
        tool("propose_settings", changes_json='{"outreach_daily_limit_per_sender": 7}'),
        tool("propose_outreach_senders", accounts=["main", "Bare"]),
        tool("propose_sender_status", account="Bare", status="paused"),
        tool("propose_parser_start", account="Main", target=40),
        tool("propose_settings", changes_json='{"api_key": "x"}'),
        tool("propose_parser_start", account="Nobody", target=40),
        tool("propose_parser_sources", action="add", urls=["https://example.com/x"]),
    )
    results = results_of(claude)
    assert all(not item.get("is_error") for item in results[:10])
    assert all(item.get("is_error") for item in results[10:])
    assert "Main" in results[11]["content"]  # an unknown account lists the real ones
    status = json.loads(results[0]["content"])
    assert status["list"] == {"by_status": {}} or status["list"]["total"] == 0
    assert main not in results[0]["content"] and main not in results[1]["content"]
    assert all(action["status"] == "done" for action in actions.values())
    done = {action["kind"]: action for action in helper.state({})["actions"]}
    assert all(action["status"] == "done" for action in done.values()), done
    assert len(service.call("scout.source_list", {})) == 2
    assert service.settings()["outreach_daily_limit_per_sender"] == 7
    assert service.workspace.state()["sender_ids"] == [main, bare]
    senders = {row["id"]: row["status"] for row in service.call("outreach.senders", {})}
    assert senders == {main: "active", bare: "paused"}
    # The parser start waits for the shell, with the goal counted from zero.
    assert target_of(service, main) == 40
    assert helper.call("assistant.shell_next_internal", {}) == {"kind": "scout", "profile_id": main}
    assert helper.call("assistant.shell_next_internal", {}) is None
    helper.call("assistant.shell_result_internal", {"profile_id": main, "error": "Нет источников"})
    assert helper.tool_parser_status({})["shell_answers"] == [
        {"account": "Main", "result": "Нет источников"}
    ]


def test_an_account_is_deleted_and_a_parser_control_needs_an_open_run(app):
    service, _, _ = app
    main, bare = accounts(service)
    helper, _ = assistant(app, [])
    actions = proposed(
        helper,
        tool("propose_account_delete", account="Bare"),
        tool("propose_parser_control", account="Main", action="pause"),
    )
    assert set(actions) == {"account_delete", "parser_control"}
    assert [profile["id"] for profile in service.call("browser.list", {})] == [main]
    failed = {action["kind"]: action for action in helper.state({})["actions"]}["parser_control"]
    assert failed["status"] == "failed" and "нет незавершённого" in failed["result"]


def test_queued_parser_start_can_be_cancelled_without_confirmation(app):
    service, _, _ = app
    main, _ = accounts(service)
    helper, _ = assistant(app, [])
    started = helper.tool_propose_parser_start({"account": "Main", "target": 10})
    assert started["status"] == "done" and target_of(service, main) == 10
    cancelled = helper.tool_propose_parser_control({"account": "Main", "action": "cancel"})
    assert cancelled["status"] == "done" and "отменён" in cancelled["result"]
    assert helper.call("assistant.shell_next_internal", {}) is None
    with pytest.raises(UserError):
        helper.action_run({"id": started["action_id"]})


def test_running_parser_is_cancelled_without_confirmation(app):
    service, sessions, _ = app
    main, _ = accounts(service)
    helper, _ = assistant(app, [])
    with sessions.begin() as session:
        job = SearchJob(name="Test", status="running")
        session.add(job)
        session.flush()
        job_id = job.id
    service.manager.states[job_id] = "running"
    original = helper.core

    def core(method, params):
        if method == "scout.accounts":
            return [{"profile": {"id": main}, "run": {"id": job_id, "status": "running"}}]
        return original(method, params)

    helper.core = core
    cancelled = helper.tool_propose_parser_control({"account": "Main", "action": "cancel"})
    assert cancelled["status"] == "done"
    with sessions() as session:
        assert session.get(SearchJob, job_id).status == "cancelled"


def test_browser_profile_can_be_created_with_proxy_without_leaking_it_into_actions(app):
    service, sessions, _ = app
    helper, _ = assistant(app, [])
    proxy = {"scheme": "http", "host": "proxy.example.com", "port": 8080,
             "username": "user", "password": "fixture-private-password"}
    result = helper.tool_browser_account_create({"name": "New", "proxy_json": json.dumps(proxy)})
    assert result["status"] == "done"
    [profile] = service.call("browser.list", {})
    assert profile["name"] == "New" and profile["cookie_count"] == 0
    assert service.browser_sessions.read(profile["id"])["proxy"] == proxy
    with sessions() as session:
        action = session.get(AssistantAction, result["action_id"])
        assert "fixture-private-password" not in json.dumps(action.params)
        assert "fixture-private-password" not in action.summary + action.result
    assert not helper.browser_changes
    with pytest.raises(ToolError, match="таким именем"):
        helper.tool_browser_account_create({"name": "new", "proxy_json": ""})


def test_proxy_change_reopens_only_selected_account_and_preserves_password_and_cookies(app):
    service, _, _ = app
    main, bare = accounts(service)
    helper, _ = assistant(app, [])
    original, runtime = helper.core, []

    def core(method, params):
        if method == "browser.runtime.is_open":
            return {"open": True}
        if method in {"browser.runtime.close", "browser.runtime.open"}:
            runtime.append((method, params["id"]))
            return {}
        return original(method, params)

    helper.core = core
    proxy = {"scheme": "socks5", "host": "proxy.example.com", "port": 1080,
             "username": "user", "password": "fixture-password"}
    result = helper.tool_browser_account_proxy({"account": "Main", "proxy_json": json.dumps(proxy)})
    assert result["status"] == "done"
    assert runtime == [("browser.runtime.close", main), ("browser.runtime.open", main)]
    assert service.browser_sessions.read(main)["cookies"]
    assert service.browser_sessions.read(bare)["proxy"] is None
    proxy.pop("password")
    proxy["port"] = 1081
    assert helper.tool_browser_account_proxy({
        "account": "Main", "proxy_json": json.dumps(proxy)
    })["status"] == "done"
    assert service.browser_sessions.read(main)["proxy"]["password"] == "fixture-password"
    assert helper.tool_browser_account_proxy({"account": "Main", "proxy_json": "null"})[
        "status"
    ] == "done"
    assert service.browser_sessions.read(main)["proxy"] is None


def test_proxy_cannot_be_changed_while_account_is_warming_up(app):
    service, _, _ = app
    main, _ = accounts(service)
    helper, _ = assistant(app, [])
    service.instagram_warmup.settings({"messages": 0, "likes": 1, "follows": 0})
    service.instagram_warmup.start({"profile_id": main})
    result = helper.tool_browser_account_proxy({"account": "Main", "proxy_json": "null"})
    assert result["status"] == "failed" and "занят" in result["result"]
    assert service.browser_sessions.read(main)["proxy"] is not None
    service.instagram_warmup.stop()


def test_duty_keeps_the_limits_starts_the_outreach_and_asks_for_the_parser(app):
    service, _, _ = app
    main, bare = accounts(service)
    helper, _ = assistant(app, [])
    clock = {"now": 1_000_000.0}
    helper.duty.now = lambda: clock["now"]
    service.workspace.update({"usernames": ["one", "two"], "messages": ["yo lets work"]})
    actions = proposed(
        helper,
        tool(
            "propose_duty",
            enabled=True,
            parser="Main",
            senders=["Bare"],
            daily_limit_per_sender=60,
            send_interval_seconds=600,
            queue_target=120,
        ),
    )
    assert actions["duty"]["status"] == "done"
    assert helper.call("assistant.shell_next_internal", {}) == {"kind": "scout", "profile_id": main}
    settings = service.settings()
    assert settings["outreach_daily_limit_per_sender"] == 60
    assert settings["outreach_send_interval_seconds"] == 600
    work = service.workspace.state()
    assert work["running"] and work["sender_ids"] == [bare]
    assert target_of(service, main) == 120
    view = helper.tool_parser_status({})["duty"]
    assert view["enabled"] and view["parser"] == "Main" and view["senders"] == ["Bare"]
    assert any("рассылка запущена" in line for line in view["log"])
    # The next rounds: not before five minutes, and the parser not more than once an hour.
    clock["now"] += EVERY - 1
    assert helper.call("assistant.shell_next_internal", {}) is None
    clock["now"] += EVERY
    assert helper.call("assistant.shell_next_internal", {}) is None
    clock["now"] += SCOUT_EVERY
    assert helper.call("assistant.shell_next_internal", {}) == {"kind": "scout", "profile_id": main}
    # Turned off: nothing more is started.
    off = proposed(
        helper,
        tool(
            "propose_duty",
            enabled=False,
            parser="",
            senders=[],
            daily_limit_per_sender=0,
            send_interval_seconds=0,
            queue_target=0,
        ),
    )
    assert off["duty"]["status"] == "done"
    clock["now"] += SCOUT_EVERY * 2
    assert helper.call("assistant.shell_next_internal", {}) is None
    with pytest.raises(UserError):
        helper.duty.config({"parser_id": main, "sender_ids": []})
