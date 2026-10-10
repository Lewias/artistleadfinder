"""A loopback CONNECT bridge for an account proxy with a login, as the desktop app's
`proxy_relay.rs`: Chromium cannot pass a SOCKS5 username and password itself.

Chromium talks plain HTTP CONNECT to 127.0.0.1; the bridge logs in to the upstream proxy
(SOCKS5 or HTTP Basic) and joins the two. Only HTTPS (port 443) is let through.
"""

import base64
import logging
import re
import socket
import struct
import threading

log = logging.getLogger(__name__)

HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
CONNECT_TIMEOUT = 10
IO_TIMEOUT = 15
MAX_HEADERS = 16_384


class RelayError(ValueError):
    """The proxy record cannot be bridged; the message is safe for the user."""


def _read_headers(stream: socket.socket) -> bytes:
    data = b""
    while not data.endswith(b"\r\n\r\n"):
        if len(data) >= MAX_HEADERS:
            raise ConnectionError("headers too large")
        byte = stream.recv(1)
        if not byte:
            raise ConnectionError("closed")
        data += byte
    return data


def _exactly(stream: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


class ProxyRelay:
    def __init__(self, proxy: dict):
        scheme = proxy.get("scheme")
        host, port = proxy.get("host"), proxy.get("port")
        username, password = proxy.get("username"), proxy.get("password")
        if (
            scheme not in ("http", "socks5")
            or not isinstance(host, str)
            or not host
            or not isinstance(port, int)
            or not 0 < port < 65536
            or not isinstance(username, str)
            or not isinstance(password, str)
            or not username
            or not password
        ):
            raise RelayError("Некорректный прокси аккаунта.")
        self.scheme, self.host, self.port = scheme, host, port
        self.username, self.password = username, password
        self.stopped = threading.Event()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.listener.settimeout(0.5)
        self.local_port = self.listener.getsockname()[1]
        threading.Thread(target=self._accept, name="proxy-relay", daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def stop(self) -> None:
        self.stopped.set()
        try:
            self.listener.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while not self.stopped.is_set():
            try:
                client, peer = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if peer[0] != "127.0.0.1":
                client.close()
                continue
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        remote = None
        try:
            client.settimeout(IO_TIMEOUT)
            line = _read_headers(client).split(b"\r\n", 1)[0].decode("latin-1")
            parts = line.split()
            if len(parts) != 3 or parts[0] != "CONNECT" or parts[2] not in ("HTTP/1.1", "HTTP/1.0"):
                client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
                return
            host, _, port_text = parts[1].rpartition(":")
            if not HOST.match(host) or port_text != "443":
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            try:
                remote = socket.create_connection((self.host, self.port), CONNECT_TIMEOUT)
                remote.settimeout(IO_TIMEOUT)
                if self.scheme == "http":
                    self._http_connect(remote, parts[1])
                else:
                    self._socks5_connect(remote, host, 443)
            except (OSError, ConnectionError, PermissionError):
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            client.settimeout(None)
            remote.settimeout(None)
            self._tunnel(client, remote)
        except (OSError, ConnectionError, UnicodeDecodeError):
            pass
        finally:
            for stream in (client, remote):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def _http_connect(self, remote: socket.socket, target: str) -> None:
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        remote.sendall(
            f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n\r\n".encode()
        )
        response = _read_headers(remote)
        if not response.startswith((b"HTTP/1.1 200 ", b"HTTP/1.0 200 ")):
            raise PermissionError("proxy rejected")

    def _socks5_connect(self, remote: socket.socket, host: str, port: int) -> None:
        user, password, name = self.username.encode(), self.password.encode(), host.encode()
        if max(len(user), len(password), len(name)) > 255:
            raise PermissionError("too long")
        remote.sendall(b"\x05\x01\x02")
        if _exactly(remote, 2) != b"\x05\x02":
            raise PermissionError("no password login")
        remote.sendall(bytes([1, len(user)]) + user + bytes([len(password)]) + password)
        if _exactly(remote, 2) != b"\x01\x00":
            raise PermissionError("login refused")
        remote.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port))
        head = _exactly(remote, 4)
        if head[0] != 5 or head[1] != 0:
            raise PermissionError("connect refused")
        if head[3] == 1:
            size = 4
        elif head[3] == 3:
            size = _exactly(remote, 1)[0]
        elif head[3] == 4:
            size = 16
        else:
            raise PermissionError("bad reply")
        _exactly(remote, size + 2)

    @staticmethod
    def _tunnel(client: socket.socket, remote: socket.socket) -> None:
        def pump(source: socket.socket, target: socket.socket) -> None:
            try:
                while chunk := source.recv(65536):
                    target.sendall(chunk)
            except OSError:
                pass
            finally:
                try:
                    target.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        forward = threading.Thread(target=pump, args=(client, remote), daemon=True)
        forward.start()
        pump(remote, client)
        forward.join()
