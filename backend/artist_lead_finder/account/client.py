"""Minimal Supabase client on the standard library: Auth (GoTrue), PostgREST, Functions.

Tokens, passwords and keys are never logged or put into error texts.
"""

import json
import re
import ssl
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

import certifi

from ..errors import UserError

TIMEOUT = 15
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# Auth (GoTrue) refusals of a sign-up, by error_code.
SIGN_UP_ERRORS = {
    "user_already_exists": "Аккаунт с таким email уже есть. Войдите.",
    "email_exists": "Аккаунт с таким email уже есть. Войдите.",
    "weak_password": "Пароль слишком простой: нужно не меньше 8 символов.",
    "email_address_invalid": "Некорректный email.",
    "validation_failed": "Некорректный email.",
    "signup_disabled": "Регистрация на сервере выключена. Обратитесь к админу.",
}


def tls_context() -> ssl.SSLContext:
    """System roots plus certifi's: a frozen macOS core has no OpenSSL root store of its own."""
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def https_opener(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=tls_context())


class OfflineError(UserError):
    """The server cannot be reached (no network, DNS, timeout, 5xx)."""

    def __init__(self, message: str = "Нет связи с сервером. Проверьте интернет."):
        super().__init__(message)


class AuthError(UserError):
    """The session is not accepted: wrong password, expired or revoked refresh token."""


class SupabaseClient:
    def __init__(self, url: str, anon_key: str, opener=https_opener):
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self.opener = opener

    # ---------- transport ----------

    def _request(self, method: str, path: str, body=None, token: str | None = None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method)
        request.add_header("apikey", self.anon_key)
        request.add_header("Authorization", f"Bearer {token or self.anon_key}")
        request.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self.opener(request, timeout=TIMEOUT) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raw = error.read()
            if error.code >= 500 or error.code == 429:
                raise OfflineError("Сервер временно недоступен. Повторите позже.") from None
            raise self._error(error.code, raw) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            raise OfflineError() from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise OfflineError("Сервер вернул некорректный ответ.") from None

    @staticmethod
    def _error(status: int, raw: bytes) -> UserError:
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        message, code = "", ""
        if isinstance(payload, dict):
            code = str(payload.get("error_code") or "")
            message = str(
                payload.get("message")
                or payload.get("msg")
                or payload.get("error_description")
                or payload.get("error")
                or ""
            )
        # Texts written for the user come from the database functions in Russian.
        if CYRILLIC.search(message):
            return UserError(message[:300])
        if code in SIGN_UP_ERRORS:
            return UserError(SIGN_UP_ERRORS[code])
        if status in (400, 401) and re.search(r"credential|grant|password", message, re.I):
            return AuthError("Неверный email или пароль.")
        if status in (401, 403) or re.search(r"jwt|token", message, re.I):
            return AuthError("Сессия истекла. Войдите снова.")
        return UserError("Сервер отклонил запрос.")

    # ---------- auth ----------

    def sign_in(self, email: str, password: str) -> dict:
        return self._request(
            "POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password}
        )

    def sign_up(self, email: str, password: str, display_name: str) -> dict:
        """A new account; the server confirms it at once and returns its session."""
        return self._request(
            "POST",
            "/auth/v1/signup",
            {"email": email, "password": password, "data": {"display_name": display_name}},
        )

    def refresh(self, refresh_token: str) -> dict:
        try:
            return self._request(
                "POST",
                "/auth/v1/token?grant_type=refresh_token",
                {"refresh_token": refresh_token},
            )
        except (AuthError, OfflineError):
            raise
        except UserError:
            # Any refusal of a refresh token means the session is gone.
            raise AuthError("Сессия истекла. Войдите снова.") from None

    def sign_out(self, token: str) -> None:
        self._request("POST", "/auth/v1/logout", {}, token)

    # ---------- database ----------

    def rpc(self, name: str, params: dict, token: str):
        return self._request("POST", f"/rest/v1/rpc/{quote(name)}", params, token)

    def select(self, table: str, query: dict, token: str) -> list[dict]:
        return self._request("GET", f"/rest/v1/{quote(table)}?{urlencode(query)}", None, token)

    def upsert(self, table: str, rows: list[dict], token: str, on_conflict: str | None = None):
        path = f"/rest/v1/{quote(table)}"
        if on_conflict:
            path += f"?on_conflict={quote(on_conflict)}"
        return self._request(
            "POST",
            path,
            rows,
            token,
            {"Prefer": "resolution=merge-duplicates,return=representation"},
        )

    def update(self, table: str, query: dict, values: dict, token: str) -> list[dict]:
        return self._request(
            "PATCH",
            f"/rest/v1/{quote(table)}?{urlencode(query)}",
            values,
            token,
            {"Prefer": "return=representation"},
        )

    def function(self, name: str, body: dict, token: str):
        return self._request("POST", f"/functions/v1/{quote(name)}", body, token)
