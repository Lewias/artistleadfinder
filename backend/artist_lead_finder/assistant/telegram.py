"""The assistant in Telegram.

The bot on the account server stores what the linked chat wrote. While the app runs, the
bridge here takes those questions with the user's own session, asks the assistant (the
same chat the «Ассистент» section shows) and writes the answer back; the bot sends it to
the chat. Nothing is sent to artists from Telegram: drafts and proposed actions still wait
for the user's button in the app, the answer only says they are there.

`TelegramService` is what the interface calls: the link state, a one-time code, unlink.
"""

import logging
import re
import threading
import time
from collections.abc import Callable

from ..account.client import OfflineError
from ..errors import UserError

log = logging.getLogger(__name__)

POLL_SECONDS = 4
# How often an account without a linked chat is checked for one.
LINK_CHECK_SECONDS = 60
# After «Подключить Telegram» the link is looked for at every round, for the code's life.
CODE_SECONDS = 15 * 60
ANSWER_TIMEOUT = 10 * 60
NEW_CHAT = "/new"


class TelegramService:
    def __init__(self, account, clock: Callable[[], float] = time.monotonic):
        self.account = account
        self.clock = clock
        # Until when the bridge should look for a fresh link at every round.
        self.expect_link_until = 0.0

    def call(self, method: str, params: dict):
        actions = {
            "telegram.state": self.state,
            "telegram.link": self.link,
            "telegram.unlink": self.unlink,
        }
        if method not in actions:
            raise UserError("Неизвестное действие.")
        if not self.account.configured:
            raise UserError("Telegram-бот работает через сервер аккаунтов.")
        return actions[method](params)

    @property
    def client(self):
        return self.account.client

    def state(self, params: dict | None = None) -> dict:
        return self.client.rpc("tg_status", {}, self.account.token()) or {"linked": False}

    def link(self, params: dict | None = None) -> dict:
        """A one-time code for `/start <code>`; with the bot's name, a link that sends it."""
        started = self.client.rpc("tg_link_start", {}, self.account.token()) or {}
        bot = started.get("bot") or ""
        if not bot:
            raise UserError("Telegram-бот на сервере ещё не запущен.")
        self.expect_link_until = self.clock() + CODE_SECONDS
        return {**started, "url": f"https://t.me/{bot}?start={started['code']}"}

    def unlink(self, params: dict | None = None) -> dict:
        self.client.rpc("tg_unlink", {}, self.account.token())
        return self.state()


def plain(text: str) -> str:
    """The assistant's Markdown as text a Telegram message shows without markup."""
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    return re.sub(r"(?m)^(\s*)[*-]\s+", r"\1• ", text).strip()


class TelegramBridge:
    def __init__(
        self,
        account,
        assistant,
        service: TelegramService,
        interval: float = POLL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.account = account
        self.assistant = assistant
        self.service = service
        self.interval = interval
        self.clock = clock
        self.linked = False
        self.link_checked: float | None = None
        # Answers the server did not take (no connection): sent again at the next round.
        self.unsent: list[tuple[int, str]] = []
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None

    # ---------- lifecycle ----------

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="telegram-bridge", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()

    def _loop(self) -> None:
        while not self.stopping.wait(self.interval):
            try:
                self.run_once()
            except Exception as error:  # noqa: BLE001 - the next round tries again
                log.warning("telegram_bridge_failed", extra={"error_type": type(error).__name__})

    # ---------- one round ----------

    def _is_linked(self, token: str) -> bool:
        now = self.clock()
        fresh = self.link_checked is not None and now - self.link_checked < LINK_CHECK_SECONDS
        if fresh and (self.linked or now >= self.service.expect_link_until):
            return self.linked
        status = self.account.client.rpc("tg_status", {}, token) or {}
        self.linked, self.link_checked = bool(status.get("linked")), now
        return self.linked

    def run_once(self) -> int:
        """Takes the waiting questions and answers them; how many were answered."""
        account = self.account
        # The assistant is for admins and moderators, as in the interface.
        if not account.ready or account.doubtful or not account.sees_all_crm:
            return 0
        try:
            token = account.token()
            self._flush(token)
            if not self._is_linked(token):
                return 0
            rows = account.client.rpc("tg_requests_take", {}, token) or []
        except OfflineError:
            account.note_offline()
            return 0
        except UserError:
            # Unlinked or refused meanwhile: look at the link again before the next take.
            self.link_checked = None
            return 0
        for row in sorted(rows, key=lambda item: item["request_id"]):
            if self.stopping.is_set():
                break
            self.unsent.append((int(row["request_id"]), self.answer(str(row["question"]))))
            try:
                self._flush(account.token())
            except OfflineError:
                account.note_offline()
            except UserError:
                pass
        return len(rows)

    def _flush(self, token: str) -> None:
        while self.unsent:
            request_id, text = self.unsent[0]
            self.account.client.rpc(
                "tg_request_answer", {"p_id": request_id, "p_answer": text}, token
            )
            self.unsent.pop(0)

    # ---------- the assistant ----------

    def answer(self, question: str) -> str:
        assistant = self.assistant
        # A question asked in the app's own chat finishes first.
        running = assistant.thread
        if running is not None and running.is_alive():
            running.join(ANSWER_TIMEOUT)
        try:
            if question == NEW_CHAT:
                assistant.new_chat({})
                return "Начат новый чат: прошлый разговор ассистент больше не помнит."
            pending = {item["id"] for item in assistant.state({})["actions"]}
            assistant.ask({"text": question})
        except UserError as error:
            return str(error)
        thread = assistant.thread
        thread.join(ANSWER_TIMEOUT)
        if thread.is_alive():
            assistant.stopping.set()
            thread.join(30)
            return "Ассистент не успел ответить за 10 минут и остановлен. Разбейте задачу на части."
        return self._compose(assistant.state({}), pending)

    @staticmethod
    def _compose(state: dict, known_actions: set[int]) -> str:
        """What the assistant said since the last question, as one Telegram message."""
        items = state["items"]
        last = max((i for i, item in enumerate(items) if item["kind"] == "user"), default=-1)
        turn = items[last + 1 :]
        parts = [plain(item["text"]) for item in turn if item["kind"] == "assistant"]
        drafts = {draft["id"]: draft for draft in state["chat_drafts"]}
        for item in turn:
            draft = drafts.get(item.get("draft_id")) if item["kind"] == "draft" else None
            if draft is not None and draft["status"] == "pending":
                who = draft["contact_name"] or draft["handle"]
                parts.append(f"✉️ Черновик для {who} ({draft['handle']}):\n{draft['text']}")
        if any(item["kind"] == "draft" for item in turn):
            parts.append("Черновики не отправлены: проверьте и отправьте их кнопкой в приложении.")
        proposed = [
            action["summary"]
            for action in state["actions"]
            if action["status"] == "pending" and action["id"] not in known_actions
        ]
        if proposed:
            parts.append(
                "Предложенные действия ждут кнопки «Выполнить» в приложении:\n"
                + "\n".join(f"• {summary}" for summary in proposed)
            )
        if state["error"]:
            parts.append(f"⚠️ {state['error']}")
        return "\n\n".join(part for part in parts if part) or "Ассистент ничего не ответил."
