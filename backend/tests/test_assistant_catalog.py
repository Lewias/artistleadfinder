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


def test_the_model_finds_a_function_reads_and_proposes_and_the_press_runs_it(app):
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
    [action] = [item for item in state["actions"] if item["status"] == "pending"]
    assert action["kind"] == "app_call" and "Mia" in action["summary"]
    # Nothing happened before the press.
    assert service.call("crm.list", {"crm": "email"})["counts"]["all"] == 0
    after = helper.action_run({"id": action["id"]})
    [done] = [item for item in after["actions"] if item["id"] == action["id"]]
    assert done["status"] == "done"
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
    helper.action_run({"id": proposal["action_id"]})
    after = service.call("settings.get", {})
    assert after[key] == current[key] - 1
    assert {name: value for name, value in after.items() if name != key} == {
        name: value for name, value in current.items() if name != key
    }


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
