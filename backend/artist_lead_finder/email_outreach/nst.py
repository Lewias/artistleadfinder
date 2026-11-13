"""Warm-up partners in Nstbrowser: Gmail accounts already signed in inside its profiles.

The local Nstbrowser API (the desktop client must be open) starts a profile and gives its
DevTools address; Playwright attaches to it and works in Gmail's web interface, forced to
English (`hl=en`) so the button labels are the same for every account. Nothing here sends
cold email: a partner only finds our warm-up email, takes it out of spam and answers.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEFAULT_URL = "http://localhost:8848/api/v2"
TIMEOUT = 30
GMAIL = "https://mail.google.com/mail/u/0/?hl=en"
TITLE_ADDRESS = re.compile(r"[\w.+-]+@(?:gmail|googlemail)\.com", re.IGNORECASE)
PAGE_WAIT_MS = 45000


class NstError(Exception):
    """kind: offline (the client is closed), auth (wrong API key), profile or gmail."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass
class Profile:
    id: str
    name: str


def _profiles_from(data) -> list[dict]:
    """The list endpoint is not described in the docs: accept the shapes in use."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("docs", "list", "profiles", "items", "records", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _profiles_from(value)
                if nested:
                    return nested
    return []


class NstClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_URL, opener=urllib.request.urlopen):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.opener = opener

    def _call(self, method: str, path: str, query: dict | None = None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url, method=method, headers={"x-api-key": self.api_key, "Accept": "application/json"}
        )
        try:
            with self.opener(request, timeout=TIMEOUT) as response:
                payload = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise NstError("auth", "Nstbrowser не принял API-ключ.") from None
            raise NstError("profile", f"Nstbrowser ответил ошибкой {error.code}.") from None
        except (urllib.error.URLError, OSError, ValueError):
            raise NstError(
                "offline", "Nstbrowser не отвечает: откройте приложение Nstbrowser на этом Mac."
            ) from None
        if not isinstance(payload, dict):
            raise NstError("profile", "Nstbrowser вернул непонятный ответ.")
        if payload.get("err"):
            message = str(payload.get("msg") or "ошибка")
            if "key" in message.lower() or "auth" in message.lower():
                raise NstError("auth", "Nstbrowser не принял API-ключ.")
            raise NstError("profile", f"Nstbrowser: {message[:200]}")
        return payload.get("data")

    def profiles(self) -> list[Profile]:
        found: list[Profile] = []
        seen: set[str] = set()
        for page in range(1, 51):
            rows = _profiles_from(self._call("GET", "/profiles", {"page": page, "pageSize": 100}))
            fresh = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                profile_id = str(row.get("profileId") or row.get("id") or "")
                if not profile_id or profile_id in seen:
                    continue
                seen.add(profile_id)
                fresh += 1
                found.append(Profile(profile_id, str(row.get("name") or "")[:160]))
            if len(rows) < 100 or not fresh:
                break
        return found

    def start(self, profile_id: str) -> str:
        """The DevTools WebSocket of the started profile."""
        data = self._call("POST", f"/browsers/{urllib.parse.quote(profile_id)}")
        url = data.get("webSocketDebuggerUrl") if isinstance(data, dict) else None
        if not url:
            raise NstError("profile", "Nstbrowser не запустил профиль.")
        return str(url)

    def stop(self, profile_id: str) -> None:
        try:
            self._call("DELETE", f"/browsers/{urllib.parse.quote(profile_id)}")
        except NstError:
            pass


class GmailWeb:
    """One Gmail tab inside a started profile."""

    def __init__(self, page):
        self.page = page

    def _open(self, fragment: str = "inbox") -> None:
        self.page.goto(f"{GMAIL}#{fragment}", wait_until="domcontentloaded", timeout=PAGE_WAIT_MS)
        if "accounts.google.com" in self.page.url:
            raise NstError("gmail", "В профиле не выполнен вход в Gmail.")
        # The mail list is drawn after the page loads.
        self.page.wait_for_selector("div[role='main']", timeout=PAGE_WAIT_MS)

    def address(self) -> str | None:
        """The signed-in address: Gmail puts it in the window title."""
        self._open()
        for _ in range(10):
            match = TITLE_ADDRESS.search(self.page.title() or "")
            if match:
                return match.group(0).lower()
            self.page.wait_for_timeout(1000)
        return None

    def handle(self, message_id: str, star: bool, reply: str | None) -> str:
        """Finds the email anywhere (spam included), marks it «Not spam», opens it, may star
        it and answer in the thread. The placement: inbox, spam or missing."""
        query = f"in:anywhere rfc822msgid:{message_id.strip().strip('<>')}"
        self._open("search/" + urllib.parse.quote(query, safe=""))
        rows = self.page.locator("div[role='main'] tr.zA")
        try:
            rows.first.wait_for(timeout=20000)
        except Exception:
            return "missing"
        rows.first.click()
        self.page.wait_for_selector("h2.hP", timeout=PAGE_WAIT_MS)
        placement = "inbox"
        not_spam = self.page.locator(
            "button:has-text('Report not spam'), button:has-text('Not spam'), "
            "div[role='button']:has-text('Not spam')"
        )
        if not_spam.count():
            not_spam.first.click()
            placement = "spam"
            self.page.wait_for_timeout(1500)
        if star:
            unstarred = self.page.locator("[role='main'] [aria-label='Not starred']")
            if unstarred.count():
                unstarred.first.click()
        if reply:
            self.page.locator("[role='main'] [aria-label='Reply'][role='button']").last.click()
            body = self.page.locator("div[aria-label='Message Body'][contenteditable='true']")
            body.last.wait_for(timeout=20000)
            body.last.click()
            self.page.keyboard.type(reply, delay=35)
            self.page.locator("div[role='button'][aria-label^='Send']").last.click()
            self.page.locator("text=Message sent").first.wait_for(timeout=30000)
        return placement


class NstSession:
    """Starts a profile, attaches Playwright and stops the profile afterwards."""

    def __init__(self, client: NstClient, profile_id: str):
        self.client = client
        self.profile_id = profile_id
        self.playwright = None
        self.browser = None

    def __enter__(self) -> GmailWeb:
        from playwright.sync_api import sync_playwright

        url = self.client.start(self.profile_id)
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(url, timeout=PAGE_WAIT_MS)
            context = (
                self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
            )
            page = context.new_page()
        except Exception as error:
            self.__exit__(None, None, None)
            raise NstError("profile", f"Не удалось подключиться к профилю: {error}"[:300]) from None
        return GmailWeb(page)

    def __exit__(self, *_exc) -> None:
        try:
            if self.browser is not None:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright is not None:
                self.playwright.stop()
        finally:
            self.client.stop(self.profile_id)
