"""Threads through the owner's existing Chromium profile, without exporting credentials.

The session stays in BrowserSessions' encrypted vault. This transport uses the website
interface, not a web session disguised as a Graph API access token. An uncertain click
is never replayed: the queue verifies recent posts or asks for manual review.
"""

import re
import uuid
from urllib.parse import urlparse

from .api import ThreadsError

HOME = "https://www.threads.com/"
HOSTS = {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}
USERNAME = re.compile(r"[A-Za-z0-9._]{1,64}")

IDENTITY = r"""() => {
  const labels = new Set(['profile', 'your profile', 'профиль', 'ваш профиль']);
  const hosts = new Set(['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net']);
  for (const a of document.querySelectorAll('a[href]')) {
    const label = [a.getAttribute('aria-label'), a.getAttribute('title'),
      a.closest('nav,[role="navigation"]') ? a.textContent : '',
      ...Array.from(a.querySelectorAll('[aria-label],svg title'),
        n => n.getAttribute('aria-label') || n.textContent)];
    if (!label.some(v => labels.has((v || '').trim().toLowerCase()))) continue;
    const u = new URL(a.href, location.href);
    const m = u.pathname.match(/^\/@([a-zA-Z0-9._]+)\/?$/);
    if (u.protocol === 'https:' && hosts.has(u.hostname) && m)
      return {username: m[1], name: m[1]};
  }
  return {};
}"""

POSTS = r"""username => {
  const result = new Map();
  for (const a of document.querySelectorAll('a[href*="/post/"]')) {
    const u = new URL(a.href, location.href);
    const m = u.pathname.match(/^\/@([^/]+)\/post\/([A-Za-z0-9_-]+)\/?$/);
    if (u.origin !== location.origin || !m ||
        m[1].toLowerCase() !== username.toLowerCase()) continue;
    const root = a.closest('article,[role="article"],[data-pressable-container]');
    if (!root) continue;
    const text = root.querySelector('[data-thread-text],[data-testid="post-text"]');
    const candidates = text ? [text.innerText] : Array.from(
      root.querySelectorAll('[dir="auto"],[data-lexical-text="true"]'), n => n.innerText);
    const time = root.querySelector('time[datetime]');
    result.set(m[2], {id: m[2], permalink: u.origin + u.pathname,
      text: candidates[0] || '', text_candidates: candidates,
      timestamp: time?.getAttribute('datetime') || null});
  }
  return Array.from(result.values()).slice(0, 30);
}"""


def identity(page) -> dict:
    if urlparse(page.url).hostname not in HOSTS:
        return {}
    try:
        found = page.evaluate(IDENTITY)
        username = str(found.get("username") or "")
        return found if USERNAME.fullmatch(username) else {}
    except Exception:
        return {}


def login_diagnostic(page) -> dict:
    """Only public UI labels and URL paths; no page bodies, cookies or query strings."""
    parsed = urlparse(page.url)
    try:
        hints = page.evaluate(r"""() => Array.from(
          document.querySelectorAll('a[href],svg[aria-label],[role="link"]')
        ).map(n => ({label: (n.getAttribute('aria-label') ||
             n.querySelector('title')?.textContent || n.textContent || '').trim().slice(0, 80),
          path: (() => {try {return new URL(n.closest('a[href]')?.href ||
            n.getAttribute('href') || '', location.href).pathname.slice(0, 120)}
            catch {return ''}})()}))
          .filter(n => /profile|профил/i.test(n.label) || /^\/@[^/]+\/?$/.test(n.path))
          .slice(0, 12)""")
    except Exception:
        hints = []
    return {"host": parsed.hostname, "path": parsed.path[:120], "hints": hints}


def _guard(page, username: str) -> None:
    current = identity(page)
    if not current:
        raise ThreadsError("auth", "Войдите в Threads в выбранном браузерном профиле.")
    if current["username"].lower() != username.lower():
        raise ThreadsError(
            "auth", "В браузере другой аккаунт Threads. Проверьте профиль перед отправкой."
        )
    body = " ".join(page.locator('[role="alert"]').all_text_contents()).lower()
    if any(
        part in body for part in ("try again later", "попробуйте позже", "temporarily restricted")
    ):
        raise ThreadsError(
            "auth", "Threads ограничил действия. Проверьте аккаунт в браузере вручную."
        )


def recent(page, username: str) -> list[dict]:
    if not USERNAME.fullmatch(username):
        raise ThreadsError("invalid", "Некорректное имя профиля Threads.")
    try:
        page.goto(f"{HOME}@{username}", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_function("() => !!(" + IDENTITY + ")().username", timeout=8000)
        _guard(page, username)
        page.wait_for_timeout(700)
        return page.evaluate(POSTS, username)
    except ThreadsError:
        raise
    except Exception:
        if not identity(page):
            raise ThreadsError(
                "auth", "Войдите в Threads и завершите проверку в браузере."
            ) from None
        raise ThreadsError(
            "network", "Не удалось прочитать профиль Threads. Проверьте окно браузера."
        ) from None


def _same_text(item: dict, text: str) -> bool:
    return any(
        str(value).strip() == text.strip()
        for value in [item.get("text", ""), *item.get("text_candidates", [])]
    )


def publish(page, username: str, text: str) -> dict:
    before = {item["id"] for item in recent(page, username)}
    submitted = False
    try:
        opener = page.get_by_role(
            "button",
            name=re.compile(r"^(Create|New thread|Создать|Новая ветка|Написать)(?:\s.*)?$", re.I),
        )
        if not opener.count():
            opener = page.get_by_text(re.compile(r"^(What's new\??|Что нового\??)$", re.I))
        opener.first.click(timeout=8000)
        dialog = (
            page.get_by_role("dialog")
            .filter(has=page.locator('[contenteditable="true"],textarea'))
            .last
        )
        editor = dialog.locator(
            '[contenteditable="true"][role="textbox"],textarea,[contenteditable="true"]'
        ).first
        editor.fill(text, timeout=8000)
        if editor.evaluate("n => n.value ?? n.innerText").strip() != text.strip():
            raise ThreadsError("invalid", "Редактор Threads не принял текст целиком.")
        _guard(page, username)
        button = dialog.get_by_role(
            "button", name=re.compile(r"^(Post|Publish|Опубликовать|Публикация)$", re.I)
        )
        button.wait_for(state="visible", timeout=5000)
        if not button.is_enabled():
            raise ThreadsError(
                "invalid", "Threads не разрешил публикацию. Проверьте текст в браузере."
            )
        submitted = True
        button.click(timeout=8000)
        # An empty composer alone is not proof of a published post.
        dialog.wait_for(state="hidden", timeout=12000)
        for _ in range(3):
            for item in recent(page, username):
                if item["id"] not in before and _same_text(item, text):
                    return item
            page.wait_for_timeout(1500)
        raise ThreadsError(
            "network", "Threads не подтвердил публикацию: проверяем профиль перед повтором."
        )
    except ThreadsError as error:
        if submitted and error.kind != "network":
            raise ThreadsError(
                "network", "Результат публикации неизвестен: нужна проверка профиля."
            ) from None
        raise
    except Exception:
        raise ThreadsError(
            "network" if submitted else "invalid",
            "Результат публикации неизвестен: проверяем профиль."
            if submitted
            else "Не удалось открыть редактор Threads. Проверьте окно браузера.",
        ) from None


class BrowserClient:
    """Adapter for the existing durable queue. A web container is a local attempt ID;
    after a submitted click its status stays inconclusive until a recent-post proof.
    """

    def __init__(self, browser, profile_id: str, username: str, text: str, uncertain: bool):
        self.browser, self.profile_id, self.username = browser, profile_id, username
        self.text, self.uncertain = text, uncertain
        self.published = None

    def create_text(self, user_id, text):
        if not self.browser.is_open(self.profile_id):
            raise ThreadsError("server", "Ожидаем открытия браузерного профиля Threads.")
        return "web-" + uuid.uuid4().hex

    def container(self, container_id):
        if not self.browser.is_open(self.profile_id):
            raise ThreadsError("server", "Ожидаем открытия браузерного профиля Threads.")
        return {"status": "IN_PROGRESS" if self.uncertain else "FINISHED"}

    def publish(self, user_id, container_id):
        try:
            self.published = self.browser.threads_publish(self.profile_id, self.username, self.text)
        except ThreadsError:
            raise
        except Exception:
            raise ThreadsError(
                "network", "Окно Threads закрылось: проверяем результат публикации."
            ) from None
        return self.published["id"]

    def media(self, media_id):
        if self.published and self.published["id"] == media_id:
            return self.published
        return next((item for item in self.recent(None) if item["id"] == media_id), {})

    def recent(self, user_id):
        try:
            return self.browser.threads_recent(self.profile_id, self.username)
        except ThreadsError:
            raise
        except Exception:
            raise ThreadsError(
                "server", "Откройте профиль Threads для проверки публикации."
            ) from None
