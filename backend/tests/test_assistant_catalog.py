"""The assistant reaches the whole app: every catalog method exists in the core, reading
runs at once, actions run only after «Выполнить», and the log is readable."""

import json
from pathlib import Path

import pytest
from test_assistant import assistant, reply, results_of, run, text, tool

from artist_lead_finder.assistant.app_catalog import CATALOG
from artist_lead_finder.assistant.app_tools import ToolError
from artist_lead_finder.database import open_database
from artist_lead_finder.service import ApplicationService

PACKAGE = Path(__file__).resolve().parents[1] / "artist_lead_finder"


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "catalog.db")
    service = ApplicationService(sessions, tmp_path / "data")
    yield service, sessions, tmp_path
    service.shutdown()
    engine.dispose()


def test_every_catalog_method_is_a_method_of_the_app():
    # The dispatch tables of the core, without the catalog itself.
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PACKAGE.rglob("*.py")
        if path.name != "app_catalog.py"
    )
    missing = [method for method in CATALOG if f'"{method}":' not in source]
    assert missing == []
    # No secrets, files, administration or internal methods.
    for method in CATALOG:
        assert not method.startswith(("admin.", "browser.", "assistant.", "telegram."))
        assert "internal" not in method and method not in {"ai.set_key", "ai.set_cloudflare"}
        assert method not in {"email.account_add", "email.account_update", "email.warmup_nst_save"}


def test_reading_methods_answer_on_a_fresh_app(app):
    helper, _ = assistant(app, [])
    for method, params in [
        ("dashboard.get", {}),
        ("leads.list", {"page_size": 5}),
        ("crm.list", {"crm": "email"}),
        ("email.state", {}),
        ("email.campaigns", {}),
        ("email.warmup_state", {}),
        ("spotify.state", {}),
        ("spotify.results", {"view": "skipped"}),
        ("imessage.state", {}),
        ("imessage.templates", {}),
        ("outreach.workspace", {}),
        ("scout.source_list", {}),
        ("autopilot.state", {}),
        ("settings.get", {}),
        ("system.info", {}),
    ]:
        assert CATALOG[method][1] == "read"
        result = helper.tool_app_read({"method": method, "params_json": json.dumps(params)})
        assert result is not None, method
    with pytest.raises(ToolError, match="propose_app_action"):
        helper.tool_app_read({"method": "email.start", "params_json": "{}"})
    with pytest.raises(ToolError, match="Нет такого метода"):
        helper.tool_app_read({"method": "admin.overview", "params_json": "{}"})
    assert set(helper.tool_app_functions({"area": "spotify"})) == {
        method for method, (area, _, _) in CATALOG.items() if area == "spotify"
    }


def test_the_model_finds_a_function_reads_and_executes_it_without_confirmation(app):
    service, _, _ = app
    contact = json.dumps(
        {"crm": "email", "name": "Mia", "channels": [{"kind": "email", "value": "mia@x.io"}]}
    )
    helper, claude = assistant(
        app,
        [
            reply(tool("app_functions", area="crm")),
            reply(tool("app_read", method="crm.list", params_json='{"crm": "email"}')),
            reply(
                tool(
                    "propose_app_action",
                    method="crm.save",
                    params_json=contact,
                    summary="Добавить Mia в CRM Email",
                ),
                tool(
                    "propose_app_action",
                    method="settings.save",
                    params_json='{"no_such_key": 1}',
                    summary="Сломать настройки",
                ),
            ),
            reply(text("Предложил добавить Mia — нажмите «Выполнить»."), stop="end_turn"),
        ],
    )
    state = run(helper, "добавь Mia в CRM почты")
    assert state["error"] == ""
    results = results_of(claude)
    assert "crm.save" in results[0]["content"]
    assert json.loads(results[1]["content"])["counts"]["all"] == 0
    assert results[3]["is_error"] and "settings.get" in results[3]["content"]
    [action] = state["actions"]
    assert action["kind"] == "app_call" and "Mia" in action["summary"]
    assert action["status"] == "done"
    assert service.call("crm.list", {"crm": "email"})["counts"]["all"] == 1
    # The email CRM is searchable by the dedicated tool too.
    found = helper.tool_crm_search({"crm": "all", "query": "mia", "status": "", "limit": 5})
    assert [item["crm"] for item in found["contacts"]] == ["email"]


def test_settings_change_runs_merged_with_the_current_ones(app):
    service, _, _ = app
    helper, _ = assistant(app, [])
    current = service.call("settings.get", {})
    key = next(name for name, value in current.items() if isinstance(value, int) and value > 1)
    proposal = helper.tool_propose_app_action(
        {
            "method": "settings.save",
            "params_json": json.dumps({key: current[key] - 1}),
            "summary": "Уменьшить значение",
        }
    )
    assert proposal["status"] == "done"
    after = service.call("settings.get", {})
    assert after[key] == current[key] - 1
    assert {name: value for name, value in after.items() if name != key} == {
        name: value for name, value in current.items() if name != key
    }


def test_instagram_warmup_settings_start_stop_and_history_are_available(app):
    from test_assistant import accounts

    service, _, _ = app
    main, _ = accounts(service)
    helper, _ = assistant(app, [])

    def action(method, params):
        return helper.tool_propose_app_action({
            "method": method, "params_json": json.dumps(params), "summary": "Прогрев"
        })

    assert action("instagram_warmup.settings", {
        "message": "saved text", "messages": 0, "likes": 1, "follows": 0
    })["status"] == "done"
    assert action("instagram_warmup.settings", {"likes": 2})["status"] == "done"
    assert service.instagram_warmup.state()["settings"]["message"] == "saved text"
    assert action("instagram_warmup.start", {"profile_id": main})["status"] == "done"
    assert service.instagram_warmup.state()["run"]["status"] == "running"
    assert action("instagram_warmup.stop", {})["status"] == "done"
    assert service.instagram_warmup.state()["run"]["status"] == "stopped"
    history = helper.tool_app_read({"method": "instagram_warmup.history", "params_json": "{}"})
    assert len(history) == 1 and history[0]["status"] == "stopped"


def test_imessage_catalog_actions_still_require_confirmation(app):
    service, _, _ = app
    helper, _ = assistant(app, [])
    result = helper.tool_propose_app_action({
        "method": "imessage.template_save",
        "params_json": json.dumps({"name": "Test", "parts": [{"text": "hello"}]}),
        "summary": "Создать шаблон iMessage",
    })
    assert service.call("imessage.templates", {}) == []
    [action] = helper.state({})["actions"]
    assert action["status"] == "pending"
    helper.action_run({"id": result["action_id"]})
    assert [item["name"] for item in service.call("imessage.templates", {})] == ["Test"]


def test_imessage_recipient_addition_via_crm_also_requires_confirmation(app):
    service, _, _ = app
    helper, _ = assistant(app, [])
    contact = service.crm.save({
        "crm": "imessage", "name": "Test", "channels": [{"kind": "phone", "value": "+79991112233"}]
    })
    result = helper.tool_propose_app_action({
        "method": "crm.write",
        "params_json": json.dumps({"crm": "imessage", "ids": [contact["id"]]}),
        "summary": "В получатели iMessage",
    })
    assert service.imessage.state()["workspace"]["recipients"] == []
    helper.action_run({"id": result["action_id"]})
    assert len(service.imessage.state()["workspace"]["recipients"]) == 1


def test_the_log_is_read_with_level_and_text_filters(app):
    service, _, _ = app
    helper, _ = assistant(app, [])
    folder = Path(service.call("system.info", {})["log_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        {"time": "t1", "level": "INFO", "event": "application_startup", "detail": None},
        {"time": "t2", "level": "WARNING", "event": "account_check_offline"},
        {"time": "t3", "level": "ERROR", "event": "email_send_failed", "reason": "auth"},
    ]
    (folder / "application.jsonl.1").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    (folder / "application.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows[1:]) + "\nnot json\n", encoding="utf-8"
    )
    everything = helper.tool_app_logs({"level": "", "contains": "", "limit": 10})["records"]
    assert [row["time"] for row in everything] == ["t1", "t2", "t3"]
    assert "detail" not in everything[0]
    errors = helper.tool_app_logs({"level": "WARNING", "contains": "email", "limit": 10})
    assert [row["event"] for row in errors["records"]] == ["email_send_failed"]
