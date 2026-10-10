"""The loopback bridge to an account proxy with a login, against a stand-in upstream proxy;
and the account window opened through it."""

import socket
import threading
import uuid

import pytest

from cloud_worker.engine import Engine
from cloud_worker.proxy_relay import ProxyRelay, RelayError, _exactly, _read_headers

PROFILE = "b" * 32


def upstream(kind: str, seen: dict) -> tuple[int, threading.Thread]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        stream, _ = server.accept()
        stream.settimeout(5)
        if kind == "http":
            seen["request"] = _read_headers(stream)
            stream.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            seen["hello"] = _exactly(stream, 3)
            stream.sendall(b"\x05\x02")
            seen["auth"] = _exactly(stream, 11)
            stream.sendall(b"\x01\x00")
            seen["connect"] = _exactly(stream, 24)
            stream.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
        seen["ping"] = _exactly(stream, 4)
        stream.sendall(b"pong")
        stream.close()
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread


def through(relay: ProxyRelay, target: bytes) -> tuple[bytes, socket.socket]:
    client = socket.create_connection(("127.0.0.1", relay.local_port), 5)
    client.settimeout(5)
    client.sendall(b"CONNECT " + target + b" HTTP/1.1\r\nHost: " + target + b"\r\n\r\n")
    return _read_headers(client), client


@pytest.mark.parametrize("kind", ["socks5", "http"])
def test_a_proxy_with_a_login_is_bridged(kind):
    seen: dict = {}
    port, server = upstream(kind, seen)
    relay = ProxyRelay(
        {"scheme": kind, "host": "127.0.0.1", "port": port, "username": "user", "password": "pass"}
    )
    try:
        answer, client = through(relay, b"www.instagram.com:443")
        assert answer.startswith(b"HTTP/1.1 200")
        client.sendall(b"ping")
        assert _exactly(client, 4) == b"pong"
        client.close()
        server.join(5)
    finally:
        relay.stop()
    assert seen["ping"] == b"ping"
    if kind == "socks5":
        assert seen["hello"] == b"\x05\x01\x02"
        assert seen["auth"] == b"\x01\x04user\x04pass"
        assert seen["connect"] == b"\x05\x01\x00\x03\x11www.instagram.com\x01\xbb"
    else:
        assert seen["request"].startswith(b"CONNECT www.instagram.com:443 HTTP/1.1\r\n")
        assert b"Proxy-Authorization: Basic dXNlcjpwYXNz\r\n" in seen["request"]


def test_only_https_goes_through_and_a_dead_proxy_is_a_bad_gateway():
    relay = ProxyRelay(
        {"scheme": "socks5", "host": "127.0.0.1", "port": 1, "username": "u", "password": "p"}
    )
    try:
        answer, client = through(relay, b"www.instagram.com:80")
        assert answer.startswith(b"HTTP/1.1 403")
        client.close()
        answer, client = through(relay, b"www.instagram.com:443")
        assert answer.startswith(b"HTTP/1.1 502")
        client.close()
    finally:
        relay.stop()
    with pytest.raises(RelayError):
        ProxyRelay({"scheme": "socks5", "host": "h", "port": 1080, "username": "u"})


def test_the_account_window_opens_through_the_bridge_only_for_a_login(tmp_path, monkeypatch):
    engine = Engine(tmp_path, str(uuid.uuid4()))
    opened = []
    monkeypatch.setattr(engine.service.chromium, "open", lambda *args: opened.append(args) or {})
    try:
        engine.put_session(PROFILE, "Main", {"proxy": {"scheme": "http", "host": "h", "port": 1}})
        engine.open_window(PROFILE)
        assert opened[-1] == (PROFILE, None) and not engine.relays
        proxy = {"scheme": "socks5", "host": "h", "port": 1080, "username": "u", "password": "p"}
        engine.put_session(PROFILE, "Main", {"proxy": proxy})
        engine.open_window(PROFILE)
        relay = engine.relays[PROFILE]
        assert opened[-1] == (PROFILE, relay.url)
        engine.open_window(PROFILE)
        assert relay.stopped.is_set() and engine.relays[PROFILE] is not relay
    finally:
        engine.close()
    assert not engine.relays
