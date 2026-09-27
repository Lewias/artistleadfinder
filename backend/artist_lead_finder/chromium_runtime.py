"""Own Chromium windows for Instagram profiles and candidate search."""

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from .browser_sessions import BrowserSessions

os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"


GUEST_ID = "0" * 32
SEARCH_ID = "search"
INSTAGRAM = "https://www.instagram.com/"

log = logging.getLogger(__name__)


class BrowserLaunchError(ValueError):
    """Safe, fixed-message startup failure for the UI."""


@dataclass
class Window:
    browser: Browser
    context: BrowserContext
    page: Page


class ChromiumRuntime:
    def __init__(self, sessions: BrowserSessions):
        self.sessions = sessions
        self.playwright: Playwright | None = None
        self.windows: dict[str, Window] = {}

    def _engine(self):
        if self.playwright is None:
            self.playwright = sync_playwright().start()
        return self.playwright.chromium

    def self_test(self) -> dict:
        browser = self._engine().launch(headless=True, channel="chromium")
        try:
            page = browser.new_page()
            page.goto("data:text/html,<title>Chromium OK</title>")
            if page.title() != "Chromium OK":
                raise RuntimeError("Chromium render failed")
            return {"ok": True, "version": browser.version}
        finally:
            browser.close()

    def _window(self, identifier: str) -> Window | None:
        window = self.windows.get(identifier)
        if window and window.browser.is_connected():
            try:
                # Sync Playwright handles browser events only during a call; this round trip
                # lets a window closed by hand be noticed.
                window.context.cookies(INSTAGRAM)
            except Exception:
                pass
            if window.page.is_closed():
                # A closed tab with other tabs still open keeps the window in use.
                alive = [page for page in window.context.pages if not page.is_closed()]
                if alive:
                    window.page = alive[-1]
            if window.browser.is_connected() and not window.page.is_closed():
                return window
        if window:
            self._close(identifier)
        return None

    def _close(self, identifier: str) -> None:
        window = self.windows.pop(identifier, None)
        if window:
            # Closing the window by hand leaves the browser connected, so the session
            # can still be kept; this also covers app exit.
            self._persist(identifier, window)
            try:
                window.browser.close()
            except Exception:
                pass

    def _persist(self, identifier: str, window: Window) -> dict:
        if identifier in {GUEST_ID, SEARCH_ID} or not window.browser.is_connected():
            return {"ok": False}
        try:
            cookies = window.context.cookies(INSTAGRAM)
            return self.sessions.call(
                "browser.save_internal", {"id": identifier, "cookies": cookies}
            )
        except Exception as error:
            log.warning("session_autosave_failed", extra={"error_type": type(error).__name__})
            return {"ok": False}

    def is_open(self, identifier: str) -> bool:
        return self._window(identifier) is not None

    def open(self, identifier: str, proxy_override: str | None = None) -> dict:
        window = self._window(identifier)
        if window:
            window.page.bring_to_front()
            return {"ok": True, "reused": True}
        if identifier == GUEST_ID:
            record = {"cookies": [], "proxy": None}
        else:
            record = self.sessions.read(identifier)
        proxy = record.get("proxy")
        proxy_options = None
        if proxy_override:
            if not proxy or "password" not in proxy:
                raise ValueError("Unexpected proxy override")
            proxy_options = {"server": proxy_override}
        elif proxy:
            proxy_options = {"server": f"{proxy['scheme']}://{proxy['host']}:{proxy['port']}"}
            if proxy.get("username"):
                proxy_options.update(username=proxy["username"], password=proxy["password"])
        try:
            browser = self._engine().launch(headless=False, proxy=proxy_options)
        except Exception:
            # Playwright errors can include credentials; never return them over RPC.
            raise BrowserLaunchError(
                "Не удалось запустить Chromium с прокси. Проверьте тип, адрес и доступность прокси."
                if proxy
                else "Не удалось запустить Chromium. Проверьте установку браузера."
            ) from None
        try:
            context = browser.new_context(no_viewport=True)
            cookies = []
            for row in record["cookies"]:
                cookie = {key: row[key] for key in ("name", "value", "domain", "path")}
                cookie["secure"] = row.get("secure", True)
                cookie["httpOnly"] = row.get("httpOnly", False)
                cookie["sameSite"] = self._same_site(row.get("sameSite"))
                if row.get("expires", -1) > 0:
                    cookie["expires"] = row["expires"]
                cookies.append(cookie)
            if cookies:
                context.add_cookies(cookies)
            page = context.new_page()
            self.windows[identifier] = Window(browser, context, page)
            try:
                page.goto(
                    "https://www.instagram.com/", wait_until="domcontentloaded", timeout=15000
                )
            except Exception:
                # Keep the window available when the site is slow or requires manual action.
                return {
                    "ok": True,
                    "reused": False,
                    "warning": (
                        "Instagram не загрузился. Проверьте подключение и прокси в окне браузера."
                    ),
                }
            return {"ok": True, "reused": False}
        except Exception:
            browser.close()
            raise

    @staticmethod
    def _same_site(value: object) -> str:
        normalized = str(value or "Lax").lower()
        return {"strict": "Strict", "none": "None", "no_restriction": "None"}.get(normalized, "Lax")

    def save(self, identifier: str) -> dict:
        if identifier == GUEST_ID:
            raise ValueError("Guest session cannot be saved")
        window = self._window(identifier)
        if not window:
            raise ValueError("Open profile first")
        return self.sessions.call(
            "browser.save_internal",
            {"id": identifier, "cookies": window.context.cookies("https://www.instagram.com/")},
        )

    def navigate(self, identifier: str, url: str) -> dict:
        window = self._window(identifier)
        if not window:
            raise ValueError("Browser window is closed")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in {"instagram.com", "www.instagram.com"}:
            raise ValueError("Only Instagram profile pages are supported")
        try:
            window.page.goto(url, wait_until="domcontentloaded", timeout=12000)
        except Exception:
            if not self._window(identifier):
                raise ValueError("Browser window is closed") from None
        return {"ok": True}

    def evaluate(self, identifier: str, script: str) -> dict:
        window = self._window(identifier)
        if not window:
            raise ValueError("Browser window is closed")
        if len(script) > 50000:
            raise ValueError("Script too large")
        result = window.page.evaluate(script)
        if not isinstance(result, dict):
            raise ValueError("Invalid browser result")
        return result

    def search_open(self, url: str) -> dict:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "search.brave.com"
            or parsed.path != "/search"
        ):
            raise ValueError("Invalid search URL")
        self._close(SEARCH_ID)
        browser = self._engine().launch(headless=False)
        try:
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            self.windows[SEARCH_ID] = Window(browser, context, page)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            return {"ok": True}
        except Exception:
            browser.close()
            raise

    def shutdown(self) -> None:
        for identifier in list(self.windows):
            self._close(identifier)
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
