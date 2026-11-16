"""What Instagram shows about the account and the recipient, read from the URL, the
window's HTTP 429 flag and Instagram's own notices.

Notices are read only where Instagram puts them: dialogs and alert / toast regions that
hold no message box. A chat window is itself a dialog on some accounts, so a dialog with
a message box is never read here: a conversation saying "try again later" is a message,
not a warning.

`account_problem` names the account's state (login, checkpoint, limit…) or None. The
sender's health and the result of one send are kept apart by the callers: a limit seen
after Enter pauses the account while the message itself stays unconfirmed.
"""

import re
from collections.abc import Callable
from urllib.parse import urlparse

LOGIN_BUTTON = re.compile(r"^\s*(log in|login|войти)\s*$", re.I)
SIGNUP_BUTTON = re.compile(r"^\s*(sign up|зарегистрироваться|регистрация)\s*$", re.I)

ACTION_BLOCKED = re.compile(
    r"(try again later|action blocked|we restrict certain activity|we limit how often"
    r"|please wait a few minutes|попробуйте (ещё раз |еще раз )?позже|действие заблокировано"
    r"|мы ограничиваем|подождите несколько минут)",
    re.I,
)
PHONE = re.compile(
    r"(add (a |your )?phone number|confirm your (phone|mobile) number"
    r"|enter your (mobile|phone) number|добавьте номер телефона"
    r"|подтвердите (свой |ваш )?номер|введите (свой |ваш )?номер телефона)",
    re.I,
)
CODE = re.compile(
    r"(enter (the |your )?(security |confirmation |login |6-digit )?code|we sent (you )?a code"
    r"|check your (email|sms|text messages) for a code|введите (код|шестизначный код)"
    r"|код подтверждения|код безопасности|мы отправили (вам )?код)",
    re.I,
)
PASSWORD = re.compile(
    r"(change your password|reset your password|create a new password|смените пароль"
    r"|измените пароль|сбросьте пароль|создайте новый пароль)",
    re.I,
)
CHALLENGE = re.compile(
    r"(confirm it'?s you|help us confirm|we suspect automated behavior|suspicious (login|activity)"
    r"|подтвердите, что это вы|подтвердите личность|подозрительн)",
    re.I,
)
SESSION = re.compile(
    r"(session (has )?expired|please log in again|log in again to continue"
    r"|сеанс (истёк|истек)|войдите (ещё раз|еще раз|снова))",
    re.I,
)
# The recipient does not take messages from this account.
RECIPIENT_CLOSED = re.compile(
    r"(can'?t message this account|cannot message this account|can'?t send (a )?messages? to"
    r"|this account can'?t receive|isn'?t receiving messages|not accepting messages"
    r"|you can'?t message|нельзя (отправить сообщение|написать)|не может получать сообщения"
    r"|не принимает сообщения|вы не можете (отправить сообщение|написать))",
    re.I,
)
# Checked in this order: the more specific request first.
NOTICES = (
    ("code_required", CODE),
    ("phone_required", PHONE),
    ("password_reset", PASSWORD),
    ("checkpoint", CHALLENGE),
    ("session_expired", SESSION),
    ("action_blocked", ACTION_BLOCKED),
)
URL_PROBLEMS = (
    ("/accounts/login", "login"),
    ("/accounts/password", "password_reset"),
    ("/accounts/suspended", "checkpoint"),
    ("/accounts/disabled", "checkpoint"),
    ("/challenge", "checkpoint"),
    ("/auth_platform", "code_required"),
    ("/two_factor", "code_required"),
)

# Texts of Instagram's notices: dialogs and alerts without a message box, short ones only.
READ_NOTICES = """() => {
  const boxes = '[role="textbox"][contenteditable="true"]';
  const nodes = [...document.querySelectorAll(
    '[role="dialog"], [role="alertdialog"], [role="alert"], [aria-live="assertive"]'
  )];
  const texts = [];
  for (const node of nodes) {
    const chat = node.closest('[role="dialog"]');
    if (node.querySelector(boxes) || (chat && chat.querySelector(boxes))) continue;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    const text = (node.innerText || '').trim();
    if (text && text.length <= 1500) texts.push(text);
  }
  return texts.slice(0, 10);
}"""


def url_problem(url: str) -> str | None:
    path = urlparse(url).path
    for prefix, kind in URL_PROBLEMS:
        if path.startswith(prefix):
            return kind
    return None


def notice_problem(texts: list[str]) -> str | None:
    for text in texts:
        for kind, pattern in NOTICES:
            if pattern.search(text):
                return kind
    return None


def login_overlay(page) -> bool:
    """The logged-out profile: a login form or both «Log in» and «Sign up» buttons."""
    login = any(page.get_by_role(role, name=LOGIN_BUTTON).count() for role in ("button", "link"))
    signup = any(page.get_by_role(role, name=SIGNUP_BUTTON).count() for role in ("button", "link"))
    return bool(page.locator('input[name="username"]').count() or (login and signup))


def account_problem(page, rate_limited: Callable[[], bool] = lambda: False) -> str | None:
    """The account's problem visible now, or None. Never raises: a page that cannot be
    read shows no problem here (the caller handles the unreadable page itself)."""
    found = url_problem(page.url)
    if found:
        return found
    if rate_limited():
        return "rate_limited"
    try:
        found = notice_problem(page.evaluate(READ_NOTICES) or [])
        if found:
            return found
        if login_overlay(page):
            return "login"
    except Exception:
        return None
    return None
