import json
import logging

from artist_lead_finder.rpc import JsonLogFormatter, failure_context


def test_error_log_names_method_and_raise_site_but_not_message():
    try:
        raise ValueError("secret proxy password")
    except ValueError as error:
        context = failure_context("browser.runtime.navigate", error)
    record = logging.LogRecord("t", logging.WARNING, __file__, 1, "invalid_request", None, None)
    record.__dict__.update(context)
    line = JsonLogFormatter().format(record)
    entry = json.loads(line)
    assert entry["method"] == "browser.runtime.navigate"
    assert entry["error_type"] == "ValueError"
    assert entry["where"].startswith("test_rpc.py:")
    assert "secret" not in line


def test_user_errors_reach_the_ui_and_other_errors_stay_generic(tmp_path, monkeypatch):
    import io
    import sys

    from artist_lead_finder import rpc
    from artist_lead_finder.database import open_database
    from artist_lead_finder.errors import UserError

    class Service:
        def __init__(self, *args):
            self.chromium = type("Chromium", (), {"prepare": lambda self: None})()

        def call(self, method, params):
            if method == "user":
                raise UserError("Некому отправлять: все номера уже получили сообщение.")
            raise ValueError("C:/secret/path invalid literal")

        def shutdown(self):
            pass

    monkeypatch.setattr(rpc, "application_data_dir", lambda: tmp_path)
    monkeypatch.setattr(rpc, "open_database", lambda *_: open_database(tmp_path / "db.sqlite3"))
    monkeypatch.setattr(rpc, "ApplicationService", Service)
    requests = b"".join(
        json.dumps({"id": n, "method": m, "params": {}}).encode() + b"\n"
        for n, m in ((1, "user"), (2, "other"))
    )
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(requests)))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    rpc.run()
    first, second = (json.loads(line) for line in output.getvalue().splitlines())
    assert first["error"] == "Некому отправлять: все номера уже получили сообщение."
    assert "secret" not in second["error"]


def test_generic_errors_get_a_plain_reason_and_a_logged_code(caplog):
    import re

    from pydantic import BaseModel, field_validator

    from artist_lead_finder.errors import UserError
    from artist_lead_finder.rpc import describe_error

    def raised(error):
        try:
            raise error
        except Exception as caught:
            return caught

    with caplog.at_level(logging.WARNING):
        text = describe_error("crm.export", raised(PermissionError("C:/Users/me/a.xlsx")))
    code = re.search(r"код ([0-9a-f]{6})", text).group(1)
    assert "другой программе" in text and "Users" not in text
    assert caplog.records[-1].ref == code
    assert "Файл или папка" in describe_error("x", raised(FileNotFoundError("/tmp/x")))
    assert "Некорректные данные" in describe_error("x", raised(KeyError("id")))

    class Form(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def check(cls, value):
            raise UserError("Название — от 1 до 120 символов.")

    try:
        Form(name="x")
    except Exception as error:
        assert describe_error("x", error) == "Название — от 1 до 120 символов."
    try:
        Form()
    except Exception as error:
        assert describe_error("x", error) == "Проверьте формат и диапазоны полей (поле name)."


def test_core_that_cannot_start_still_says_why(tmp_path, monkeypatch):
    import io
    import sys

    from artist_lead_finder import rpc
    from artist_lead_finder.database import NewerDatabaseError

    def newer(*_):
        raise NewerDatabaseError("База данных создана более новой версией Artist Lead Finder.")

    monkeypatch.setattr(rpc, "application_data_dir", lambda: tmp_path)
    monkeypatch.setattr(rpc, "open_database", newer)
    requests = b"".join(
        json.dumps({"id": n, "method": m, "params": {}}).encode() + b"\n"
        for n, m in ((1, "system.info"), (2, "system.shutdown"))
    )
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(requests)))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    rpc.run()
    first, second = (json.loads(line) for line in output.getvalue().splitlines())
    assert first == {
        "id": 1,
        "error": "База данных создана более новой версией Artist Lead Finder.",
    }
    assert second == {"id": 2, "result": {"ok": True}}


def test_user_error_text_is_logged_for_support(caplog):
    from artist_lead_finder.errors import UserError
    from artist_lead_finder.rpc import describe_error

    with caplog.at_level(logging.WARNING):
        describe_error("scout.start_internal", UserError("Добавьте источники."))
    record = caplog.records[-1]
    entry = json.loads(JsonLogFormatter().format(record))
    assert (entry["event"], entry["method"]) == ("user_error", "scout.start_internal")
    assert entry["detail"] == "Добавьте источники."
