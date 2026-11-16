"""The official Threads API (graph.threads.net), on the standard library.

Authorization uses the user's own Meta app: the code and the short-lived token are
exchanged with that app's secret, which the user enters and the core keeps encrypted.
A long-lived token (60 days) is refreshed without any secret. Tokens and secrets are
never logged or put into error texts.
"""

import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

from ..account.client import https_opener

GRAPH = "https://graph.threads.net"
AUTHORIZE = "https://threads.net/oauth/authorize"
SCOPES = "threads_basic,threads_content_publish"
TIMEOUT = 20
# Tests and the local stand-in point the core at a local server; nothing else is accepted.
BASE_ENV = "ARTIST_LEAD_FINDER_THREADS_API"
MAX_TEXT = 500
MAX_LINKS = 5
LINK = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/[^\s]*"
)
# Graph error codes: the token is gone or lacks a permission / the app is throttled.
AUTH_CODES = {102, 190, 10, 200, 459, 460, 463, 464, 467}
LIMIT_CODES = {4, 17, 32, 613, 80001, 80002}


def graph_base() -> str:
    override = os.environ.get(BASE_ENV, "").rstrip("/")
    if override and urlparse(override).hostname in {"127.0.0.1", "localhost"}:
        return override
    return GRAPH


@dataclass
class ThreadsError(Exception):
    """kind: network (no answer, may have gone through), auth (reconnect), limit
    (throttled), server (5xx), invalid (the request itself is refused), unknown."""

    kind: str
    message: str
    code: int | None = None

    def __str__(self) -> str:
        return self.message


def text_length(text: str) -> int:
    """Threads counts a character as one, an emoji as its UTF-8 bytes."""
    total = 0
    for char in text:
        point = ord(char)
        emoji = (
            point >= 0x1F000
            or 0x2600 <= point <= 0x27BF
            or 0x2B00 <= point <= 0x2BFF
            or 0x2300 <= point <= 0x23FF
            or point in (0x200D, 0xFE0F, 0x20E3)
        )
        total += len(char.encode("utf-8")) if emoji else 1
    return total


def text_errors(text: str) -> list[str]:
    errors = []
    if not text.strip():
        errors.append("Пустой текст.")
    length = text_length(text)
    if length > MAX_TEXT:
        errors.append(
            f"Слишком длинно: {length} из {MAX_TEXT} символов (эмодзи считаются по байтам)."
        )
    links = len(LINK.findall(text))
    if links > MAX_LINKS:
        errors.append(f"Слишком много ссылок: {links}, можно не больше {MAX_LINKS}.")
    return errors


def authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{AUTHORIZE}?{query}"


class ThreadsClient:
    def __init__(self, token: str | None = None, opener=None, base: str | None = None):
        self.token = token
        self.opener = opener or https_opener
        self.base = base or graph_base()

    def _call(self, method: str, path: str, params: dict | None = None, token=True) -> dict:
        params = dict(params or {})
        if token:
            params["access_token"] = self.token
        body = None
        url = f"{self.base}{path}"
        if method == "GET":
            url += "?" + urlencode(params)
        else:
            body = urlencode(params).encode()
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Accept", "application/json")
        try:
            with self.opener(request, TIMEOUT) as response:
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as error:
            raise _http_error(error) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
            raise ThreadsError("network", "Нет связи с Threads. Проверьте интернет.") from None
        except ValueError:
            raise ThreadsError("unknown", "Threads ответил непонятно.") from None

    # ---------- authorization ----------

    def exchange_code(self, app_id: str, secret: str, code: str, redirect_uri: str) -> dict:
        return self._call(
            "POST",
            "/oauth/access_token",
            {
                "client_id": app_id,
                "client_secret": secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            token=False,
        )

    def long_lived(self, secret: str) -> dict:
        return self._call(
            "GET",
            "/access_token",
            {"grant_type": "th_exchange_token", "client_secret": secret},
        )

    def refresh(self) -> dict:
        return self._call("GET", "/refresh_access_token", {"grant_type": "th_refresh_token"})

    def me(self) -> dict:
        return self._call("GET", "/v1.0/me", {"fields": "id,username,name"})

    # ---------- publishing ----------

    def create_text(self, user_id: str, text: str) -> str:
        found = self._call("POST", f"/v1.0/{user_id}/threads", {"media_type": "TEXT", "text": text})
        if not found.get("id"):
            raise ThreadsError("unknown", "Threads не вернул контейнер поста.")
        return str(found["id"])

    def container(self, container_id: str) -> dict:
        return self._call("GET", f"/v1.0/{container_id}", {"fields": "id,status,error_message"})

    def publish(self, user_id: str, container_id: str) -> str:
        found = self._call(
            "POST", f"/v1.0/{user_id}/threads_publish", {"creation_id": container_id}
        )
        if not found.get("id"):
            raise ThreadsError("unknown", "Threads не вернул опубликованный пост.")
        return str(found["id"])

    def media(self, media_id: str) -> dict:
        return self._call("GET", f"/v1.0/{media_id}", {"fields": "id,permalink,text,timestamp"})

    def recent(self, user_id: str, limit: int = 15) -> list[dict]:
        found = self._call(
            "GET",
            f"/v1.0/{user_id}/threads",
            {"fields": "id,permalink,text,timestamp", "limit": limit},
        )
        return list(found.get("data") or [])


def _http_error(error: urllib.error.HTTPError) -> ThreadsError:
    try:
        body = json.loads(error.read().decode() or "{}")
    except (ValueError, OSError):
        body = {}
    detail = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = detail.get("code")
    message = str(detail.get("error_user_msg") or detail.get("message") or "")[:300]
    if (
        error.code == 401
        or code in AUTH_CODES
        or detail.get("type") == "OAuthException"
        and code in (None, 190)
    ):
        return ThreadsError(
            "auth", "Доступ к Threads истёк или отозван: переподключите аккаунт.", code
        )
    if error.code == 429 or code in LIMIT_CODES:
        return ThreadsError("limit", "Threads ограничил запросы: повторим позже.", code)
    if error.code >= 500:
        return ThreadsError("server", "Threads временно не отвечает.", code)
    return ThreadsError("invalid", message or f"Threads отклонил запрос ({error.code}).", code)
