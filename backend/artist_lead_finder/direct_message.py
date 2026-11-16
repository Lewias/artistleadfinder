"""One outreach message sent through Instagram's own interface in the sender's window.

The shell has opened the recipient's profile (or, for a follow-up with a known thread, the
thread). From there the message goes the way a person sends it by hand: the thread is
opened, checked to be this recipient's, the text typed into its message box
(`MessageTyper`), verified, Enter. Nothing is disguised and nothing is resent here; the
worker decides on retries.

Opening the thread (before anything is typed, bounded, never after Enter):
1. the profile's «Отправить сообщение» (a recipient picker opened by it is handled);
2. the profile once more after a reload, when the button was slow or missing;
3. a new message from Direct: the exact username is searched and picked.
A profile that shows only «Подписаться» is "messages_closed" only when Direct also
refuses this recipient; a slow button alone never is.

The thread must be this recipient's: its header names them, or it is the single chat the
profile's own button just opened. Several chat windows, a header naming someone else or
an unverifiable thread send nothing.

Initial outreach never writes into a thread that already has messages ("existing_thread").
A follow-up (`mode="followup"`) writes only into the thread that shows our previous
message, and only when the person has not answered after it ("reply_detected").

After Enter the result is "sent" only when the interface shows it: a new outgoing message
with this text in the same chat (counted against the state captured before Enter, so an
earlier identical message never counts), the box cleared, no error or pending mark, all
steady for a moment. An explicit refusal shown by Instagram is an error. Anything else —
no new message, an unreadable page, a closed window, a lost box, a timeout — is
"unconfirmed", which the core never resends. "sent" means Instagram's interface showed the
message as sent; it says nothing about delivery or reading.

The account's state (login, checkpoint, phone or code request, password reset, limits,
HTTP 429) is checked before opening, before typing, right before Enter and while
confirming. Seen after Enter it is reported beside the result (`account`), never instead
of it: the message stays unconfirmed or sent, and the core pauses the account.

`guard` asks the core whether the job may still be sent (stop, do-not-contact, a reply…);
it is asked before opening, while typing and right before Enter, never after.

Result: {"outcome": "sent", "thread_id", "confirmed": "interface"[, "account"]} or
{"outcome": "error", "error": <kind>, ...} with the kinds of `outreach.worker.OUTCOMES`.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .instagram_state import RECIPIENT_CLOSED, account_problem, login_overlay, url_problem
from .message_typer import (
    MessageTyper,
    MessageTypingCancelled,
    MessageTypingMismatch,
    clear_input,
)

log = logging.getLogger(__name__)

USERNAME = re.compile(r"^[a-z0-9_.]{1,30}$")
MAX_TEXT = 1000
# Exact button names; the «Сообщения» / «Messages» inbox dock never matches.
MESSAGE_BUTTON = re.compile(
    r"^\s*(отправить сообщение|написать|сообщение|message|send message)\s*$", re.I
)
FOLLOW_BUTTON = re.compile(r"^\s*(подписаться|подписаться в ответ|follow|follow back)\s*$", re.I)
DISMISS_BUTTON = re.compile(r"^\s*(не сейчас|not now)\s*$", re.I)
NEW_MESSAGE_BUTTON = re.compile(r"^\s*(new message|новое сообщение|send message)\s*$", re.I)
CHAT_BUTTON = re.compile(r"^\s*(chat|next|чат|далее)\s*$", re.I)
UNAVAILABLE = re.compile(
    r"(эта страница недоступна|страница недоступна|sorry, this page isn't available"
    r"|profile isn't available)",
    re.I,
)
SEND_FAILED = re.compile(
    r"(не удалось отправить|не отправлено|failed to send|couldn'?t send|not sent|tap to retry"
    r"|нажмите, чтобы повторить)",
    re.I,
)
PENDING = re.compile(r"^\s*(sending|отправка)(…|\.\.\.)?\s*$", re.I)
# Status labels and times under messages, never someone's words.
SYSTEM = re.compile(
    r"^\s*(seen|sent|delivered|read|active now|active \d+\s?\w* ago|typing(…|\.\.\.)?"
    r"|seen (just now|by .+|\d+\s?\w* ago)|просмотрено|отправлено|доставлено|прочитано"
    r"|в сети|печатает(…|\.\.\.)?|today|yesterday|сегодня|вчера"
    r"|\d{1,2}:\d{2}(\s?[ap]\.?m\.?)?)\s*$",
    re.I,
)
COMPOSER = '[role="textbox"][contenteditable="true"]'
SEARCH_INPUT = (
    'input[name="queryBox"], [role="dialog"] input[type="text"], [role="dialog"] input[placeholder]'
)
THREAD = re.compile(r"^/direct/t/([0-9A-Za-z_-]{1,80})/?")
PROFILE_PATH = re.compile(r"^/([a-z0-9._]{1,30})/?$")
NOT_PROFILES = {"direct", "explore", "reels", "reel", "accounts", "stories", "p", "tv", "about"}
DIRECT_NEW = "https://www.instagram.com/direct/new/"
DIRECT_INBOX = "https://www.instagram.com/direct/inbox/"

# The thread of a message box: its nearest ancestor that is a floating chat window (fixed
# or a dialog: Instagram opens one over the profile on some accounts) or a column at least
# 60% of the window tall (the /direct/ page). Messages count only above the box, below
# the thread header; their side is where they stand in the column.
PANE_JS = """
const paneOf = box => {
  const floating = node =>
    node.getAttribute('role') === 'dialog' || getComputedStyle(node).position === 'fixed';
  let pane = box;
  let via = 'page';
  while (pane.parentElement && pane !== document.body) {
    if (floating(pane)) { via = 'window'; break; }
    if (pane.getBoundingClientRect().height >= innerHeight * 0.6) { via = 'column'; break; }
    pane = pane.parentElement;
  }
  return [pane, via];
};
const messagesOf = (pane, box, header, center) => {
  const area = pane.getBoundingClientRect();
  const bottom = box.getBoundingClientRect().top;
  const middle = area.left + area.width / 2;
  const messages = [];
  for (const node of pane.querySelectorAll('[dir="auto"]')) {
    if (node.querySelector('[dir="auto"]') || node.closest('[role="textbox"]')) continue;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.top < area.top + header || rect.top >= bottom) continue;
    const text = (node.innerText || '').trim();
    if (!text) continue;
    const offset = (rect.left + rect.right) / 2 - middle;
    const side = Math.abs(offset) < area.width * center ? 'middle' : offset < 0 ? 'in' : 'out';
    let opacity = 1;
    for (let up = node, i = 0; up && up !== pane && i < 6; up = up.parentElement, i++)
      opacity = Math.min(opacity, parseFloat(getComputedStyle(up).opacity));
    messages.push({ text, side, faded: opacity < 0.7 });
  }
  return messages;
};
const headerOf = (pane, header) => {
  const area = pane.getBoundingClientRect();
  const links = [];
  const lines = [];
  for (const node of pane.querySelectorAll('a[href], [dir="auto"], h1, h2, span')) {
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.top >= area.top + header || rect.bottom <= area.top)
      continue;
    if (node.tagName === 'A') links.push(node.getAttribute('href') || '');
    for (const line of (node.innerText || '').split('\\n'))
      if (line.trim()) lines.push(line.trim());
  }
  return { links: [...new Set(links)].slice(0, 20), lines: [...new Set(lines)].slice(0, 40) };
};
const notesOf = pane => {
  const notes = [];
  for (const node of pane.querySelectorAll('*')) {
    if (node.closest('[role="textbox"]')) continue;
    const label = node.getAttribute('aria-label');
    if (label) notes.push(label);
    if (!node.children.length) {
      const text = (node.textContent || '').trim();
      if (text && text.length < 200) notes.push(text);
    }
  }
  return notes.slice(-300);
};
"""

# The open thread's messages; `index` picks the message box (the last one by default).
COLLECT = (
    "([header, center, index]) => {"
    + PANE_JS
    + """
  const boxes = [...document.querySelectorAll('[role="textbox"][contenteditable="true"]')];
  const box = index === undefined || index === null ? boxes[boxes.length - 1] : boxes[index];
  if (!box) return null;
  const [pane, via] = paneOf(box);
  const area = pane.getBoundingClientRect();
  return {
    messages: messagesOf(pane, box, header, center).map(({ text, side }) => ({ text, side })),
    via,
    pane: [Math.round(area.width), Math.round(area.height)],
    view: [innerWidth, innerHeight],
  };
}"""
)
# Every message box on the page with its thread's header: whose chat each one is.
PANES = (
    "([header]) => {"
    + PANE_JS
    + """
  return [...document.querySelectorAll('[role="textbox"][contenteditable="true"]')].map(
    (box, index) => {
      const [pane, via] = paneOf(box);
      const rect = box.getBoundingClientRect();
      return { index, via, visible: !!(rect.width && rect.height), ...headerOf(pane, header) };
    });
}"""
)
# One chosen message box (an element handle): its thread now.
STATE = (
    "(box, [header, center]) => {"
    + PANE_JS
    + """
  if (!box || !box.isConnected) return { lost: true };
  const [pane, via] = paneOf(box);
  return {
    lost: false,
    via,
    messages: messagesOf(pane, box, header, center),
    notes: notesOf(pane),
    draft: (box.innerText || '').trim(),
  };
}"""
)
# Recipient picker rows naming exactly this username; the innermost ones are marked.
PICK = """([username]) => {
  for (const node of document.querySelectorAll('[data-alf-pick]'))
    node.removeAttribute('data-alf-pick');
  const input = document.querySelector('input[name="queryBox"]') ||
    [...document.querySelectorAll('[role="dialog"] input')].pop();
  const scope = (input && input.closest('[role="dialog"]')) || document.body;
  const rows = [...scope.querySelectorAll(
    '[role="button"], [role="checkbox"], [role="option"], [role="listitem"], label, button'
  )];
  const lines = row => (row.innerText || '').split('\\n')
    .map(line => line.trim().replace(/^@/, '').toLowerCase()).filter(Boolean);
  const exact = rows.filter(row => lines(row).includes(username));
  const inner = exact.filter(row => !exact.some(other => other !== row && row.contains(other)));
  // A row that also names another username is someone whose display name is ours.
  const clean = inner.filter(row =>
    !lines(row).some(line => line !== username && /^[a-z0-9._]{1,30}$/.test(line)));
  (clean.length ? clean : inner)
    .forEach((row, index) => row.setAttribute('data-alf-pick', String(index)));
  return (clean.length ? clean : inner).length;
}"""
HEADER = 72  # px: the thread header with the name and buttons
CENTER = 0.06  # share of the column width around the middle: dates and notices

# Seconds. The whole send stays inside the shell's 45 s call limit: the routes that open
# the thread give way to typing and confirming (BUDGET).
BUDGET = 40.0
PROFILE_WAIT = 8.0
PROFILE_RETRY_WAIT = 5.0
DIALOG_WAIT = 10.0
PICKER_WAIT = 8.0
CONFIRM_WAIT = 8.0
SETTLE = 1.0
# An opened thread shows its earlier messages within this time; until then it is not
# taken for an empty one.
HISTORY_WAIT = 2.5
TYPE_SECONDS_PER_CHAR = 0.02  # 10 ms pause + the key event, measured on the stand-ins
GUARD_EVERY = 1.0
POLL_MS = 250


def fail(error: str, **extra) -> dict:
    return {"outcome": "error", "error": error, **extra}


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def human(messages: list[dict]) -> list[dict]:
    """Messages that are someone's words: no status labels, times or centred notices."""
    return [
        item
        for item in messages
        if item.get("side") in ("in", "out") and not SYSTEM.match(str(item.get("text") or ""))
    ]


def header_match(pane: dict, username: str) -> str:
    """'match' when the thread header names this username, 'other' when it links to
    someone else's profile, 'unknown' when it says neither."""
    others = False
    for href in pane.get("links") or []:
        path = urlparse(str(href)).path.lower()
        found = PROFILE_PATH.match(path)
        if not found or found.group(1) in NOT_PROFILES:
            continue
        if found.group(1) == username:
            return "match"
        others = True
    if others:
        return "other"
    for line in pane.get("lines") or []:
        if str(line).strip().lstrip("@").lower() == username:
            return "match"
    return "unknown"


@dataclass
class Opened:
    index: int
    route: str
    thread_id: str = ""


@dataclass
class Attempt:
    """What the routes saw, for the final verdict when none of them opened the thread."""

    follow_only: bool = False
    closed: bool = False
    not_found: bool = False
    wrong: bool = False
    errors: list[str] = field(default_factory=list)


class Stop(Exception):
    """A reason not to go on before anything is typed or sent: an error kind."""

    def __init__(self, kind: str, **extra):
        super().__init__(kind)
        self.kind = kind
        self.extra = extra


class DirectSender:
    def __init__(
        self,
        page,
        username: str,
        text: str,
        *,
        mode: str,
        previous: list[str],
        known_inbound: list[str],
        guard: Callable[[], str | None],
        rate_limited: Callable[[], bool],
        typer: MessageTyper,
        clock: Callable[[], float],
    ):
        self.page = page
        self.username = username
        self.text = text
        self.mode = mode
        self.previous = [normalized(item) for item in previous if normalized(item)]
        self.known_inbound = {normalized(item) for item in known_inbound if normalized(item)}
        self.guard = guard
        self.rate_limited = rate_limited
        self.typer = typer
        self.clock = clock
        self.started = clock()
        self.attempt = Attempt()

    # ---------- time ----------

    def reserve(self) -> float:
        """Seconds kept for typing and confirming."""
        return len(self.text) * TYPE_SECONDS_PER_CHAR + CONFIRM_WAIT + 3.0

    def open_deadline(self) -> float:
        """The alternative routes give way to typing and confirming; the profile's own
        button always gets its full wait."""
        return max(self.started + BUDGET - self.reserve(), self.started + PROFILE_WAIT + 2.0)

    def wait(self, ms: int = POLL_MS) -> None:
        self.page.wait_for_timeout(ms)

    # ---------- checks at the boundaries ----------

    def check(self, where: str) -> None:
        problem = account_problem(self.page, self.rate_limited)
        if problem:
            log.info("direct_account_problem", extra={"detail": f"{problem} at {where}"})
            raise Stop(problem)
        reason = self._guard()
        if reason:
            raise Stop("stopped", reason=reason)

    def _guard(self) -> str | None:
        try:
            return self.guard()
        except Exception:
            # The core could not answer: not knowing is a reason to stop before sending.
            return "guard_failed"

    # ---------- opening the thread ----------

    def open(self) -> Opened:
        path = urlparse(self.page.url).path
        thread = THREAD.match(path)
        routes: list[str] = []
        if thread:
            routes.append("thread")
        routes += ["profile", "profile_reload", "direct"]
        for route in routes:
            if self.clock() > self.open_deadline():
                break
            try:
                opened = getattr(self, f"_route_{route}")()
            except Stop:
                raise
            except Exception as error:  # a lost frame or context: the next route tries again
                self.attempt.errors.append(f"{route}:{type(error).__name__}")
                if self.page.is_closed():
                    raise Stop("dialog") from None
                opened = None
            if opened is not None:
                return opened
        attempt = self.attempt
        if attempt.closed:
            raise Stop("messages_closed")
        if attempt.wrong:
            raise Stop("wrong_recipient")
        if attempt.not_found:
            raise Stop("not_found")
        log.info(
            "direct_thread_not_opened",
            extra={"detail": f"follow_only={attempt.follow_only} errors={attempt.errors[:4]}"},
        )
        raise Stop("dialog")

    def _route_thread(self) -> Opened | None:
        """A follow-up opened straight in its thread by the shell."""
        thread = THREAD.match(urlparse(self.page.url).path)
        if not thread:
            return None
        return self._await_composer("thread", strict=False, single_ok=True)

    def _goto_profile(self) -> None:
        self.page.goto(
            f"https://www.instagram.com/{self.username}/",
            wait_until="domcontentloaded",
            timeout=12000,
        )

    def _on_profile(self) -> bool:
        return urlparse(self.page.url).path.rstrip("/").lower() == f"/{self.username}"

    def _route_profile(self) -> Opened | None:
        if not self._on_profile():
            self._goto_profile()
        return self._from_profile(PROFILE_WAIT, "profile")

    def _route_profile_reload(self) -> Opened | None:
        """Once more after a reload: the button may simply have been slow."""
        if self._on_profile():
            self.page.reload(wait_until="domcontentloaded", timeout=12000)
        else:
            self._goto_profile()
        return self._from_profile(PROFILE_RETRY_WAIT, "profile_reload")

    def _from_profile(self, wait: float, route: str) -> Opened | None:
        page = self.page
        main = page.locator("main")
        message = main.get_by_role("button", name=MESSAGE_BUTTON)
        follow = main.get_by_role("button", name=FOLLOW_BUTTON)
        deadline = min(self.clock() + wait, self.open_deadline())
        while True:
            if login_overlay(page):
                raise Stop("login")
            if message.count():
                break
            if page.get_by_text(UNAVAILABLE).count():
                self.attempt.not_found = True
                return None
            problem = account_problem(page, self.rate_limited)
            if problem:
                raise Stop(problem)
            if self.clock() > deadline:
                # Only «Подписаться» this long is a hint, not a verdict: Direct decides.
                if follow.count():
                    self.attempt.follow_only = True
                return None
            self.wait()
        before = page.locator(COMPOSER).count()
        message.first.click(timeout=5000)
        picked = self._picker_after_click()
        if picked is False:
            return None
        # A recipient picked in the picker must be named by the chat's header.
        return self._await_composer(route, strict=bool(picked), single_ok=not picked, before=before)

    def _picker_after_click(self) -> bool | None:
        """Some accounts get a recipient picker from «Отправить сообщение» instead of the
        chat. None: no picker (the chat opens); True: the exact username was picked;
        False: a picker without this username."""
        page = self.page
        search = page.locator(SEARCH_INPUT)
        stop = self.clock() + 2.0
        while self.clock() < stop:
            if page.locator(COMPOSER).count():
                return None
            if search.count():
                return self._pick(
                    search.first, min(self.clock() + PICKER_WAIT, self.open_deadline())
                )
            self.wait()
        return None

    def _route_direct(self) -> Opened | None:
        """A new message from Direct: the exact username is searched and picked."""
        page = self.page
        page.goto(DIRECT_NEW, wait_until="domcontentloaded", timeout=12000)
        search = page.locator(SEARCH_INPUT)
        deadline = min(self.clock() + PICKER_WAIT, self.open_deadline())
        opened_inbox = False
        while not search.count():
            self._dismiss()
            problem = account_problem(page, self.rate_limited)
            if problem:
                raise Stop(problem)
            if not opened_inbox and self.clock() > deadline - PICKER_WAIT / 2:
                opened_inbox = True
                page.goto(DIRECT_INBOX, wait_until="domcontentloaded", timeout=12000)
                button = page.get_by_role("button", name=NEW_MESSAGE_BUTTON)
                if button.count():
                    button.first.click(timeout=5000)
            if self.clock() > deadline:
                return None
            self.wait()
        if not self._pick(search.first, deadline):
            return None
        return self._await_composer("direct", strict=True, single_ok=False)

    def _pick(self, search, deadline: float) -> bool:
        """Types the username into the picker and picks the row naming exactly it."""
        page = self.page
        search.click(timeout=5000)
        search.fill("")
        page.keyboard.type(self.username, delay=20)
        while True:
            if page.evaluate(PICK, [self.username]):
                page.locator('[data-alf-pick="0"]').first.click(timeout=5000)
                break
            if self._closed_notice_anywhere():
                self.attempt.closed = True
                return False
            if self.clock() > deadline + 4.0:
                return False
            self.wait()
        chat = page.get_by_role("button", name=CHAT_BUTTON)
        stop = self.clock() + 3.0
        while not chat.count() and not page.locator(COMPOSER).count() and self.clock() < stop:
            self.wait()
        if chat.count():
            chat.last.click(timeout=5000)
        return True

    def _closed_notice_anywhere(self) -> bool:
        return bool(self.page.get_by_text(RECIPIENT_CLOSED).count())

    def _dismiss(self) -> None:
        dismiss = self.page.get_by_role("button", name=DISMISS_BUTTON)
        if dismiss.count():
            dismiss.first.click(timeout=3000)

    def _await_composer(
        self, route: str, *, strict: bool, single_ok: bool, before: int = 0
    ) -> Opened | None:
        """Waits for a message box and picks the one that is this recipient's chat."""
        page = self.page
        deadline = min(self.clock() + DIALOG_WAIT, self.open_deadline())
        while True:
            self._dismiss()
            problem = account_problem(page, self.rate_limited)
            if problem:
                raise Stop(problem)
            if self._closed_notice_anywhere():
                self.attempt.closed = True
                return None
            if page.locator(COMPOSER).count():
                index = self._choose(strict=strict, single_ok=single_ok, before=before)
                if index is not None:
                    thread = THREAD.match(urlparse(page.url).path)
                    return Opened(index, route, thread.group(1) if thread else "")
            if self.clock() > deadline:
                if self.attempt.follow_only and route == "direct":
                    # Only «Подписаться» on the profile and no message box for the exact
                    # username in Direct either: this account cannot write to them.
                    self.attempt.closed = True
                return None
            self.wait()

    def _choose(self, *, strict: bool, single_ok: bool, before: int) -> int | None:
        panes = [pane for pane in self.page.evaluate(PANES, [HEADER]) or [] if pane.get("visible")]
        verdicts = [(pane, header_match(pane, self.username)) for pane in panes]
        matched = [pane for pane, verdict in verdicts if verdict == "match"]
        if matched:
            return int(matched[-1]["index"])
        if any(verdict == "other" for _, verdict in verdicts) and len(panes) == 1:
            self.attempt.wrong = True
            return None
        if strict or not single_ok:
            return None
        # The one chat the profile's own button opened (no other chat window around).
        unknown = [pane for pane, verdict in verdicts if verdict == "unknown"]
        if len(panes) == 1 and len(unknown) == 1 and before == 0:
            return int(unknown[0]["index"])
        if len(panes) > 1:
            self.attempt.wrong = True
        return None

    # ---------- the thread before typing ----------

    def collect(self, index: int) -> list[dict]:
        found = self.page.evaluate(COLLECT, [HEADER, CENTER, index]) or {}
        return list(found.get("messages") or [])

    def check_history(self, index: int) -> None:
        if self.mode == "followup":
            self._check_followup(index)
            return
        deadline = self.clock() + HISTORY_WAIT
        while True:
            found = self.page.evaluate(COLLECT, [HEADER, CENTER, index]) or {}
            sides = [item.get("side") for item in found.get("messages") or []]
            if "in" in sides or "out" in sides:
                # Sizes only, no text: enough to tell a real thread from a misread layout.
                log.info(
                    "direct_existing_thread",
                    extra={
                        "detail": (
                            f"via={found.get('via')} pane={found.get('pane')} "
                            f"view={found.get('view')} in={sides.count('in')} "
                            f"out={sides.count('out')}"
                        )
                    },
                )
                raise Stop("existing_thread")
            if self.clock() > deadline:
                return
            self.wait()

    def _check_followup(self, index: int) -> None:
        """Our previous message must be in this thread, and no answer after it."""
        if not self.previous:
            raise Stop("thread_mismatch")
        deadline = self.clock() + HISTORY_WAIT
        while True:
            messages = human(self.collect(index))
            flats = [normalized(item["text"]) for item in messages]
            ours = set(self.previous)
            positions = [i for i, flat in enumerate(flats) if flat in ours]
            if self.previous[-1] in flats:
                first = positions[0]
                replies = [
                    item["text"]
                    for item, flat in zip(messages[first + 1 :], flats[first + 1 :], strict=True)
                    if item["side"] == "in" and flat not in ours and flat not in self.known_inbound
                ]
                if replies:
                    raise Stop("reply_detected", replies=replies[:5])
                return
            if self.clock() > deadline:
                raise Stop("thread_mismatch")
            self.wait()

    # ---------- after Enter ----------

    def state(self, handle) -> dict:
        return handle.evaluate(STATE, [HEADER, CENTER]) or {"lost": True}

    def baseline(self, state: dict) -> dict:
        mine = normalized(self.text)
        messages = human(state.get("messages") or [])
        outs = [normalized(m["text"]) for m in messages if m["side"] == "out"]
        notes = state.get("notes") or []
        return {
            "matching": outs.count(mine),
            "failed": sum(1 for note in notes if SEND_FAILED.search(note)),
            "closed": sum(1 for note in notes if RECIPIENT_CLOSED.search(note)),
        }

    def confirm(self, handle, base: dict, index: int) -> dict:
        page = self.page
        mine = normalized(self.text)
        started = self.clock()
        limit = max(min(CONFIRM_WAIT, self.started + BUDGET - started), 3.0)
        steady_since = None
        account = None
        evidence = "no_message"
        read_errors = reads = 0
        while True:
            page.wait_for_timeout(POLL_MS)
            account = account or account_problem(page, self.rate_limited)
            try:
                state = self.state(handle)
                if state.get("lost"):
                    handle, state = self._refind(handle)
                reads += 1
            except Exception:
                read_errors += 1
                state = None
            if state is None or state.get("lost"):
                evidence = "composer_lost" if state else "unreadable"
                steady_since = None
            else:
                notes = state.get("notes") or []
                if sum(1 for note in notes if RECIPIENT_CLOSED.search(note)) > base["closed"]:
                    return {**fail("messages_closed"), **({"account": account} if account else {})}
                if sum(1 for note in notes if SEND_FAILED.search(note)) > base["failed"]:
                    limited = account in {"rate_limited", "action_blocked"} or self.rate_limited()
                    return fail("rate_limited" if limited else "rejected")
                messages = human(state.get("messages") or [])
                outs = [m for m in messages if m["side"] == "out"]
                flats = [normalized(m["text"]) for m in outs]
                new = flats.count(mine) > base["matching"]
                last = max((i for i, flat in enumerate(flats) if flat == mine), default=-1)
                near_end = new and last >= len(flats) - 3
                pending = near_end and (
                    outs[last].get("faded")
                    or any(PENDING.match(note) for note in (state.get("notes") or [])[-20:])
                )
                cleared = mine not in normalized(state.get("draft") or "")
                if near_end and cleared and not pending:
                    steady_since = steady_since or self.clock()
                    if self.clock() - steady_since >= SETTLE:
                        thread = THREAD.match(urlparse(page.url).path)
                        result = {
                            "outcome": "sent",
                            "thread_id": thread.group(1) if thread else "",
                            "confirmed": "interface",
                        }
                        if account:
                            result["account"] = account
                        return result
                else:
                    steady_since = None
                    evidence = (
                        "pending"
                        if pending
                        else "not_cleared"
                        if near_end and not cleared
                        else "no_message"
                    )
            if self.clock() - started > limit:
                if reads == 0 and read_errors:
                    evidence = "unreadable"
                result = fail("unconfirmed", evidence=evidence)
                if account:
                    result["account"] = account
                return result

    def _refind(self, handle):
        """The box is gone (a closed window, a re-render): this recipient's chat again."""
        index = self._choose(strict=True, single_ok=False, before=0)
        if index is None:
            return handle, {"lost": True}
        handle = self.page.locator(COMPOSER).nth(index).element_handle(timeout=2000)
        return handle, self.state(handle)

    # ---------- the send ----------

    def run(self) -> dict:
        page = self.page
        try:
            self.check("start")
            opened = self.open()
            self.check_history(opened.index)
            self.check("typing")
            if self.clock() + len(self.text) * TYPE_SECONDS_PER_CHAR + CONFIRM_WAIT > (
                self.started + BUDGET + 2.0
            ):
                raise Stop("dialog")
            box = page.locator(COMPOSER).nth(opened.index)
            self.typer.type_message(page, box, self.text, cancelled=self._typing_stop())
            handle = box.element_handle(timeout=3000)
            base = self.baseline(self.state(handle))
            try:
                self.check("enter")
            except Stop:
                # Typed but not sent: the draft does not stay in the box.
                try:
                    clear_input(page, box)
                except Exception:
                    pass
                raise
        except Stop as stop:
            return fail(stop.kind, **stop.extra)
        except MessageTypingCancelled:
            return fail("stopped", reason=self._guard() or "stopped")
        except MessageTypingMismatch as error:
            return {
                **fail("typing_mismatch"),
                "expected_length": error.expected_length,
                "actual_length": error.actual_length,
            }
        except Exception:
            # Nothing has been sent yet: the worker may try again later.
            return fail("dialog")

        # Send. From here on a doubt is "unconfirmed" and never resent.
        try:
            box.press("Enter", timeout=3000)
        except Exception:
            return fail("unconfirmed", evidence="enter_failed")
        try:
            return self.confirm(handle, base, opened.index)
        except Exception:
            return fail("unconfirmed", evidence="closed")

    def _typing_stop(self) -> Callable[[], bool]:
        last = [self.clock()]

        def stop() -> bool:
            if self.clock() - last[0] < GUARD_EVERY:
                return False
            last[0] = self.clock()
            return self._guard() is not None

        return stop


def has_history(
    page, clock: Callable[[], float] = time.monotonic, index: int | None = None
) -> bool:
    """Messages in the open thread: ours or the other person's, on either side."""
    deadline = clock() + HISTORY_WAIT
    while True:
        found = page.evaluate(COLLECT, [HEADER, CENTER, index]) or {}
        sides = [item.get("side") for item in found.get("messages") or []]
        if "in" in sides or "out" in sides:
            return True
        if clock() > deadline:
            return False
        page.wait_for_timeout(POLL_MS)


def send_direct(
    page,
    username: str,
    text: str,
    *,
    mode: str = "initial",
    previous: list[str] | None = None,
    known_inbound: list[str] | None = None,
    guard: Callable[[], str | None] = lambda: None,
    rate_limited: Callable[[], bool] = lambda: False,
    typer: MessageTyper | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    parsed = urlparse(page.url)
    if (parsed.hostname or "").lower() not in {"instagram.com", "www.instagram.com"}:
        return fail("no_instagram_tab")
    if not USERNAME.match(username) or not text.strip() or len(text) > MAX_TEXT:
        return fail("bad_request")
    if mode not in ("initial", "followup"):
        return fail("bad_request")
    on_profile = parsed.path.rstrip("/").lower() == f"/{username}"
    in_thread = mode == "followup" and bool(THREAD.match(parsed.path))
    if not on_profile and not in_thread and url_problem(page.url) is None:
        return fail("no_instagram_tab")
    return DirectSender(
        page,
        username,
        text,
        mode=mode,
        previous=previous or [],
        known_inbound=known_inbound or [],
        guard=guard,
        rate_limited=rate_limited,
        typer=typer or MessageTyper(),
        clock=clock,
    ).run()
