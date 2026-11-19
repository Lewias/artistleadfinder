"""Official profile APIs for browser sessions managed outside Artist Lead Finder."""

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .errors import UserError

PROVIDERS = {"nst": ("NSTbrowser", 8848), "dolphin": ("Dolphin Anty", 3001)}
DOLPHIN_API = "https://dolphin-anty-api.com"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = build_opener(ProxyHandler({}), _NoRedirect())


def provider_fields(params: dict, *, token_required: bool = True) -> dict:
    provider = params.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise UserError("Выберите NSTbrowser или Dolphin Anty.")
    label, default_port = PROVIDERS[provider]
    port = params.get("port", default_port)
    if type(port) is not int or not 1 <= port <= 65535:
        raise UserError("Порт локального API должен быть от 1 до 65535.")
    token = params.get("token", "")
    if not isinstance(token, str):
        raise UserError(f"Введите ключ API {label}.")
    token = token.strip()
    if (token_required and not token) or not re.fullmatch(r"[!-~]{0,8192}", token):
        raise UserError(f"Проверьте ключ API {label}.")
    return {"provider": provider, "port": port, "token": token}


def valid_profile_id(provider: str, value: object) -> str:
    if type(value) is int:
        value = str(value)
    pattern = r"[1-9][0-9]{0,18}" if provider == "dolphin" else r"[A-Za-z0-9_-]{1,128}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise UserError("Некорректный ID профиля браузера.")
    return value


class ProfileClient:
    def __init__(self, connection: dict):
        self.provider = connection["provider"]
        self.port = connection["port"]
        self.token = connection["token"]
        self.label = PROVIDERS[self.provider][0]
        origin = f"http://127.0.0.1:{self.port}"
        self.local_url = origin + ("/api/v2" if self.provider == "nst" else "/v1.0")

    def _request(self, method: str, path: str, *, query=None, body=None, cloud=False, timeout=8):
        base = DOLPHIN_API if cloud else self.local_url
        url = base + path + ("?" + urlencode(query) if query else "")
        headers = {"Accept": "application/json"}
        if self.provider == "nst":
            headers["x-api-key"] = self.token
        elif cloud:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            url,
            method=method,
            headers=headers,
            data=None if body is None else json.dumps(body).encode(),
        )
        try:
            with _opener.open(request, timeout=timeout) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError
            payload = json.loads(raw)
        except HTTPError as error:
            with error:
                status = error.code
            if status == 401 or (cloud and status == 403):
                if self.provider == "dolphin" and not cloud:
                    raise UserError(
                        "Войдите в Dolphin Anty в его приложении и повторите."
                    ) from None
                raise UserError(f"{self.label} не принял ключ API.") from None
            if status == 402 and self.provider == "dolphin":
                raise UserError(
                    "Для запуска через API нужна действующая подписка Dolphin Anty."
                ) from None
            if status == 409 and self.provider == "dolphin" and not cloud:
                raise UserError(
                    "Профиль Dolphin Anty занят. Закройте его в Dolphin и повторите."
                ) from None
            if status == 429:
                raise UserError(f"{self.label} просит повторить запрос позже.") from None
            raise UserError(f"{self.label} вернул ошибку API ({status}).") from None
        except (URLError, OSError):
            detail = (
                "Проверьте интернет и повторите."
                if cloud
                else f"Откройте {self.label} на этом компьютере и проверьте порт {self.port}."
            )
            raise UserError(f"{self.label} не отвечает. {detail}") from None
        except (UnicodeError, ValueError):
            raise UserError(f"{self.label} вернул непонятный ответ.") from None
        if not isinstance(payload, dict):
            raise UserError(f"{self.label} вернул непонятный ответ.")
        if self.provider == "nst":
            # The API examples use 0, while the desktop client also returns 200 on success.
            if payload.get("err") or payload.get("code", 0) not in (0, 200):
                raise UserError("Ошибка API NSTbrowser. Проверьте ключ и состояние профиля.")
            return payload.get("data")
        if payload.get("success") is False:
            raise UserError("Dolphin Anty отклонил запрос. Проверьте состояние профиля.")
        return payload

    def profiles(self, cursor: str | None) -> dict:
        query = {"cursor": cursor} if cursor else {}
        if self.provider == "nst":
            data = self._request("GET", "/profiles/cursor", query={**query, "pageSize": 50})
            rows = data.get("docs") if isinstance(data, dict) else None
            next_cursor = (
                data.get("nextCursor") if isinstance(data, dict) and data.get("hasMore") else None
            )
            key = "profileId"
        else:
            data = self._request(
                "GET", "/browser_profiles/list-cursor", query={**query, "limit": 50}, cloud=True
            )
            rows = data.get("data")
            # Only carry the cursor forward, never send the token to URLs from a response.
            try:
                next_cursor = parse_qs(urlsplit(data.get("next_page_url") or "").query).get(
                    "cursor", [None]
                )[0]
            except (TypeError, ValueError, AttributeError):
                next_cursor = None
            key = "id"
        if not isinstance(rows, list):
            raise UserError(f"{self.label} вернул непонятный список профилей.")
        profiles = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                identifier = valid_profile_id(self.provider, row.get(key))
            except UserError:
                continue
            name = str(row.get("name") or "").strip()[:80] or f"{self.label} {identifier}"[:80]
            profiles.append({"id": identifier, "name": name})
        return {"profiles": profiles, "cursor": next_cursor if next_cursor != cursor else None}

    def open_profile(self, identifier: str) -> tuple[str, bool]:
        path = "/browsers/" if self.provider == "nst" else "/browser_profiles/"
        path += quote(valid_profile_id(self.provider, identifier), safe="")
        started = True
        if self.provider == "nst":
            active = self._request("GET", "/browsers")
            if not isinstance(active, list):
                raise UserError("NSTbrowser вернул непонятный список открытых окон.")
            current = next(
                (
                    row
                    for row in active
                    if isinstance(row, dict) and row.get("profileId") == identifier
                ),
                None,
            )
            if current and (current.get("starting") or current.get("stopping")):
                raise UserError(
                    "Профиль NSTbrowser сейчас открывается или закрывается. Повторите чуть позже."
                )
            started = not (current and current.get("running"))
            if started:
                data = self._request("POST", path, timeout=20)
            else:
                data = self._request("GET", path + "/debugger")
        else:
            data = self._request("POST", path + "/start", body={"automation": True}, timeout=25)
        try:
            if self.provider == "nst":
                address = urlsplit(data["webSocketDebuggerUrl"])
                if address.scheme != "ws" or address.hostname not in (
                    "localhost",
                    "127.0.0.1",
                    "::1",
                ):
                    raise ValueError
                if address.username is not None or address.fragment or address.query:
                    raise ValueError
                port, endpoint = address.port, address.path
            else:
                port, endpoint = data["automation"]["port"], data["automation"]["wsEndpoint"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError
            if not re.fullmatch(r"/devtools/browser/[A-Za-z0-9_-]+", endpoint):
                raise ValueError
            return urlunsplit(("ws", f"127.0.0.1:{port}", endpoint, "", "")), bool(started)
        except (KeyError, TypeError, ValueError):
            if started:
                self.stop_profile(identifier)
            raise UserError(f"{self.label} не вернул локальный адрес открытого профиля.") from None

    def stop_profile(self, identifier: str) -> None:
        identifier = quote(valid_profile_id(self.provider, identifier), safe="")
        try:
            if self.provider == "nst":
                self._request("DELETE", f"/browsers/{identifier}", timeout=4)
            else:
                self._request("GET", f"/browser_profiles/{identifier}/stop", timeout=4)
        except UserError:
            pass


@dataclass
class ExternalWindow:
    client: ProfileClient
    identifier: str
    started_here: bool

    def stop(self) -> None:
        if self.started_here:
            self.client.stop_profile(self.identifier)
