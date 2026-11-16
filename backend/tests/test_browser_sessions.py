import json
import os
import time

import pytest

from artist_lead_finder import browser_sessions
from artist_lead_finder.browser_sessions import BrowserSessions, parse_cookies, validate_proxy


def test_cookie_formats_filter_domains_expiry_and_secrets():
    rows = [
        dict(domain=".instagram.com", name="sessionid", value="test-secret"),
        dict(domain="instagram.com.evil.test", name="other", value="unrelated"),
        dict(domain=".instagram.com", name="expired", value="x", expires=time.time() - 10),
    ]
    cookies = parse_cookies(json.dumps(rows))
    assert len(cookies) == 1
    assert cookies[0]["secure"]
    text = (
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\ttest-secret"
    )
    assert parse_cookies(text)[0]["httpOnly"]
    with pytest.raises(ValueError):
        parse_cookies('[{"domain":".instagram.com","name":"sessionid","value":"x;bad"}]')
    with pytest.raises(ValueError):
        parse_cookies(json.dumps(rows[1:]))


def test_shared_profile_keeps_threads_and_instagram_sessions_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        browser_sessions, "protect", lambda data, decrypt=False: bytes(b ^ 0xAA for b in data)
    )
    store = BrowserSessions(tmp_path)
    profile = store.call("browser.create", {"name": "Shared"})
    instagram = dict(domain=".instagram.com", name="sessionid", value="instagram-secret")
    threads = dict(domain=".threads.com", name="sessionid", value="threads-secret")
    foreign = dict(domain="threads.com.evil.test", name="sessionid", value="foreign")
    store.call(
        "browser.save_internal",
        {
            "id": profile["id"],
            "cookies": [instagram, threads],
            "threads_cookies": [threads, instagram, foreign],
        },
    )
    saved = store.read(profile["id"])
    assert [c["value"] for c in saved["cookies"]] == ["instagram-secret"]
    assert [c["value"] for c in saved["threads_cookies"]] == ["threads-secret"]
    assert b"threads-secret" not in store.path(profile["id"]).read_bytes()
    assert "threads-secret" not in json.dumps(store.call("browser.list", {}))
    store.call(
        "browser.save_internal", {"id": profile["id"], "cookies": [], "threads_cookies": [threads]}
    )
    assert store.read(profile["id"])["cookies"] == []
    assert store.read(profile["id"])["threads_cookies"]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_vault_roundtrip_no_plaintext_and_delete(tmp_path):
    try:
        browser_sessions.protect(b"dpapi-probe")
    except ValueError:
        pytest.skip("Windows DPAPI unavailable in this test session")
    store = BrowserSessions(tmp_path)
    source = tmp_path / "cookies.json"
    source.write_text(
        json.dumps(
            [dict(domain=".instagram.com", name="sessionid", value="fixture-secret-do-not-log")]
        )
    )
    profile = store.call("browser.import", dict(name="Test", path=str(source)))
    vault = store.path(profile["id"])
    assert b"fixture-secret" not in vault.read_bytes()
    assert "cookies" not in store.call("browser.list", {})[0]
    restarted = BrowserSessions(tmp_path)
    assert restarted.read(profile["id"])["cookies"][0]["value"] == "fixture-secret-do-not-log"
    with pytest.raises(ValueError):
        restarted.path("../outside")
    restarted.call("browser.delete", dict(id=profile["id"]))
    assert restarted.call("browser.list", {}) == []


def test_create_edit_proxy_and_replace_cookies(tmp_path, monkeypatch):
    def reversible_test_cipher(data, decrypt=False):
        return bytes(byte ^ 0xAA for byte in data)

    monkeypatch.setattr(browser_sessions, "protect", reversible_test_cipher)
    store = BrowserSessions(tmp_path)
    proxy = {
        "scheme": "socks5",
        "host": "proxy.example.com",
        "port": 1080,
        "username": "my-user",
        "password": "fixture-proxy-password",
    }
    profile = store.call("browser.create", {"name": "  Work  ", "proxy": proxy})
    assert profile == {
        "id": profile["id"],
        "name": "Work",
        "cookie_count": 0,
        "proxy": {
            "scheme": "socks5",
            "host": "proxy.example.com",
            "port": 1080,
            "username": "my-user",
            "has_password": True,
        },
    }
    assert b"proxy.example.com" not in store.path(profile["id"]).read_bytes()
    assert b"fixture-proxy-password" not in store.path(profile["id"]).read_bytes()
    assert "fixture-proxy-password" not in json.dumps(store.call("browser.list", {}))
    same_proxy = {key: proxy[key] for key in ("scheme", "host", "port", "username")}
    store.call("browser.update", {"id": profile["id"], "name": "Work", "proxy": same_proxy})
    assert store.read(profile["id"])["proxy"]["password"] == "fixture-proxy-password"
    source = tmp_path / "cookies.json"
    source.write_text(json.dumps([dict(domain=".instagram.com", name="sessionid", value="secret")]))
    updated = store.call("browser.import_cookies", {"id": profile["id"], "path": str(source)})
    assert updated["cookie_count"] == 1
    assert "secret" not in json.dumps(updated)
    renamed = store.call("browser.update", {"id": profile["id"], "name": "Personal", "proxy": None})
    assert renamed["proxy"] is None
    assert store.read(profile["id"])["cookies"][0]["value"] == "secret"
    assert BrowserSessions(tmp_path).call("browser.list", {}) == [renamed]


@pytest.mark.parametrize(
    "proxy",
    [
        {"scheme": "https", "host": "proxy.example.com", "port": 443},
        {"scheme": "http", "host": "proxy.example.com", "port": 0},
        {"scheme": "http", "host": "proxy.example.com", "port": True},
        {"scheme": "http", "host": "host --bypass", "port": 8080},
        {"scheme": "http", "host": "proxy.example.com", "port": 8080, "username": "x"},
        {
            "scheme": "http",
            "host": "proxy.example.com",
            "port": 8080,
            "username": "x",
            "password": "",
        },
        {
            "scheme": "socks5",
            "host": "proxy.example.com",
            "port": 1080,
            "username": "x:y",
            "password": "secret",
        },
    ],
)
def test_invalid_proxy_rejected(proxy):
    with pytest.raises(ValueError):
        validate_proxy(proxy)


def test_keychain_box_roundtrip_and_tamper():
    from artist_lead_finder.secret_box import MAC_PREFIX, keychain_box

    key = bytes(range(32))
    created = []

    def source(create):
        created.append(create)
        return key

    sealed = keychain_box(b'{"cookies": "fixture-secret"}', False, source)
    assert sealed.startswith(MAC_PREFIX) and b"fixture-secret" not in sealed
    assert keychain_box(sealed, True, source) == b'{"cookies": "fixture-secret"}'
    assert created == [True, False]
    with pytest.raises(ValueError):
        keychain_box(sealed[:-1] + bytes([sealed[-1] ^ 1]), True, source)
    with pytest.raises(ValueError):
        keychain_box(b"legacy-dpapi-bytes", True, source)
    with pytest.raises(ValueError):
        keychain_box(sealed, True, lambda create: bytes(32))
