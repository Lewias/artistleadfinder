"""Own Chromium windows for Instagram profiles."""

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlsplit

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from . import profile_requests
from .browser_sessions import BrowserSessions
from .database import application_data_dir
from .direct_message import send_direct
from .inbox.reader import read_thread
from .lead_scout import feed as grid_feed
from .lead_scout.candidates import normalize_instagram_username, parse_instagram_post_url
from .lead_scout.profiles import page_data
from .profile_requests import ProfileTemplate

# Windows: Chromium is frozen into the core (Playwright's "0" location). macOS: PyInstaller
# cannot re-sign Chromium's app bundle, so it is downloaded once into the app data folder,
# which also survives app updates.
# Linux is only the cloud parser container, whose image brings its own Chromium path.
DOWNLOADS_CHROMIUM = sys.platform == "darwin"
if not (sys.platform.startswith("linux") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH")):
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
    # Bodies of data_responses already read, by response identity (cleared on navigation).
    bodies: dict = field(default_factory=dict)
    # Fast profile requests: the learned template, borrowed session headers, and until
    # when the fast path stays off after Instagram refused it (monotonic seconds).
    profile_template: ProfileTemplate | None = None
    session_headers: dict | None = None
    fast_off_until: float = 0.0
    fast_failures: int = 0
    # `web_profile_info` answered 404: this session has no such address; not tried again.
    borrowed_off: bool = False
    # Profile pages whose own request was not learned yet (reported once).
    unlearned: int = 0
    # The app worked in this window (a parser run, a send, Direct reading); since when it
    # has had nothing more to do (monotonic seconds). See browser_idle.
    worked: bool = False
    idle_since: float | None = None


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


PRELOADED_WITH = """(needle) => [...document.querySelectorAll('script[type="application/json"]')]
  .map(script => script.textContent || '')
  .filter(text => text.includes(needle) && text.length < 4000000)
  .slice(0, 60)"""


def response_texts(window: Window) -> list[tuple[object, str]]:
    """(response, body text) of the data answers since the last navigation. Makes no
    requests; a body the browser already evicted is skipped."""
    found, size = [], 0
    for response in list(window.data_responses):
        key = id(response)
        text = window.bodies.get(key)
        if text is None:
            try:
                if response.status != 200:
                    continue
                body = response.body()
            except Exception:  # noqa: BLE001 - a body evicted by the browser is just skipped
                continue
            if len(body) > MAX_DATA_BYTES:
                continue
            text = body.decode("utf-8", "replace")
            window.bodies[key] = text
        size += len(text)
        if size > MAX_DATA_BYTES * 4:
            break
        found.append((response, text))
    return found


def preloaded(window: Window, needle: str) -> list[str]:
    """JSON the page got with its HTML (relay preloads) that mentions `needle`."""
    try:
        return [str(text) for text in window.page.evaluate(PRELOADED_WITH, needle)]
    except Exception:  # noqa: BLE001 - the page may be navigating; the DOM reading still works
        return []


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
    texts = [text for _, text in response_texts(window)]
    try:
        texts += [str(text) for text in window.page.evaluate(PRELOADED_DATA)]
    except Exception:  # noqa: BLE001 - the page may be navigating; the grid still works
        pass
    facts = grid_feed.collect_texts(texts)
    return {code: item.as_dict() for code, item in facts.items() if code in wanted}


def _request_parts(response) -> tuple[str, str, dict, str | None] | None:
    try:
        request = response.request
        try:
            headers = request.all_headers()
        except Exception:  # noqa: BLE001 - older drivers: the headers the page set
            headers = request.headers
        return request.url, request.method, dict(headers or {}), request.post_data
    except Exception:  # noqa: BLE001 - a request without readable parts teaches nothing
        return None


def learn_profile_request(window: Window, username: str, user: dict | None) -> None:
    """Remember how this page asked for its owner's data, for fast requests later."""
    if user is None:
        return
    uid = page_data.user_id(user)
    for response, text in response_texts(window):
        parts = _request_parts(response)
        if parts is None:
            continue
        url, method, headers, body = parts
        if window.session_headers is None and "x-ig-app-id" in {k.lower() for k in headers}:
            window.session_headers = headers
        if username not in text.lower() or not page_data.profile_user([text], username):
            continue
        template = profile_requests.learn(url, method, headers, body, username, uid)
        if template is not None:
            learned = window.profile_template is None or window.profile_template.kind != (
                template.kind
            )
            window.profile_template = template
            window.fast_failures = 0
            if learned:
                log.info("profile_request_learned", extra={"detail": template.kind})
            return
    window.unlearned += 1
    if window.unlearned == 3:
        # Why nothing was learned: the paths of the data requests the page made.
        paths = sorted({urlsplit(response.url).path for response, _ in response_texts(window)})[:12]
        log.info("profile_request_not_learned", extra={"detail": ", ".join(paths) or "none"})
    if (
        window.profile_template is None
        and window.session_headers is not None
        and not window.borrowed_off
    ):
        window.profile_template = profile_requests.borrowed(window.session_headers, username)


FAST_OFF_SECONDS = 3600
FAST_MAX_FAILURES = 3


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

    def close(self, identifier: str) -> None:
        """Close the window, keeping its session cookies."""
        self._close(identifier)

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
        window.bodies.clear()
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

    def read_thread(self, identifier: str, username: str, outbound: list[str]) -> dict:
        """One outreach thread read through Instagram's interface; nothing is typed."""
        window = self._window(identifier)
        if not window:
            raise ValueError("Browser window is closed")
        return read_thread(
            window.page, username, outbound, rate_limited=lambda: window.rate_limited
        )

    def evaluate(self, identifier: str, script: str, args=None, feed: bool = False) -> dict:
        """Run a page script. `feed` (grid pages) adds what the page's own data says about
        the collected posts."""
        window = self._window(identifier)
        if not window:
            raise ValueError("Browser window is closed")
        if len(script) > 50000:
            raise ValueError("Script too large")
        # Scripts written as functions receive their arguments (follow-list paging).
        result = (
            window.page.evaluate(script, args) if args is not None else window.page.evaluate(script)
        )
        if not isinstance(result, dict):
            raise ValueError("Invalid browser result")
        # Chromium shows its own error page for HTTP 429, which page scripts cannot
        # recognise; report it so the queue pauses and waits out the break.
        if window.rate_limited:
            return {**result, "ready": False, "blocked": True, "rate_limited": True}
        if feed and result.get("ready") and not result.get("blocked"):
            try:
                result["feed"] = grid_facts(window, result.get("posts") or [])
            except Exception:  # noqa: BLE001 - publications are then opened as before
                log.warning("grid_feed_failed")
        return result

    # ---------- Page data and fast profile requests (called by the core in-process) ----------

    def page_profile(self, identifier: str, username: str) -> dict | None:
        """The opened profile's owner from the JSON the page received (no request), and
        the page's own profile request learned for fast requests."""
        window = self._window(identifier)
        name = normalize_instagram_username(username)
        if not window or not name:
            return None
        try:
            texts = [text for _, text in response_texts(window)] + preloaded(window, name)
            user = page_data.profile_user(texts, name)
            learn_profile_request(window, name, user)
            return user
        except Exception:  # noqa: BLE001 - the DOM reading is used alone then
            log.warning("page_profile_failed")
            return None

    def page_people(self, identifier: str) -> dict[str, dict]:
        """Light facts of the users the current page's data shows (private, followers)."""
        window = self._window(identifier)
        if not window:
            return {}
        try:
            texts = [text for _, text in response_texts(window)] + preloaded(window, '"username"')
            return page_data.people(texts)
        except Exception:  # noqa: BLE001 - candidates are then checked on their profile
            log.warning("page_people_failed")
            return {}

    def can_fetch_profile(self, identifier: str, user_id: str | None = None) -> bool:
        """A fast request can be made for this candidate (a "page_query" needs its id)."""
        window = self.windows.get(identifier)
        template = window.profile_template if window else None
        return bool(
            template is not None
            and time.monotonic() >= window.fast_off_until
            and (not template.user_id or (user_id and user_id.isdigit()))
        )

    def fetch_profile(self, identifier: str, username: str, user_id: str | None = None) -> dict:
        """One profile through the learned request, from the open tab without loading a
        page. {"user": {...}} or {"fallback": reason}; a refusal turns the fast path off
        for an hour (the profile page is opened instead, no run pause)."""
        window = self._window(identifier)
        name = normalize_instagram_username(username)
        template = window.profile_template if window else None
        if not window or not name or template is None:
            return {"fallback": "no_template"}
        if template.user_id and not (user_id and user_id.isdigit()):
            return {"fallback": "no user id"}
        url, body, count = profile_requests.retarget(template, name, user_id)
        if not count:
            window.profile_template = None
            return {"fallback": "no_template"}
        flagged = window.rate_limited
        try:
            answer = window.page.evaluate(
                profile_requests.REPLAY,
                {
                    "url": url,
                    "method": template.method,
                    "headers": template.headers,
                    "body": body,
                    "referrer": f"https://www.instagram.com/{name}/",
                },
            )
        except Exception:  # noqa: BLE001 - the tab may be navigating; open the page instead
            return {"fallback": "browser_error"}
        finally:
            # A refused fast request is not the page's 429: the run is not paused for it.
            window.rate_limited = flagged
        verdict = profile_requests.outcome(answer)
        said = profile_requests.describe(answer)
        if verdict == "limited":
            window.fast_off_until = time.monotonic() + FAST_OFF_SECONDS
            log.info("fast_profile_refused", extra={"detail": f"{template.kind}: {said}"})
            return {"fallback": f"refused, {said}", "status": (answer or {}).get("status")}
        user = page_data.profile_user([answer.get("text") or ""], name) if verdict == "ok" else None
        if user is not None and user_id and page_data.user_id(user) not in (None, user_id):
            user = None  # another user under this name: the page decides
        if user is None:
            reason = "no user in the answer" if verdict == "ok" else said
            log.info("fast_profile_unused", extra={"detail": f"{template.kind}: {reason}"})
            if template.kind == "web_profile_info" and (answer or {}).get("status") == 404:
                # The address does not exist for this session: page requests only.
                window.borrowed_off = True
                window.profile_template = None
                window.fast_failures = 0
                return {"fallback": reason, "status": 404}
            window.fast_failures += 1
            if window.fast_failures >= FAST_MAX_FAILURES:
                # Answers without the user: the template no longer fits; learn it again.
                window.profile_template = None
                window.fast_failures = 0
            return {"fallback": reason, "status": (answer or {}).get("status")}
        window.fast_failures = 0
        return {"user": user, "kind": template.kind}

    def shutdown(self) -> None:
        for identifier in list(self.windows):
            self._close(identifier)
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
