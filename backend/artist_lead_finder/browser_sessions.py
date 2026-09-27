"""User-imported Instagram sessions. Secrets never enter settings or logs."""

import ctypes
import json
import os
import re
import time
import uuid
from pathlib import Path


def protect(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise ValueError("Windows DPAPI required")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("Session encryption failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(output.data)


def parse_cookies(text: str) -> list[dict]:
    if len(text.encode("utf-8")) > 1_000_000:
        raise ValueError("Cookie file too large")
    if text.lstrip().startswith(("[", "{")):
        raw = json.loads(text)
        rows = raw.get("cookies") if isinstance(raw, dict) else raw
    else:
        rows = []
        for line in text.splitlines():
            http_only = line.startswith("#HttpOnly_")
            if http_only:
                line = line[len("#HttpOnly_") :]
            elif not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) != 7:
                raise ValueError("Invalid Netscape cookie row")
            domain, _, path, secure, expires, name, value = fields
            rows.append(
                dict(
                    domain=domain,
                    path=path,
                    secure=secure == "TRUE",
                    expires=float(expires),
                    name=name,
                    value=value,
                    httpOnly=http_only,
                )
            )
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Invalid cookie list")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid cookie entry")
        domain = str(row.get("domain", "")).lower()
        host = domain.lstrip(".")
        if host != "instagram.com" and not host.endswith(".instagram.com"):
            continue
        name, value = row.get("name"), row.get("value")
        path = row.get("path", "/")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
            or not isinstance(value, str)
            or len(value) > 16384
            or any(ord(char) < 32 or ord(char) == 127 or char == ";" for char in value)
            or not isinstance(path, str)
            or not path.startswith("/")
            or any(ord(char) < 32 for char in path)
        ):
            raise ValueError("Invalid cookie")
        expires = row.get("expirationDate", row.get("expires", -1))
        expires = float(expires) if expires is not None else -1
        if expires > 0 and expires <= time.time():
            continue
        if expires > 253402300799 or expires != expires:
            raise ValueError("Invalid expiry")
        result[(domain, path, name)] = dict(
            name=name,
            value=value,
            domain=domain,
            path=path,
            secure=True,
            httpOnly=bool(row.get("httpOnly", False)),
            sameSite=row.get("sameSite", "Lax"),
            expires=expires,
        )
    if not result:
        raise ValueError("No unexpired Instagram cookies")
    return list(result.values())


def validate_name(value: object) -> str:
    name = str(value).strip()
    if not name or len(name) > 80:
        raise ValueError("Invalid profile name")
    return name


def validate_proxy(value: object, previous: dict | None = None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not {"scheme", "host", "port"} <= set(value):
        raise ValueError("Invalid proxy configuration")
    if set(value) - {"scheme", "host", "port", "username", "password"}:
        raise ValueError("Invalid proxy configuration")
    scheme, host, port = value["scheme"], value["host"], value["port"]
    if scheme not in ("http", "socks5"):
        raise ValueError("Only HTTP and SOCKS5 proxies are supported")
    if not isinstance(host, str) or len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("Invalid proxy host")
    if host.startswith((".", "-")) or host.endswith((".", "-")) or ".." in host:
        raise ValueError("Invalid proxy host")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid proxy port")
    result = {"scheme": scheme, "host": host, "port": port}
    username = value.get("username")
    password = value.get("password")
    if username is None and password is None:
        return result
    if not isinstance(username, str) or not username or len(username.encode("utf-8")) > 128:
        raise ValueError("Invalid proxy username")
    if ":" in username or any(ord(char) < 32 or ord(char) == 127 for char in username):
        raise ValueError("Invalid proxy username")
    if password is None and previous and previous.get("username") == username:
        password = previous.get("password")
    if not isinstance(password, str) or not password or len(password.encode("utf-8")) > 255:
        raise ValueError("Proxy password required")
    if any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise ValueError("Invalid proxy password")
    result.update(username=username, password=password)
    return result


def read_cookie_file(path: str) -> list[dict]:
    with Path(path).open("rb") as file:
        data = file.read(1_000_001)
    return parse_cookies(data.decode("utf-8-sig"))


def public_profile(identifier: str, record: dict) -> dict:
    proxy = record.get("proxy")
    return dict(
        id=identifier,
        name=record["name"],
        cookie_count=len(record["cookies"]),
        proxy=({key: proxy[key] for key in ("scheme", "host", "port")}
               | {"username": proxy.get("username"), "has_password": bool(proxy.get("password"))})
        if proxy else None,
    )


class BrowserSessions:
    def __init__(self, root: Path):
        self.root = root / "browser-sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid profile id")
        return self.root / (identifier + ".vault")

    def read(self, identifier: str) -> dict:
        return json.loads(protect(self.path(identifier).read_bytes(), decrypt=True))

    def write(self, identifier: str, record: dict):
        path = self.path(identifier)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(protect(json.dumps(record).encode("utf-8")))
        temporary.replace(path)

    def call(self, method: str, params: dict):
        if method == "browser.list":
            profiles = []
            for path in self.root.glob("*.vault"):
                record = self.read(path.stem)
                profiles.append(public_profile(path.stem, record))
            return sorted(profiles, key=lambda profile: profile["name"].casefold())
        if method == "browser.create":
            name = validate_name(params["name"])
            proxy = validate_proxy(params.get("proxy"))
            identifier = uuid.uuid4().hex
            record = dict(name=name, cookies=[], proxy=proxy)
            self.write(identifier, record)
            return public_profile(identifier, record)
        if method == "browser.import":
            name = validate_name(params["name"])
            cookies = read_cookie_file(params["path"])
            identifier = uuid.uuid4().hex
            record = dict(name=name, cookies=cookies, proxy=None)
            self.write(identifier, record)
            return public_profile(identifier, record)
        identifier = params["id"]
        if method == "browser.update":
            record = self.read(identifier)
            record["name"] = validate_name(params["name"])
            record["proxy"] = validate_proxy(params.get("proxy"), record.get("proxy"))
            self.write(identifier, record)
            return public_profile(identifier, record)
        if method == "browser.import_cookies":
            cookies = read_cookie_file(params["path"])
            record = self.read(identifier)
            record["cookies"] = cookies
            self.write(identifier, record)
            return public_profile(identifier, record)
        if method == "browser.load_internal":
            return self.read(identifier)
        if method == "browser.save_internal":
            record = self.read(identifier)
            record["cookies"] = parse_cookies(json.dumps(params["cookies"]))
            self.write(identifier, record)
            return {"ok": True}
        if method == "browser.delete":
            self.path(identifier).unlink()
            return {"ok": True}
        raise ValueError("Unknown browser method")
