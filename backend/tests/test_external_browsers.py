import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from artist_lead_finder import browser_sessions, external_browsers
from artist_lead_finder.browser_sessions import BrowserSessions
from artist_lead_finder.chromium_runtime import BrowserLaunchError, ChromiumRuntime
from artist_lead_finder.errors import UserError
from artist_lead_finder.external_browsers import ProfileClient, provider_fields


@pytest.fixture
def store(tmp_path, monkeypatch):
    def test_cipher(data, decrypt=False):
        return bytes(byte ^ 0xA5 for byte in data)

    monkeypatch.setattr(browser_sessions, "protect", test_cipher)
    return BrowserSessions(tmp_path)


@pytest.mark.parametrize("provider", ["nst", "dolphin"])
def test_list_and_link_profiles_without_exposing_secrets(store, monkeypatch, provider):
    token = "fixture-token"
    remote_id = "7"
    params = {"provider": provider, "token": token}
    calls = []

    def reply(request, timeout):
        calls.append(request)
        cursor = parse_qs(urlsplit(request.full_url).query).get("cursor")
        if provider == "nst":
            data = {
                "docs": [
                    {"profileId": remote_id, "name": "Test", "proxyConfig": {"password": "secret"}}
                ],
                "hasMore": not cursor,
                "nextCursor": "next",
            }
            payload = {"code": 200, "err": False, "data": data}
        else:
            payload = {
                "data": [{"id": 7, "name": "Test", "proxy": {"password": "secret"}}],
                "next_page_url": None if cursor else "https://example.invalid/?cursor=next",
            }
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(external_browsers._opener, "open", reply)
    first = store.call("browser.external_list", params)
    final = store.call("browser.external_list", {**params, "cursor": first["cursor"]})
    assert first == {"profiles": [{"id": remote_id, "name": "Test"}], "cursor": "next"}
    assert final["cursor"] is None
    request = calls[-1]
    if provider == "nst":
        assert request.full_url.startswith("http://127.0.0.1:8848/api/v2/profiles/cursor?")
        assert request.get_header("X-api-key") == token
    else:
        assert request.full_url.startswith(
            "https://dolphin-anty-api.com/browser_profiles/list-cursor?"
        )
        assert request.get_header("Authorization") == f"Bearer {token}"
    selected = {**params, "profiles": first["profiles"]}
    assert store.call("browser.external_add", selected) == {"added": 1, "skipped": 0}
    assert store.call("browser.external_add", selected) == {"added": 0, "skipped": 1}
    (profile,) = store.call("browser.list", {})
    assert profile["external"]["id"] == remote_id
    assert token not in json.dumps(profile)
    identifier = profile["id"]
    assert token.encode() not in store.path(identifier).read_bytes()
    assert store.read(identifier)["external"]["token"] == (token if provider == "nst" else "")
    assert store.call("browser.load_internal", {"id": identifier}) == {"proxy": None}
    store.call("browser.delete", {"id": identifier})
    assert len(calls) == 2


def test_dolphin_start_uses_signed_in_local_client_and_refuses_duplicates(monkeypatch):
    connection = provider_fields({"provider": "dolphin", "port": 3002, "token": "test"})
    client = ProfileClient(connection)
    calls = []

    def reply(request, timeout):
        calls.append(request)
        if len(calls) > 1:
            raise HTTPError(request.full_url, 409, "busy", {}, io.BytesIO(b"{}"))
        return io.BytesIO(
            b'{"success":true,"automation":{"port":9222,"wsEndpoint":"/devtools/browser/test"}}'
        )

    monkeypatch.setattr(external_browsers._opener, "open", reply)
    assert client.open_profile("7") == ("ws://127.0.0.1:9222/devtools/browser/test", True)
    assert calls[0].full_url == "http://127.0.0.1:3002/v1.0/browser_profiles/7/start"
    assert calls[0].get_header("Authorization") is None
    assert json.loads(calls[0].data) == {"automation": True}
    with pytest.raises(UserError, match="занят"):
        client.open_profile("7")
    assert len(calls) == 2  # A conflict must never close the user's window.


@pytest.mark.parametrize("running", [False, True])
def test_nst_runtime_preserves_original_session_and_window(store, monkeypatch, running):
    store.call(
        "browser.external_add",
        {"provider": "nst", "token": "test", "profiles": [{"id": "abc", "name": "NST"}]},
    )
    (profile,) = store.call("browser.list", {})
    identifier = profile["id"]

    calls = []

    def reply(request, timeout):
        path = urlsplit(request.full_url).path
        calls.append((request.method, path))
        data = (
            [{"profileId": "abc", "running": running}]
            if path.endswith("/browsers")
            else {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/test"}
        )
        return io.BytesIO(json.dumps({"code": 0, "data": data}).encode())

    monkeypatch.setattr(external_browsers._opener, "open", reply)
    runtime = ChromiumRuntime(store)
    context, browser, page, existing = (Mock() for _ in range(4))
    existing.is_closed.return_value = page.is_closed.return_value = False
    browser.is_connected.return_value = True
    context.pages = [existing]
    context.new_page.return_value = page
    context.cookies.return_value = [{"name": "sessionid", "value": "fixture-session-value"}]
    browser.contexts = [context]
    engine = Mock()
    engine.connect_over_cdp.return_value = browser
    monkeypatch.setattr(runtime, "_engine", lambda: engine)
    monkeypatch.setattr(runtime, "_ready", Mock(side_effect=AssertionError("bundled Chromium")))
    try:
        assert runtime.open(identifier)["ok"]
        assert runtime.open(identifier)["reused"]
        engine.launch.assert_not_called()
        engine.connect_over_cdp.assert_called_once()
        assert store.call("browser.list", {})[0]["cookie_count"] == 1
        record = store.read(identifier)
        assert record["cookies"] == []
        assert "fixture-session-value" not in json.dumps(record)
    finally:
        runtime.shutdown()
    existing.close.assert_not_called()
    if running:
        assert calls == [("GET", "/api/v2/browsers"), ("GET", "/api/v2/browsers/abc/debugger")]
        page.close.assert_called_once()
    else:
        assert calls == [
            ("GET", "/api/v2/browsers"),
            ("POST", "/api/v2/browsers/abc"),
            ("DELETE", "/api/v2/browsers/abc"),
        ]


def test_failed_cdp_attach_closes_only_the_profile_just_started(monkeypatch):
    runtime = ChromiumRuntime(None)
    connection = {"provider": "dolphin", "port": 3001, "id": "7", "token": ""}
    engine = Mock()
    engine.connect_over_cdp.side_effect = RuntimeError("internal-debug-url")
    monkeypatch.setattr(runtime, "_engine", lambda: engine)
    monkeypatch.setattr(
        ProfileClient,
        "open_profile",
        lambda *_: ("ws://127.0.0.1:9222/devtools/browser/test", True),
    )
    stop = Mock()
    monkeypatch.setattr(ProfileClient, "stop_profile", stop)
    with pytest.raises(BrowserLaunchError, match="Dolphin Anty") as error:
        runtime._open_external("a" * 32, connection)
    assert "internal-debug-url" not in str(error.value)
    stop.assert_called_once_with("7")
