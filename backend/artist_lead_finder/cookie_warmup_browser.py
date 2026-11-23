"""Search, browse and accept cookies inside the user's NSTbrowser context."""

import json
import random
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .errors import UserError
from .external_browsers import ProfileClient

COOKIE_BUTTONS = re.compile(
    r"^(accept all(?: cookies)?|accept cookies|allow all(?: cookies)?|agree to all|"
    r"accept all & continue|принять все(?: файлы cookie)?|принять cookies|"
    r"приемане на всички|приемам всички|alle akzeptieren|alles akzeptieren|"
    r"alle cookies akzeptieren|zustimmen und weiter|tout accepter|aceptar todas|accetta tutti)$",
    re.I,
)
BLOCKED_LINKS = re.compile(
    r"login|log-in|signin|sign-in|logout|signout|signup|register|checkout|purchase|"
    r"unsubscribe|delete|remove|privacy|cookie|terms|settings|account|/ads/",
    re.I,
)


class SessionComplete(Exception):
    pass


def host_matches(url, domain):
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def result_url(base, href):
    value = urljoin(base, href)
    parsed = urlsplit(value)
    if parsed.path == "/url" and host_matches(value, "google.com"):
        query = parse_qs(parsed.query)
        return (query.get("q") or query.get("url") or [""])[0]
    return value


class VisitSession:
    def __init__(self, page, end, stopping, report):
        self.page, self.end, self.stopping, self.report = page, end, stopping, report

    def check(self):
        if self.stopping.is_set():
            raise InterruptedError
        if time.monotonic() >= self.end:
            raise SessionComplete

    def timeout(self, seconds=15):
        self.check()
        return max(1, min(seconds, self.end - time.monotonic()) * 1000)

    def wait(self, seconds):
        self.check()
        self.stopping.wait(min(seconds, max(0, self.end - time.monotonic())))
        self.check()

    def check_access(self):
        self.check()
        body = self.page.locator("body").inner_text(timeout=self.timeout(5)).lower()[:18000]
        if "/sorry/" in self.page.url or any(
            phrase in body
            for phrase in (
                "verify you are human",
                "confirm you are human",
                "unusual traffic from your computer",
                "подозрительный трафик",
                "потвърдете, че сте човек",
                "подтвердите, что вы человек",
            )
        ):
            raise UserError(
                "Сайт запросил проверку человека. Прогрев остановлен, вкладка оставлена открытой."
            )

    def click(self, link):
        self.check()
        heading = link.locator("h3").first
        target = heading if heading.count() and heading.is_visible() else link
        link.evaluate("e => {const a=e.closest('a'); if(a)a.target='_self'}")
        try:
            target.scroll_into_view_if_needed(timeout=self.timeout(3))
            rect = target.bounding_box()
            if rect:
                self.page.mouse.move(
                    rect["x"] + rect["width"] / 2,
                    rect["y"] + rect["height"] / 2,
                    steps=random.randint(15, 30),
                )
            target.click(timeout=self.timeout(3), no_wait_after=True)
        except Exception:
            self.check()
            if link.evaluate("e => e.tagName !== 'A'"):
                raise
            link.focus(timeout=self.timeout(3))
            link.press("Enter", timeout=self.timeout(3), no_wait_after=True)

    def accept_cookies(self):
        for frame in self.page.frames:
            self.check()
            try:
                for button in frame.get_by_role("button", name=COOKIE_BUTTONS).all():
                    if button.is_visible():
                        self.click(button)
                        self.report("Приняты cookies сайта")
                        return
            except PlaywrightError:
                self.check()

    def is_result(self, link, domain):
        href = result_url(self.page.url, link.get_attribute("href") or "")
        if host_matches(href, domain):
            return True
        parsed = urlsplit(href)
        if not (host_matches(href, "google.com") and parsed.path in ("/goto", "/url")):
            return False
        if not link.locator("h3").count():
            return False
        source = re.compile(
            r"^(?:https?://)?(?:[a-z0-9-]+\.)*" + re.escape(domain) + r"(?=[/\s›·]|$)", re.I
        )
        if any(source.search(text.strip()) for text in link.locator("cite").all_text_contents()):
            return True
        brand = re.compile(
            "^" + re.escape(domain.split(".")[0]) + r"(?:\.com|\.org)?(?:\s*[·›].*)?$", re.I
        )
        return any(
            brand.fullmatch(text.strip()) for text in link.locator("span").all_text_contents()
        )

    def open_from_search(self, domain):
        page = self.page
        self.report("Поиск в Google: " + domain, current=domain)
        page.goto("https://www.google.com/", wait_until="domcontentloaded", timeout=self.timeout())
        self.accept_cookies()
        self.check_access()
        box = page.locator('textarea[name="q"], input[name="q"]').filter(visible=True).first
        box.wait_for(state="visible", timeout=self.timeout(12))
        self.click(box)
        box.fill("")
        box.press_sequentially(domain, delay=90, timeout=self.timeout())
        box.press("Enter", timeout=self.timeout())
        page.wait_for_url(
            lambda url: urlsplit(url).path in ("/search", "/sorry/index"),
            timeout=self.timeout(),
            wait_until="domcontentloaded",
        )
        deadline = min(self.end, time.monotonic() + 15)
        while time.monotonic() < deadline:
            self.check_access()
            candidates = [
                link
                for link in page.locator("a[href]:visible").all()[:300]
                if self.is_result(link, domain) and not link.get_attribute("download")
            ]
            if candidates:
                self.click(candidates[0])
                page.wait_for_url(
                    lambda url: host_matches(url, domain),
                    timeout=self.timeout(),
                    wait_until="domcontentloaded",
                )
                self.check_access()
                self.report("Открыт сайт: " + domain)
                return
            self.wait(0.3)
        self.check()
        raise UserError(f"В Google не найдена ссылка на {domain}. Проверьте вкладку браузера.")

    def open_content(self, domain):
        page, original = self.page, self.page.url
        choices = []
        height = page.evaluate("window.innerHeight")
        for link in page.locator("a[href]:visible").all()[:200]:
            self.check()
            raw = link.get_attribute("href") or ""
            href = urljoin(original, raw)
            path = urlsplit(href).path
            if (
                urlsplit(href).scheme not in ("http", "https")
                or not host_matches(href, domain)
                or path in ("", "/")
                or href.split("#")[0] == original.split("#")[0]
                or BLOCKED_LINKS.search(href)
                or link.get_attribute("download")
                or (domain == "reddit.com" and "/comments/" not in path)
            ):
                continue
            score = (
                10
                if any(part in path for part in ("/comments/", "/dp/", "/product", "/item/"))
                else 0
            )
            rect = link.bounding_box()
            if rect and 100 < rect["y"] < height - 50:
                score += 100
            slot = link.get_attribute("slot") or ""
            if slot == "text-body":
                continue
            if slot in ("title", "full-post-link"):
                score += 20
            selector = "a[href=" + json.dumps(raw) + "]"
            if slot:
                selector += "[slot=" + json.dumps(slot) + "]"
            choices.append((score, selector))
        for _, selector in sorted(choices, reverse=True)[:5]:
            self.check()
            link = page.locator(selector).filter(visible=True).first
            try:
                self.click(link)
                page.wait_for_url(
                    lambda url: url.split("#")[0] != original.split("#")[0],
                    timeout=self.timeout(12),
                    wait_until="domcontentloaded",
                )
                self.check_access()
                self.accept_cookies()
                self.report("Открыт материал на " + domain)
                self.wait(random.uniform(5, 10))
                return True
            except (InterruptedError, SessionComplete, UserError):
                raise
            except Exception:
                if page.url != original:
                    raise
        self.report("Материал не найден, просмотр страницы " + domain)
        self.wait(7)
        return False

    def visit(self, url):
        domain = urlsplit(url).hostname.removeprefix("www.")
        self.open_from_search(domain)
        self.wait(3)
        self.accept_cookies()
        for _ in range(2):
            self.check_access()
            self.page.mouse.wheel(0, random.randint(250, 450))
            self.wait(2)
        opened = self.open_content(domain)
        self.report("Просмотр завершён: " + domain, visited=opened)


def browse(connection, settings, stopping, report):
    if stopping.is_set():
        raise InterruptedError
    endpoint, _started = ProfileClient(connection).open_profile(connection["id"])
    if stopping.is_set():
        raise InterruptedError
    end = time.monotonic() + settings.minutes * 60
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
        if not browser.contexts:
            raise UserError("NSTbrowser не предоставил контекст профиля.")
        context = browser.contexts[0]
        index = 0
        try:
            while True:
                if stopping.is_set():
                    raise InterruptedError
                if time.monotonic() >= end:
                    return
                page = context.new_page()
                page.set_default_timeout(4000)
                session = VisitSession(page, end, stopping, report)
                keep_open = False
                try:
                    page.bring_to_front()
                    session.visit(settings.sites[index % len(settings.sites)])
                except (InterruptedError, SessionComplete):
                    raise
                except Exception:
                    session.check()
                    keep_open = True
                    raise
                finally:
                    if not keep_open:
                        try:
                            page.close()
                        except PlaywrightError:
                            pass
                index += 1
        except SessionComplete:
            return
