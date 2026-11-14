"""Visible Instagram controls used by the warm-up. Never toggles an existing like/follow."""

import re
from urllib.parse import urlparse

from .direct_message import DISMISS_BUTTON, FOLLOW_BUTTON, LOGIN_BUTTON
from .errors import UserError
from .lead_scout.profiles.normalize import profile_from_fields

# Instagram exposes both the icon's label and the adjacent text in the link name,
# e.g. "Поиск Поиск". Match the navigation word rather than the entire combined name.
SEARCH = re.compile(r"\bsearch\b|поиск", re.I)
FOLLOWING = re.compile(
    r"\bfollowing\b|\brequested\b|вы подписаны|подписки|запрос отправлен", re.I
)
LIKE = 'svg[aria-label="Like"], svg[aria-label="Нравится"]'
UNLIKE = 'svg[aria-label="Unlike"], svg[aria-label="Не нравится"]'
SEARCH_INPUT = (
    'input[placeholder*="Search" i], input[placeholder*="Поиск" i], '
    'input[aria-label*="Search" i], input[aria-label*="Поиск" i]'
)
USER_LINKS = r"""() => {
  const blocked = new Set(['accounts','explore','reels','direct','stories','about','legal']);
  const found = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) continue;
    const match = url.pathname.match(/^\/([a-zA-Z0-9_.]{1,30})\/$/);
    if (!match || blocked.has(match[1].toLowerCase()) || !a.getBoundingClientRect().height)
      continue;
    const name = match[1].toLowerCase();
    if ((a.innerText || '').toLowerCase().includes(name) && !found.includes(name)) found.push(name);
  }
  return found.slice(0, 50);
}"""
PROFILE_DOM = r"""() => {
  const header = document.querySelector('main header');
  const text = header?.innerText || '';
  const link = header?.querySelector('a[href$="/followers/"], a[href$="/followers"]');
  const titled = link?.querySelector('[title]');
  return {text, followers: titled?.getAttribute('title') || link?.getAttribute('title') || '',
    private: /private|закрытый аккаунт|закрытый профиль/i.test(
      document.querySelector('main')?.innerText || '')};
}"""


def check_window(window):
    page = window.page
    parsed = urlparse(page.url)
    if window.rate_limited:
        raise UserError("Instagram ограничил запросы. Прогрев остановлен.")
    if (parsed.hostname or "").lower() not in {"instagram.com", "www.instagram.com"}:
        raise UserError("В окне аккаунта не открыта страница Instagram.")
    if parsed.path.startswith(("/challenge", "/checkpoint")):
        raise UserError("Instagram запросил проверку. Выполните её вручную в окне аккаунта.")
    if (
        parsed.path.startswith("/accounts/")
        or page.get_by_role("button", name=LOGIN_BUTTON).first.is_visible()
    ):
        raise UserError("Войдите в Instagram в окне выбранного аккаунта.")
    body = page.locator("body").inner_text(timeout=3000)[:30000]
    blocked = r"try again later|action blocked|попробуйте позже|действие заблокировано"
    if re.search(blocked, body, re.I):
        raise UserError("Instagram ограничил действия. Прогрев остановлен.")


def warmup_action(runtime, window, identifier, action, value):
    check_window(window)
    page = window.page
    if action == "check":
        return {"ok": True}
    if action == "search":
        # Instagram can cover its navigation with the initial notifications dialog.
        dismiss = page.get_by_role("button", name=DISMISS_BUTTON).filter(visible=True).first
        if dismiss.is_visible():
            dismiss.click(timeout=3000)
        control = (
            page.get_by_role("link", name=SEARCH)
            .or_(page.get_by_role("button", name=SEARCH))
            .filter(visible=True)
            .first
        )
        box = page.locator(SEARCH_INPUT).filter(visible=True).first
        if not box.is_visible():
            control.click(timeout=5000)
        box.fill(value, timeout=5000)
        page.wait_for_timeout(2500)
        names = page.evaluate(USER_LINKS)
        check_window(window)
        return {"usernames": names}
    if action in {"profile", "follow"}:
        if urlparse(page.url).path.rstrip("/").lower() != f"/{value}":
            raise UserError("Страница магазина изменилась. Прогрев остановлен.")
    if action == "profile":
        raw = runtime.page_profile(identifier, value)
        if raw:
            return profile_from_fields(raw, "api").as_dict()
        facts = page.evaluate(PROFILE_DOM)
        digits = re.sub(r"[\s,\u00a0\u202f]", "", facts["followers"])
        return {
            "biography": facts["text"],
            "is_private": facts["private"],
            "followers_count": int(digits) if digits.isdigit() else None,
        }
    if action == "follow":
        header = page.locator("main header")
        if header.get_by_role("button", name=FOLLOWING).first.is_visible():
            return {
                "confirmed": False,
                "attempted": False,
                "detail": "Уже подписаны или запрос уже отправлен",
            }
        button = header.get_by_role("button", name=FOLLOW_BUTTON).first
        if not button.is_visible():
            return {"confirmed": False, "attempted": False, "detail": "Кнопка подписки не найдена"}
        button.click(timeout=5000)
        try:
            header.get_by_role("button", name=FOLLOWING).first.wait_for(timeout=5000)
        except Exception:
            check_window(window)
            raise UserError("Подписка не подтверждена. Прогрев остановлен.") from None
        check_window(window)
        return {"confirmed": True, "detail": "Подписка подтверждена"}
    if action == "like":
        if urlparse(page.url).path != "/":
            raise UserError("Лента аккаунта закрыта. Прогрев остановлен.")
        articles = page.locator("main article")
        for index in range(min(articles.count(), 12)):
            article = articles.nth(index)
            # A liked publication is skipped even if a comment has a separate Like.
            if article.locator(UNLIKE).count() or not article.locator(LIKE).count():
                continue
            icon = article.locator(LIKE).first
            button = icon.locator('xpath=ancestor::*[self::button or @role="button"][1]')
            button.click(timeout=5000)
            try:
                article.locator(UNLIKE).first.wait_for(timeout=5000)
            except Exception:
                check_window(window)
                raise UserError("Лайк не подтверждён. Прогрев остановлен.") from None
            check_window(window)
            return {"confirmed": True, "detail": "Публикация в ленте отмечена лайком"}
        page.mouse.wheel(0, 700)
        page.wait_for_timeout(1000)
        check_window(window)
        return {"confirmed": False, "detail": "Лента просмотрена; доступных новых публикаций нет"}
    raise UserError("Неизвестное действие прогрева.")
