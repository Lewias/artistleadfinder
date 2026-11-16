"""One Direct thread read through Instagram's interface in the sender's window.

The shell has opened the thread (`/direct/t/<id>/`) or, without a known thread, the
person's profile; from a profile the thread is opened with «Отправить сообщение», the way
a person does it. Nothing is typed and nothing is sent.

Which side wrote a message is read from where it stands, not from Instagram's class names:
the other person's messages sit on the left of the thread column, our own on the right,
dates and notices in the middle. The column is found from the message box up, so the
inbox list next to it (other people's previews) is never read.

Result: {"outcome": "read", "thread_id", "messages": [{"text", "side": "in"|"out"}]} or
{"outcome": "error", "error": <kind>}.
"""

import time
from collections.abc import Callable
from urllib.parse import urlparse

from ..direct_message import (
    CENTER,
    COLLECT,
    COMPOSER,
    DISMISS_BUTTON,
    FOLLOW_BUTTON,
    HEADER,
    MESSAGE_BUTTON,
    PANES,
    THREAD,
    UNAVAILABLE,
    USERNAME,
    header_match,
)
from ..instagram_state import account_problem

# Seconds; the whole read stays inside the shell's call limit.
PROFILE_WAIT = 8.0
DIALOG_WAIT = 12.0
SETTLE = 2.0
SCROLL_ROUNDS = 3
SCROLL_PAUSE_MS = 1200
POLL_MS = 250
MAX_MESSAGES = 300
MAX_TEXT = 2000

# Up one screen in the message list; True when it moved (older messages may load).
SCROLL_UP = """(index) => {
  const boxes = [...document.querySelectorAll('[role="textbox"][contenteditable="true"]')];
  const box = index === undefined || index === null ? boxes[boxes.length - 1] : boxes[index];
  if (!box) return false;
  let pane = box;
  while (pane.parentElement && pane.getBoundingClientRect().height < innerHeight * 0.6)
    pane = pane.parentElement;
  const lists = [...pane.querySelectorAll('*')].filter(node => {
    const style = getComputedStyle(node);
    return /(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 20;
  });
  const list = lists.sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
  if (!list) return false;
  const before = list.scrollTop;
  list.scrollBy(0, -list.clientHeight);
  return list.scrollTop !== before;
}"""


def fail(error: str) -> dict:
    return {"outcome": "error", "error": error}


def read_thread(
    page,
    username: str,
    outbound: list[str] | None = None,
    *,
    rate_limited: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    parsed = urlparse(page.url)
    if (parsed.hostname or "").lower() not in {"instagram.com", "www.instagram.com"}:
        return fail("no_instagram_tab")
    if not USERNAME.match(username):
        return fail("bad_request")
    problem = account_problem(page, rate_limited)
    if problem:
        return fail(problem)
    in_thread = bool(THREAD.match(parsed.path))
    if not in_thread and parsed.path.rstrip("/").lower() != f"/{username}":
        return fail("no_instagram_tab")
    try:
        if not in_thread:
            opened = open_from_profile(page, clock)
            if opened:
                return fail(opened)
        composer = page.locator(COMPOSER)
        deadline = clock() + DIALOG_WAIT
        while not composer.count():
            if page.get_by_text(UNAVAILABLE).count():
                return fail("not_found")
            dismiss = page.get_by_role("button", name=DISMISS_BUTTON)
            if dismiss.count():
                dismiss.first.click(timeout=3000)
            if clock() > deadline:
                return fail("rate_limited" if rate_limited() else "dialog")
            page.wait_for_timeout(POLL_MS)
        page.wait_for_timeout(int(SETTLE * 1000))
        index = this_chat(page, username)
        if index is None:
            return fail("dialog")
        messages = collect(page, outbound or [], index)
    except Exception:
        return fail("rate_limited" if rate_limited() else "dialog")
    thread = THREAD.match(urlparse(page.url).path)
    return {
        "outcome": "read",
        "thread_id": thread.group(1) if thread else "",
        "messages": messages,
    }


def open_from_profile(page, clock) -> str | None:
    """«Отправить сообщение» on the profile; an error kind when there is no thread. A
    profile that still shows only «Подписаться» after the whole wait has no thread with
    this account; a slow button is waited for."""
    main = page.locator("main")
    message = main.get_by_role("button", name=MESSAGE_BUTTON)
    follow = main.get_by_role("button", name=FOLLOW_BUTTON)
    deadline = clock() + PROFILE_WAIT
    while not message.count():
        if page.get_by_text(UNAVAILABLE).count():
            return "not_found"
        if clock() > deadline:
            return "no_thread" if follow.count() else "dialog"
        page.wait_for_timeout(POLL_MS)
    message.first.click(timeout=5000)
    return None


def this_chat(page, username: str) -> int | None:
    """The message box of this person's chat: the one whose header names them, or the
    only chat open. Several chats and none of them theirs: None, nothing is read."""
    panes = [pane for pane in page.evaluate(PANES, [HEADER]) or [] if pane.get("visible")]
    verdicts = [(pane, header_match(pane, username)) for pane in panes]
    matched = [pane for pane, verdict in verdicts if verdict == "match"]
    if matched:
        return int(matched[-1]["index"])
    if len(panes) == 1 and verdicts[0][1] == "unknown":
        return int(panes[0]["index"])
    return None


def collect(page, outbound: list[str], index: int | None = None) -> list[dict]:
    """The visible messages, then up to SCROLL_ROUNDS screens back until our own first
    message is in view: replies come after it."""
    ours = {" ".join(text.split()) for text in outbound if text.strip()}
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for round_ in range(SCROLL_ROUNDS + 1):
        reached = False
        thread = page.evaluate(COLLECT, [HEADER, CENTER, index]) or {}
        for item in thread.get("messages") or []:
            text = str(item.get("text") or "")[:MAX_TEXT]
            side = item.get("side")
            flat = " ".join(text.split())
            if flat in ours:
                side, reached = "out", True
            if side not in ("in", "out") or (flat, side) in seen:
                continue
            seen.add((flat, side))
            found.append({"text": text, "side": side})
        if reached or round_ == SCROLL_ROUNDS or not page.evaluate(SCROLL_UP, index):
            break
        page.wait_for_timeout(SCROLL_PAUSE_MS)
    return found[:MAX_MESSAGES]
