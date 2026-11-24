"""Short activity steps through the existing browser session's visible Instagram UI."""

import re
from time import monotonic
from urllib.parse import urlsplit

from .direct_message import DISMISS_BUTTON
from .instagram_state import account_problem
from .instagram_warmup_browser import LIKE, UNLIKE
from .lead_scout.candidates import normalize_instagram_username, parse_instagram_post_url

HOME = "https://www.instagram.com/"
REELS = HOME + "reels/"
LOAD_BUDGET = 15
REELS_SECONDS = 300
REEL_SCROLL_SECONDS = 12
TIMELINE_SCROLLS = 3
REEL_PATH = re.compile(r"/reels?/[A-Za-z0-9_-]{1,80}/?")
STORY = re.compile(r"/stories/([A-Za-z0-9_.]{1,30})/(?:([0-9]+)/)?$")
STORY_TITLE = re.compile(r"^(?:stories|истории)\s*[•·|–-]\s*instagram$", re.I)
VIEW_STORY = re.compile(r"^(view story|watch story|смотреть историю|посмотреть историю)$", re.I)
NOTIFICATIONS = re.compile(r"(?:turn on|enable) notifications|включить уведомления", re.I)
POST_ACTIONS = (
    'section:has(svg[aria-label="Comment"]), section:has(svg[aria-label="Комментировать"])'
)
LINKS = """() => [...document.querySelectorAll('main a[href], header a[href]')]
  .filter(a => a.getBoundingClientRect().width && a.getBoundingClientRect().height)
  .map(a => a.href).slice(0, 150)"""
STORY_BUTTONS = """() => [...document.querySelectorAll(
  'main [role="button"][aria-label], main button[aria-label]')]
  .filter(b => b.getBoundingClientRect().width && b.getBoundingClientRect().height)
  .map(b => b.getAttribute('aria-label')).slice(0, 50)"""
STORY_LABEL = re.compile(
    r"^(?:История ([A-Za-z0-9_.]{1,30})|([A-Za-z0-9_.]{1,30})['’]s story)(?:,.*)?$",
    re.I,
)
TIMELINE_LINKS = """() => [...document.querySelectorAll('main article')].flatMap(article => {
  const rect = article.getBoundingClientRect();
  if (rect.bottom <= 0 || rect.top >= innerHeight ||
      !article.checkVisibility({visibilityProperty: true})) return [];
  return [...article.querySelectorAll('a[href]:has(time), a[href*="/p/"], a[href*="/reel/"]')]
    .map(a => a.href).slice(0, 8);
}).slice(0, 40)"""


def canonical(value, kind):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ("instagram.com", "www.instagram.com")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return None
        post = parse_instagram_post_url(value)
        username = normalize_instagram_username(value)
        story = STORY.fullmatch(parsed.path)
        if kind in ("feed", "like") and post:
            prefix = "p" if post.type == "post" else "reel"
            return f"{HOME}{prefix}/{post.shortcode}/"
        if kind == "profile" and username:
            return f"{HOME}{username}/"
        if (
            kind == "story"
            and story
            and story[1].lower() != "highlights"
            and normalize_instagram_username(story[1])
        ):
            # Feed links often omit the item id. Reserve the owner's viewer once so an
            # id-less link and a link to that same story never create two viewing tasks.
            return f"{HOME}stories/{story[1].lower()}/"
        if kind == "navigate":
            if parsed.path == "/":
                return HOME
            return canonical(value, "profile") or canonical(value, "feed")
    except (ValueError, TypeError):
        pass
    return None


def candidates(page):
    """Only content actually exposed by the page, with stable URLs as identities."""
    result, seen = [], set()
    origin = canonical(page.url, "navigate")
    # The current feed exposes its story tray as labelled buttons without hrefs.
    # Only owners explicitly offered by those visible controls become candidates.
    for label in page.evaluate(STORY_BUTTONS) or []:
        match = STORY_LABEL.fullmatch(label or "")
        owner = next((part for part in match.groups() if part), None) if match else None
        url = canonical(f"{HOME}stories/{owner}/", "story") if owner else None
        if url and ("story", url) not in seen:
            seen.add(("story", url))
            result.append({"kind": "story", "url": url, "from_url": origin})
    for href in page.evaluate(LINKS) or []:
        for kind in ("story", "feed", "profile", "like", "navigate"):
            url = canonical(href, kind)
            if not url or url == origin or (kind, url) in seen:
                continue
            if kind == "navigate" and not origin:
                continue
            seen.add((kind, url))
            # Real links can open directly. Tray buttons need their source page.
            result.append(
                {"kind": kind, "url": url, "from_url": None if kind == "story" else origin}
            )
    return result


def media_visible(page, selector):
    return page.locator(selector).evaluate_all("""nodes => nodes.some(node => {
      const rect = node.getBoundingClientRect();
      if (rect.width < 100 || rect.height < 100 || node.closest('header')) return false;
      return node.tagName === 'IMG' ? node.complete && node.naturalWidth > 0 : node.readyState >= 2;
    })""")


def story_media_ready(page):
    """Confirm the central player, excluding the feed and side previews."""
    return page.locator("img, video").evaluate_all("""nodes => nodes.some(node => {
      if (node.closest('article, header')) return false;
      const rect = node.getBoundingClientRect();
      const x = innerWidth / 2, y = innerHeight / 2;
      if (rect.width < 100 || rect.height < innerHeight / 2 ||
          rect.left >= x || rect.right <= x || rect.top >= y || rect.bottom <= y ||
          !node.checkVisibility({opacityProperty: true, visibilityProperty: true})) return false;
      return node.tagName === 'IMG' ? node.complete && node.naturalWidth > 0 : node.readyState >= 2;
    })""")


class ActivityTab:
    def __init__(self, window):
        self.window = window
        self.page = window.context.new_page()
        self.page.set_default_timeout(600)
        if window.external:
            window.opened_pages.add(self.page)
        self.cdp = window.context.new_cdp_session(self.page)
        self.task_id = None
        self.opened_at = 0
        self.problem = None
        self.watch_at = None
        self.scroll_at = None
        self.timeline_posts = {}
        self.timeline_scrolls = 0
        self.page.on("response", self._response)
        self.page.on("requestfinished", self._request_finished)

    def _response(self, response):
        parsed = urlsplit(response.url)
        if parsed.hostname not in ("instagram.com", "www.instagram.com"):
            return
        if not (
            response.request.is_navigation_request()
            or parsed.path.startswith(("/api/", "/graphql"))
        ):
            return
        if response.status == 429:
            self.problem = "rate_limited"
            self.window.rate_limited = True
        elif response.status == 401:
            self.problem = "login"
        elif response.status == 403:
            self.problem = "action_blocked"
        elif response.status >= 500:
            self.problem = "instagram_error"
        elif response.status == 404 and response.request.is_navigation_request():
            self.problem = "unavailable"
        elif response.status >= 400:
            self.problem = "instagram_error"

    def _request_finished(self, request):
        """Instagram also returns HTTP 200 with a structured API failure."""
        parsed = urlsplit(request.url)
        if parsed.hostname not in (
            "instagram.com",
            "www.instagram.com",
        ) or not parsed.path.startswith(("/api/", "/graphql")):
            return
        try:
            response = request.response()
            if response is None or "json" not in response.headers.get("content-type", ""):
                return
            if int(response.headers.get("content-length", "0")) > 1_000_000:
                return
            payload = response.json()
        except Exception:
            return
        if not isinstance(payload, dict) or not (
            payload.get("status") == "fail" or payload.get("error_type") or payload.get("errors")
        ):
            return
        code = str(payload.get("error_type") or payload.get("message") or "")
        reason = {
            "checkpoint_required": "checkpoint",
            "challenge_required": "checkpoint",
            "login_required": "login",
            "feedback_required": "rate_limited",
            "rate_limit_error": "rate_limited",
        }.get(code, "instagram_error")
        if self.problem in (None, "instagram_error") or reason != "instagram_error":
            self.problem = reason
        if reason == "rate_limited":
            self.window.rate_limited = True

    def _problem(self):
        if self.problem:
            return self.problem
        main_problem = account_problem(self.window.page, lambda: self.window.rate_limited)
        tab_problem = account_problem(self.page, lambda: self.window.rate_limited)
        # Browser calls above also deliver completed API response events.
        return self.problem or main_problem or tab_problem

    def _blocked(self, guard):
        problem = self._problem()
        if problem:
            return {
                "outcome": "skipped" if problem == "unavailable" else "error",
                "reason": problem,
            }
        return None if guard() else {"outcome": "pending"}

    def _waiting(self, reason="unavailable"):
        if monotonic() - self.opened_at < LOAD_BUDGET:
            return {"outcome": "pending"}
        return {"outcome": "skipped", "reason": reason}

    def _scroll(self, guard):
        blocked = self._blocked(guard)
        if blocked:
            return blocked
        width, height = self.page.evaluate("() => [innerWidth, innerHeight]")
        self.page.mouse.move(width / 2, height / 2)
        self.page.mouse.wheel(0, round(height * 0.9))
        return None

    def _reels(self, task, guard):
        path = urlsplit(self.page.url).path
        now = monotonic()
        if self.page.url != REELS and not (
            REEL_PATH.fullmatch(path) and canonical(self.page.url, "feed")
        ):
            return self._waiting()
        if not media_visible(self.page, "video"):
            self.watch_at = None
            return self._waiting()
        elapsed = min(now - self.watch_at, LOAD_BUDGET) if self.watch_at is not None else 0
        watched = task.payload.get("progress", 0) + elapsed
        if watched >= REELS_SECONDS:
            return {"outcome": "done"}
        self.opened_at = self.watch_at = now
        if self.scroll_at is None:
            self.scroll_at = now
        if now - self.scroll_at >= REEL_SCROLL_SECONDS:
            blocked = self._scroll(guard)
            if blocked:
                return blocked
            self.scroll_at = now
        return {"outcome": "pending", "phase": "watch", "progress": watched}

    def _timeline(self, guard):
        if (
            canonical(self.page.url, "navigate") != HOME
            or not self.page.locator("main article").count()
        ):
            return self._waiting()
        for url in self.page.evaluate(TIMELINE_LINKS):
            post = canonical(url, "like")
            if post:
                self.timeline_posts.setdefault(post, None)
        if self.timeline_scrolls >= TIMELINE_SCROLLS:
            return {"outcome": "done", "posts": list(self.timeline_posts)[:100]}
        blocked = self._scroll(guard)
        if blocked:
            return blocked
        self.timeline_scrolls += 1
        return {"outcome": "pending", "phase": "scroll"}

    def step(self, task, guard):
        if task.phase == "open":
            self.problem = None
        blocked = self._blocked(guard)
        if blocked:
            return blocked
        kind = task.kind
        if kind not in (
            "discover",
            "story",
            "feed",
            "profile",
            "like",
            "navigate",
            "reels",
            "timeline",
        ):
            return {"outcome": "skipped", "reason": "unsupported"}
        target = {"reels": REELS, "timeline": HOME, "discover": HOME}.get(kind)
        if not target:
            target = canonical(task.payload.get("url"), kind)
        if not target:
            return {"outcome": "skipped", "reason": "unavailable"}
        if task.phase == "open":
            self.task_id, self.opened_at, self.problem = task.id, monotonic(), None
            self.watch_at = self.scroll_at = None
            self.timeline_posts.clear()
            self.timeline_scrolls = 0
            origin = canonical(task.payload.get("from_url"), "navigate")
            url = origin if kind in ("navigate", "story") and origin else target
            # Page.navigate returns after scheduling navigation. Network loading continues
            # independently of the DM driver; no browser sleeps or load waits are used.
            result = self.cdp.send("Page.navigate", {"url": url})
            if result.get("errorText"):
                return {"outcome": "error", "reason": "navigation_error"}
            return {"outcome": "pending", "phase": "inspect"}
        if self.task_id != task.id:
            return {"outcome": "error", "reason": "tab_lost"}
        if self.page.evaluate("document.readyState") == "loading":
            return self._waiting()
        dismiss = (
            self.page.get_by_role("dialog")
            .filter(has_text=NOTIFICATIONS)
            .get_by_role("button", name=DISMISS_BUTTON)
            .filter(visible=True)
            .first
        )
        if dismiss.is_visible():
            blocked = self._blocked(guard)
            if blocked:
                return blocked
            dismiss.click(timeout=600, no_wait_after=True)
            self.opened_at = monotonic()
            return {"outcome": "pending"}
        if kind == "reels":
            return self._reels(task, guard)
        if kind == "timeline":
            return self._timeline(guard)
        if kind == "navigate" and task.phase == "inspect":
            origin = canonical(task.payload.get("from_url"), "navigate")
            if not origin or canonical(self.page.url, "navigate") != origin:
                return self._waiting()
            links = self.page.locator("main a[href], header a[href]")
            for link in links.all()[:150]:
                href = link.get_attribute("href") or ""
                if href.startswith("/"):
                    href = HOME.rstrip("/") + href
                if canonical(href, "navigate") != target or not link.is_visible():
                    continue
                if link.get_attribute("target") not in (None, "", "_self"):
                    return {"outcome": "skipped", "reason": "unsupported"}
                blocked = self._blocked(guard)
                if blocked:
                    return blocked
                link.evaluate("e => e.click()")
                return {"outcome": "pending", "phase": "confirm"}
            return self._waiting()
        if kind == "story":
            path = STORY.fullmatch(urlsplit(self.page.url).path)
            viewer = canonical(self.page.url, "story")
            if (
                path
                and viewer == target
                and (path[2] or STORY_TITLE.fullmatch(self.page.title().strip()))
                and story_media_ready(self.page)
            ):
                # Close the story viewer as soon as this item is observed; autoplay must
                # not keep watching other stories after the task has finished.
                self.page.close()
                return {"outcome": "done"}
            if viewer and viewer != target:
                return {"outcome": "skipped", "reason": "story_changed"}
            origin = canonical(task.payload.get("from_url"), "navigate")
            if (
                task.phase == "inspect"
                and origin
                and canonical(self.page.url, "navigate") == origin
            ):
                owner = re.escape(STORY.fullmatch(urlsplit(target).path)[1])
                offered = (
                    self.page.get_by_role(
                        "button",
                        name=re.compile(rf"^(?:История {owner}|{owner}['’]s story)(?:,.*)?$", re.I),
                    )
                    .filter(visible=True)
                    .first
                )
                if offered.is_visible():
                    blocked = self._blocked(guard)
                    if blocked:
                        return blocked
                    offered.click(timeout=600, no_wait_after=True)
                    self.opened_at = monotonic()
                    return {"outcome": "pending", "phase": "confirm"}
            if viewer == target and task.phase != "view":
                button = self.page.get_by_role("button", name=VIEW_STORY).filter(visible=True).first
                if button.is_visible():
                    blocked = self._blocked(guard)
                    if blocked:
                        return blocked
                    button.click(timeout=600, no_wait_after=True)
                    self.opened_at = monotonic()
                    return {"outcome": "pending", "phase": "view"}
            return self._waiting()
        if canonical(self.page.url, "navigate") != target:
            return self._waiting()
        if kind == "like":
            # Post permalinks currently render a div layout; the feed uses articles.
            # Keep likes scoped to the post toolbar identified by its Comment control.
            toolbar = self.page.locator("main").first.locator(POST_ACTIONS).first
            unlike = toolbar.locator(UNLIKE).first
            if unlike.is_visible():
                return {
                    "outcome": "done" if task.phase == "confirm" else "skipped",
                    "reason": "" if task.phase == "confirm" else "already_liked",
                }
            if task.phase == "confirm":
                waiting = self._waiting()
                return (
                    waiting
                    if waiting["outcome"] == "pending"
                    else {
                        "outcome": "error",
                        "reason": "unconfirmed_like",
                    }
                )
            icon = toolbar.locator(LIKE).first
            if not icon.is_visible():
                return self._waiting()
            blocked = self._blocked(guard)
            if blocked:
                return blocked
            icon.locator('xpath=ancestor::*[self::button or @role="button"][1]').click(
                timeout=600, no_wait_after=True
            )
            return {"outcome": "pending", "phase": "confirm"}
        content = (
            self.page.locator("main header") if kind == "profile" else self.page.locator("main")
        )
        if not content.filter(visible=True).count():
            return self._waiting()
        if kind == "feed" and not media_visible(self.page, "main img, main video"):
            return self._waiting()
        return {"outcome": "done", "candidates": candidates(self.page)}
