"""Own Chromium windows for Instagram profiles."""

import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from .browser_sessions import BrowserSessions
from .database import application_data_dir
from .direct_message import send_direct
from .lead_scout import feed as grid_feed
from .lead_scout.candidates import parse_instagram_post_url

# Windows: Chromium is frozen into the core (Playwright's "0" location). macOS: PyInstaller
# cannot re-sign Chromium's app bundle, so it is downloaded once into the app data folder,
# which also survives app updates.
DOWNLOADS_CHROMIUM = sys.platform == "darwin"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = (
    str(application_data_dir() / "ms-playwright") if DOWNLOADS_CHROMIUM else "0"
)


GUEST_ID = "0" * 32
INSTAGRAM = "https://www.instagram.com/"

log = logging.getLogger(__name__)


class BrowserLaunchError(ValueError):
    """Safe, fixed-message startup failure for the UI."""


@dataclass
class Window:
    browser: Browser
    context: BrowserContext
    page: Page
    # Set when Instagram answered HTTP 429 since the last navigation.
    rate_limited: bool = False
    # Instagram data answers since the last navigation (a grid's posts arrive this way).
    data_responses: list = field(default_factory=list)


def watch_rate_limits(window: Window) -> None:
    """Flag the window when any Instagram request (page or API call) gets HTTP 429."""

    def on_response(response) -> None:
        try:
            host = (urlparse(response.url).hostname or "").lower()
        except ValueError:
            return
        if response.status == 429 and (host == "instagram.com" or host.endswith(".instagram.com")):
            window.rate_limited = True

    window.context.on("response", on_response)


MAX_DATA_RESPONSES = 200
MAX_DATA_BYTES = 8_000_000
DATA_PATHS = ("/graphql", "/api/v1/")
# Posts the page got with its HTML (relay preloads) before any request was made.
PRELOADED_DATA = """() => [...document.querySelectorAll('script[type="application/json"]')]
  .map(script => script.textContent || '')
  .filter(text => text.includes('"code"') && text.length < 4000000)
  .slice(0, 60)"""


def watch_data(window: Window) -> None:
    """Keep Instagram's own data answers (not their bodies yet) for the grid reader."""

    def on_response(response) -> None:
        try:
            parsed = urlparse(response.url)
        except ValueError:
            return
        host = (parsed.hostname or "").lower()
        if not (host == "instagram.com" or host.endswith(".instagram.com")):
            return
        if not any(part in parsed.path for part in DATA_PATHS):
            return
        if len(window.data_responses) < MAX_DATA_RESPONSES:
            window.data_responses.append(response)

    window.context.on("response", on_response)


def grid_facts(window: Window, posts: list) -> dict:
    """Author and collaborators of the grid's publications, from data the
    page already received. Makes no requests; unreadable answers are skipped."""
    wanted = {
        parsed.shortcode
        for parsed in (parse_instagram_post_url(str(url)) for url in posts or [])
        if parsed
    }
    if not wanted:
        return {}
    texts, size = [], 0
    for response in list(window.data_responses):
        try:
            if response.status != 200:
                continue
            body = response.body()
        except Exception:  # noqa: BLE001 - a body evicted by the browser is just skipped
            continue
        size += len(body)
        if len(body) > MAX_DATA_BYTES or size > MAX_DATA_BYTES * 4:
            continue
        texts.append(body.decode("utf-8", "replace"))
    try:
        texts += [str(text) for text in window.page.evaluate(PRELOADED_DATA)]
    except Exception:  # noqa: BLE001 - the page may be navigating; the grid still works
        pass
    facts = grid_feed.collect_texts(texts)
    return {code: item.as_dict() for code, item in facts.items() if code in wanted}


def install_chromium() -> bool:
    """Run Playwright's own installer; a no-op when this Chromium build is already there."""
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    try:
        result = subprocess.run(
            [*compute_driver_executable(), "install", "chromium", "--no-shell"],
            env=get_driver_env(),
            capture_output=True,
            timeout=1800,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("chromium_install_failed", extra={"error_type": type(error).__name__})
        return False
    if result.returncode:
        log.warning("chromium_install_failed", extra={"exit_code": result.returncode})
        return False
    return True


class ChromiumRuntime:
    def __init__(self, sessions: BrowserSessions, installer=install_chromium):
        self.sessions = sessions
        self.playwright: Playwright | None = None
        self.windows: dict[str, Window] = {}
        self.installer = installer
        self.install: threading.Thread | None = None
        self.installed = not DOWNLOADS_CHROMIUM

    def prepare(self) -> None:
        """Start the one-time Chromium download in the background (macOS)."""
        if self.installed or (self.install and self.install.is_alive()):
            return

        def work() -> None:
            log.info("chromium_install_started")
            self.installed = self.installer()
            if self.installed:
                log.info("chromium_install_finished")

        self.install = threading.Thread(target=work, name="chromium-install", daemon=True)
        self.install.start()

    def _ready(self, wait: float) -> None:
        if self.installed:
            return
        self.prepare()
        self.install.join(wait)
        if self.install.is_alive():
            raise BrowserLaunchError(
                "Chromium скачивается при первом запуске. Попробуйте через пару минут."
            )
        if not self.installed:
            raise BrowserLaunchError(
                "Не удалось скачать Chromium. Проверьте подключение к интернету и повторите."
            )

    def _engine(self):
        if self.playwright is None:
            self.playwright = sync_playwright().start()
        return self.playwright.chromium

    def self_test(self) -> dict:
        self._ready(wait=1800)
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
        if identifier == GUEST_ID or not window.browser.is_connected():
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
        self._ready(wait=20)
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
            window = Window(browser, context, page)
            watch_rate_limits(window)
            watch_data(window)
            self.windows[identifier] = window
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

    def screenshot(self, identifier: str, path) -> bool:
        """Debug screenshot of a profile window's page; False when the window is gone."""
        window = self._window(identifier)
        if not window:
            return False
        window.page.screenshot(path=str(path), full_page=False)
        return True

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
        window.rate_limited = False
        window.data_responses.clear()
        try:
            window.page.goto(url, wait_until="domcontentloaded", timeout=12000)
        except Exception:
            if not self._window(identifier):
                raise ValueError("Browser window is closed") from None
        return {"ok": True, "rate_limited": window.rate_limited}

    def send_message(self, identifier: str, username: str, text: str) -> dict:
        """One outreach message through Instagram's interface in the open profile page."""
        window = self._window(identifier)
        if not window:
            raise ValueError("Browser window is closed")
        window.rate_limited = False
        return send_direct(window.page, username, text, rate_limited=lambda: window.rate_limited)

    def evaluate(
        self, identifier: str, script: str, args=None, fresh: bool = False, feed: bool = False
    ) -> dict:
        """Run a page script. `fresh` starts a new request window without navigating (the
        in-tab profile API step), so a 429 seen before it is not reported twice. `feed`
        (grid pages) adds what the page's own data says about the collected posts."""
        window = self._window(identifier)
        if not window:
            raise ValueError("Browser window is closed")
        if len(script) > 50000:
            raise ValueError("Script too large")
        if fresh:
            window.rate_limited = False
        # Scripts written as functions receive their arguments (follow-list paging).
        result = (
            window.page.evaluate(script, args) if args is not None else window.page.evaluate(script)
        )
        if not isinstance(result, dict):
            raise ValueError("Invalid browser result")
        # Chromium shows its own error page for HTTP 429, which page scripts cannot
        # recognise; report it so the queue pauses and waits out the break. An in-tab API
        # step (`fresh`) reports its own HTTP status instead: that 429 concerns the web
        # API, not the page, and the profile resolver handles it.
        if window.rate_limited and not fresh:
            return {**result, "ready": False, "blocked": True, "rate_limited": True}
        if feed and result.get("ready") and not result.get("blocked"):
            try:
                result["feed"] = grid_facts(window, result.get("posts") or [])
            except Exception:  # noqa: BLE001 - publications are then opened as before
                log.warning("grid_feed_failed")
        return result

    def shutdown(self) -> None:
        for identifier in list(self.windows):
            self._close(identifier)
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
