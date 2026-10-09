"""One outreach message sent through Instagram's own interface in the sender's window.

The shell has just opened the recipient's profile. From there the message goes the way
a person sends it by hand: «Отправить сообщение», the text typed into the thread's
message box (`MessageTyper`), verified, Enter. Nothing is disguised and nothing is
retried here; the worker decides on retries.

Someone already in this account's Direct is never written to: when the opened thread
already has messages (ours written by hand, theirs, a request), nothing is typed and the
result is "existing_thread".

Result: {"outcome": "sent", "thread_id"} or {"outcome": "error", "error": <kind>} with the
kinds of `outreach.worker.OUTCOMES`. Failures before Enter never sent anything; after
Enter anything unclear is "unconfirmed", which the core never resends.
"""

import re
import time
from collections.abc import Callable
from urllib.parse import urlparse

from .message_typer import MessageTyper, MessageTypingMismatch

USERNAME = re.compile(r"^[a-z0-9_.]{1,30}$")
MAX_TEXT = 1000
# Exact button names; the «Сообщения» / «Messages» inbox dock never matches.
MESSAGE_BUTTON = re.compile(
    r"^\s*(отправить сообщение|написать|сообщение|message|send message)\s*$", re.I
)
FOLLOW_BUTTON = re.compile(r"^\s*(подписаться|подписаться в ответ|follow|follow back)\s*$", re.I)
DISMISS_BUTTON = re.compile(r"^\s*(не сейчас|not now)\s*$", re.I)
UNAVAILABLE = re.compile(
    r"(эта страница недоступна|страница недоступна|sorry, this page isn't available"
    r"|profile isn't available)",
    re.I,
)
SEND_FAILED = re.compile(
    r"(не удалось отправить|не отправлено|failed to send|couldn't send|not sent)", re.I
)
COMPOSER = '[role="textbox"][contenteditable="true"]'
THREAD = re.compile(r"^/direct/t/([0-9A-Za-z_-]{1,80})/?")

COLLECT = """([header, center]) => {
  const box = [...document.querySelectorAll('[role="textbox"][contenteditable="true"]')].pop();
  if (!box) return null;
  let pane = box;
  while (pane.parentElement && pane.getBoundingClientRect().height < innerHeight * 0.6)
    pane = pane.parentElement;
  const area = pane.getBoundingClientRect();
  const middle = area.left + area.width / 2;
  const messages = [];
  for (const node of pane.querySelectorAll('[dir="auto"]')) {
    if (node.querySelector('[dir="auto"]') || node.closest('[role="textbox"]')) continue;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.top < area.top + header) continue;
    const text = (node.innerText || '').trim();
    if (!text) continue;
    const offset = (rect.left + rect.right) / 2 - middle;
    const side = Math.abs(offset) < area.width * center ? 'middle' : offset < 0 ? 'in' : 'out';
    messages.push({ text, side });
  }
  return messages;
}"""
HEADER = 72  # px: the thread header with the name and buttons
CENTER = 0.06  # share of the column width around the middle: dates and notices

# Seconds. The whole send stays well inside the shell's 45 s call limit.
PROFILE_WAIT = 8.0
FOLLOW_GRACE = 1.2
DIALOG_WAIT = 12.0
CONFIRM_WAIT = 6.0
SETTLE = 2.0
# An opened thread shows its earlier messages within this time; until then it is not
# taken for an empty one.
HISTORY_WAIT = 2.5
POLL_MS = 250


def fail(error: str) -> dict:
    return {"outcome": "error", "error": error}


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def has_history(page, clock: Callable[[], float] = time.monotonic) -> bool:
    """Messages in the open thread: ours or the other person's, on either side."""
    deadline = clock() + HISTORY_WAIT
    while True:
        messages = page.evaluate(COLLECT, [HEADER, CENTER]) or []
        if any(item.get("side") in ("in", "out") for item in messages):
            return True
        if clock() > deadline:
            return False
        page.wait_for_timeout(POLL_MS)


def send_direct(
    page,
    username: str,
    text: str,
    *,
    rate_limited: Callable[[], bool] = lambda: False,
    typer: MessageTyper | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    typer = typer or MessageTyper()
    parsed = urlparse(page.url)
    if (parsed.hostname or "").lower() not in {"instagram.com", "www.instagram.com"}:
        return fail("no_instagram_tab")
    if parsed.path.startswith("/accounts/login"):
        return fail("login")
    if parsed.path.startswith("/challenge"):
        return fail("checkpoint")
    if not USERNAME.match(username) or not text.strip() or len(text) > MAX_TEXT:
        return fail("bad_request")
    if parsed.path.rstrip("/").lower() != f"/{username}":
        return fail("no_instagram_tab")

    # 1. The profile's buttons. Only «Подписаться» means its owner takes no messages.
    try:
        main = page.locator("main")
        message = main.get_by_role("button", name=MESSAGE_BUTTON)
        follow = main.get_by_role("button", name=FOLLOW_BUTTON)
        deadline, follow_seen = clock() + PROFILE_WAIT, None
        while True:
            if message.count():
                break
            if page.get_by_text(UNAVAILABLE).count():
                return fail("not_found")
            if follow.count():
                follow_seen = follow_seen or clock()
                if clock() - follow_seen > FOLLOW_GRACE:
                    return fail("messages_closed")
            if clock() > deadline:
                return fail("dialog")
            page.wait_for_timeout(POLL_MS)

        # 2. The thread with its message box.
        message.first.click(timeout=5000)
        composer = page.locator(COMPOSER)
        deadline = clock() + DIALOG_WAIT
        while not composer.count():
            dismiss = page.get_by_role("button", name=DISMISS_BUTTON)
            if dismiss.count():
                dismiss.first.click(timeout=3000)
            if clock() > deadline:
                return fail("dialog")
            page.wait_for_timeout(POLL_MS)
        if has_history(page, clock):
            return fail("existing_thread")
        box = composer.last
        # Keyboard events, one character at a time; a leftover draft is cleared first.
        # The typer verifies the box and never sends.
        typer.type_message(page, box, text)
    except MessageTypingMismatch as error:
        return {
            **fail("typing_mismatch"),
            "expected_length": error.expected_length,
            "actual_length": error.actual_length,
        }
    except Exception:
        # Nothing has been sent yet: the worker may try again later.
        return fail("dialog")

    # 3. Send. From here on a doubt is "unconfirmed" and never resent.
    try:
        page.keyboard.press("Enter")
        started = clock()
        while True:
            page.wait_for_timeout(POLL_MS)
            if page.get_by_text(SEND_FAILED).count():
                return fail("rate_limited" if rate_limited() else "rejected")
            empty = not normalized(box.inner_text())
            if empty and clock() - started >= SETTLE:
                break
            if clock() - started > CONFIRM_WAIT:
                return fail("unconfirmed")
    except Exception:
        return fail("unconfirmed")
    thread = THREAD.match(urlparse(page.url).path)
    return {"outcome": "sent", "thread_id": thread.group(1) if thread else ""}
