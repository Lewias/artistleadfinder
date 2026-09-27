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
