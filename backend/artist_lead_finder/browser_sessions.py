"""User-imported Instagram sessions. Secrets never enter settings or logs."""

import json
import re
import time
import uuid
from pathlib import Path

from .errors import UserError
from .external_browsers import ProfileClient, provider_fields, valid_profile_id
from .secret_box import protect


def parse_cookies(
    text: str, *, domains: tuple[str, ...] = ("instagram.com",), allow_empty: bool = False
) -> list[dict]:
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
        if not any(host == allowed or host.endswith("." + allowed) for allowed in domains):
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
    if not result and not allow_empty:
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
    external = record.get("external")
    result = dict(
        id=identifier,
        name=record["name"],
        cookie_count=len(record["cookies"]),
        proxy=(
            {key: proxy[key] for key in ("scheme", "host", "port")}
            | {"username": proxy.get("username"), "has_password": bool(proxy.get("password"))}
        )
        if proxy
        else None,
    )
    if external:
        result["cookie_count"] = record.get("external_session_count", 0)
        result["external"] = {key: external[key] for key in ("provider", "id", "port")}
    return result


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

    def save_external_session_count(self, identifier: str, count: int) -> dict:
        record = self.read(identifier)
        if record.get("external_session_count") != count:
            record["external_session_count"] = count
            self.write(identifier, record)
        return {"ok": True}

    def call(self, method: str, params: dict):
        if method == "browser.list":
            profiles = []
            for path in self.root.glob("*.vault"):
                record = self.read(path.stem)
                profiles.append(public_profile(path.stem, record))
            return sorted(profiles, key=lambda profile: profile["name"].casefold())
        if method == "browser.external_list":
            connection = provider_fields(params)
            cursor = params.get("cursor")
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 4096):
                raise UserError("Некорректная страница списка профилей.")
            return ProfileClient(connection).profiles(cursor)
        if method == "browser.external_add":
            return self.add_external(params)
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
            external = record.get("external")
            if external:
                if params.get("proxy") is not None:
                    raise UserError("Меняйте прокси в настройках профиля исходного браузера.")
                if "external_connection" in params:
                    values = params["external_connection"]
                    if not isinstance(values, dict):
                        raise UserError("Проверьте настройки подключения профиля.")
                    updated = provider_fields(
                        {
                            "provider": external["provider"],
                            "port": values.get("port", external["port"]),
                            "token": (
                                values.get("token") or external["token"]
                                if external["provider"] == "nst"
                                else ""
                            ),
                        },
                        token_required=external["provider"] == "nst",
                    )
                    record["external"] = {**updated, "id": external["id"]}
            else:
                record["proxy"] = validate_proxy(params.get("proxy"), record.get("proxy"))
            self.write(identifier, record)
            return public_profile(identifier, record)
        if method == "browser.import_cookies":
            record = self.read(identifier)
            if record.get("external"):
                raise UserError("Импортируйте cookies в исходном браузере этого профиля.")
            record["cookies"] = read_cookie_file(params["path"])
            self.write(identifier, record)
            return public_profile(identifier, record)
        if method == "browser.load_internal":
            return {"proxy": self.read(identifier).get("proxy")}
        if method == "browser.save_internal":
            record = self.read(identifier)
            if record.get("external"):
                raise UserError("Сессия этого профиля хранится в исходном браузере.")
            record["cookies"] = parse_cookies(
                json.dumps(params["cookies"]), allow_empty="threads_cookies" in params
            )
            if "threads_cookies" in params:
                record["threads_cookies"] = parse_cookies(
                    json.dumps(params["threads_cookies"]),
                    domains=("threads.com", "threads.net"),
                    allow_empty=True,
                )
            self.write(identifier, record)
            return {"ok": True}
        if method == "browser.delete":
            self.path(identifier).unlink()
            return {"ok": True}
        raise ValueError("Unknown browser method")

    def add_external(self, params: dict) -> dict:
        connection = provider_fields(params, token_required=params.get("provider") != "dolphin")
        provider = connection["provider"]
        if provider == "dolphin":
            # The remote token is only used to retrieve the list. Launch uses the signed-in app.
            connection["token"] = ""
        rows = params.get("profiles")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise UserError("Выберите от 1 до 500 профилей.")
        requested = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise UserError("Некорректные данные профиля.")
            requested[valid_profile_id(provider, row.get("id"))] = validate_name(row["name"])

        linked = {
            profile["external"]["id"]
            for profile in self.call("browser.list", {})
            if profile.get("external", {}).get("provider") == provider
        }
        added = 0
        for remote_id, name in requested.items():
            if remote_id in linked:
                continue
            record = dict(
                name=name, cookies=[], proxy=None, external={**connection, "id": remote_id}
            )
            self.write(uuid.uuid4().hex, record)
            added += 1
        return {"added": added, "skipped": len(requested) - added}
