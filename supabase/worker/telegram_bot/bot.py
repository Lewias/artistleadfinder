"""Telegram bot of the assistant: `python -m telegram_bot`.

A user links a chat with a one-time code from the app (`/start <code>`). After that every
message of the chat is a question to the assistant. The assistant lives in the app on the
user's computer, so the bot only stores the question (public.tg_requests); the running app
takes it, asks the assistant and writes the answer back, and the bot sends it to the chat.
While the app is closed the question waits and the bot says so.

The bot asks Telegram for updates itself (long polling): no port is opened on the server.
"""

import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
# Short, so an answer the app has written reaches the chat within a few seconds.
POLL_SECONDS = 3
CODE = re.compile(r"^[0-9A-F]{10}$")
MAX_CODE_TRIES = 5
MAX_QUESTION = 4000
# Telegram's limit for one message is 4096 characters.
MESSAGE_CHARS = 3900
# Questions of one account that may wait at once.
MAX_OPEN = 5
# The app asks for questions every few seconds; silent for longer, it is closed.
APP_ALIVE = "45 seconds"
# A question the app took and never answered (it was closed, the connection was lost).
ANSWER_WAIT = "15 minutes"
QUESTION_WAIT = "24 hours"
# Buttons of the menu the bot had as the cloud parser's remote: a chat that still shows
# it gets the help and loses the menu instead of asking the assistant «Статус».
OLD_BUTTONS = ("🔎", "✉️ Написать лидам", "📊 Статус", "⏹ Остановить", "📋 Последние лиды")

COMMANDS = [
    {"command": "new", "description": "Начать новый чат с ассистентом"},
    {"command": "unlink", "description": "Отвязать Telegram от аккаунта"},
    {"command": "help", "description": "Что умеет бот"},
]
HELP = (
    "Я передаю ваши сообщения ассистенту Artist Lead Finder и приношу его ответы.\n\n"
    "Просто напишите вопрос — например «Кто ждёт ответа в iMessage?» или «Кому стоит "
    "написать сегодня?». Ассистент работает в приложении на вашем компьютере, поэтому "
    "отвечает, пока приложение запущено; иначе вопрос подождёт (до суток).\n\n"
    "Сообщения артистам ассистент только готовит: отправить их можно кнопкой в приложении.\n\n"
    "/new — начать новый чат (ассистент забудет прошлый разговор)\n"
    "/unlink — отвязать этот чат"
)
NOT_LINKED = (
    "Этот чат не привязан к аккаунту.\n\nОткройте приложение → «Настройки» → «Ассистент» → "
    "«Подключить Telegram» и нажмите ссылку или отправьте сюда /start КОД."
)
APP_CLOSED = (
    "Приложение сейчас не запущено. Вопрос сохранён: ассистент ответит, когда вы откроете "
    "приложение (вопрос ждёт сутки)."
)
NO_ANSWER = (
    "Ассистент не ответил: приложение закрылось или потеряло связь. Отправьте вопрос ещё раз."
)


class TelegramError(Exception):
    def __init__(self, description: str, code: int | None = None):
        super().__init__(description)
        self.code = code


class Telegram:
    """Bot API over HTTPS. The token is part of the address and is never logged."""

    def __init__(self, token: str):
        self.base = f"{API}/bot{token}/"
        try:
            import certifi

            self.context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self.context = ssl.create_default_context()

    def call(self, method: str, **params):
        request = urllib.request.Request(
            self.base + method,
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"},
        )
        timeout = int(params.get("timeout") or 0) + 15
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=self.context) as answer:
                body = json.load(answer)
        except urllib.error.HTTPError as error:
            try:
                body = json.load(error)
            except Exception:
                raise TelegramError(f"HTTP {error.code}", error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TelegramError(type(error).__name__) from None
        if not body.get("ok"):
            raise TelegramError(str(body.get("description") or "error"), body.get("error_code"))
        return body.get("result")


def pieces(text: str, size: int = MESSAGE_CHARS) -> list[str]:
    """A long answer as several messages, cut at a paragraph or a line where it can be."""
    text = text.strip()
    parts = []
    while len(text) > size:
        cut = text.rfind("\n\n", 0, size)
        if cut < size // 2:
            cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut < size // 2:
            cut = size
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


class Bot:
    def __init__(
        self,
        connect: Callable,
        telegram,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.connect = connect
        self.telegram = telegram
        self.clock = clock
        self.conn = None
        self.offset: int | None = None
        self.username = ""
        self.code_tries: dict[int, list[float]] = {}

    # ---------- database ----------

    def db(self):
        if self.conn is None or self.conn.closed:
            self.conn = self.connect()
        return self.conn

    def rows(self, sql: str, params=()) -> list[dict]:
        cur = self.db().execute(sql, params)
        return cur.fetchall() if cur.description else []

    def as_user(self, owner: str, sql: str, params=()) -> list[dict]:
        """A call with the user's rights, the way PostgREST makes it for the app."""
        conn = self.db()
        with conn.transaction():
            conn.execute("set local role authenticated")
            conn.execute(
                "select set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": owner, "role": "authenticated"}),),
            )
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def owner_of(self, chat_id: int) -> str | None:
        found = self.rows("select owner_id from public.tg_links where chat_id = %s", (chat_id,))
        if not found:
            return None
        owner = str(found[0]["owner_id"])
        active = self.as_user(owner, "select public.is_active() as ok")
        return owner if active and active[0]["ok"] else None

    # ---------- Telegram ----------

    def send(self, chat_id: int, text: str, markup: dict | None = None) -> bool:
        """Plain text (an answer may hold any characters); False when Telegram refused it."""
        for part in pieces(text) or [""]:
            params = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
            if markup is not None:
                params["reply_markup"] = markup
            try:
                self.telegram.call("sendMessage", **params)
            except TelegramError as error:
                # 403: the user blocked the bot; nothing more to do for this message.
                log.warning("tg_send_failed %s", error.code)
                return False
        return True

    def typing(self, chat_id: int) -> None:
        try:
            self.telegram.call("sendChatAction", chat_id=chat_id, action="typing")
        except TelegramError:
            pass

    def start(self) -> None:
        me = self.telegram.call("getMe")
        self.username = me.get("username") or ""
        self.rows(
            "insert into public.tg_bot (id, username, updated_at) values (true, %s, now()) "
            "on conflict (id) do update set username = excluded.username, updated_at = now()",
            (self.username,),
        )
        try:
            self.telegram.call("setMyCommands", commands=COMMANDS)
        except TelegramError:
            log.warning("tg_commands_failed")
        log.info("telegram_bot_started")

    def poll(self) -> None:
        params = {"timeout": POLL_SECONDS, "allowed_updates": ["message"]}
        if self.offset is not None:
            params["offset"] = self.offset
        for update in self.telegram.call("getUpdates", **params) or []:
            self.offset = int(update["update_id"]) + 1
            try:
                self.handle(update)
            except psycopg.Error:
                log.exception("tg_update_db_error")
                self.conn = None
            except Exception:
                log.exception("tg_update_error")

    def run_forever(self) -> None:
        while True:
            try:
                if not self.username:
                    self.start()
                self.poll()
                self.tick()
            except TelegramError as error:
                log.warning("tg_api_error %s", error.code)
                time.sleep(5)
            except psycopg.Error:
                log.exception("tg_db_error")
                self.conn = None
                time.sleep(5)
            except Exception:
                log.exception("tg_error")
                time.sleep(5)

    # ---------- updates ----------

    def handle(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return
        if not isinstance(message.get("text"), str):
            if self.owner_of(chat["id"]):
                self.send(chat["id"], "Я понимаю только текст. Напишите вопрос словами.")
            return
        self.command(chat["id"], message["text"].strip(), message.get("from") or {})

    def command(self, chat_id: int, text: str, sender: dict) -> None:
        word, _, rest = text.partition(" ")
        word = word.split("@", 1)[0].lower()
        if word == "/start":
            if rest.strip():
                self.link(chat_id, rest.strip(), sender)
            elif self.owner_of(chat_id):
                self.send(chat_id, "Чат привязан. " + HELP, {"remove_keyboard": True})
            else:
                self.send(chat_id, NOT_LINKED)
            return
        owner = self.owner_of(chat_id)
        if word == "/help":
            self.send(chat_id, HELP if owner else NOT_LINKED)
            return
        if owner is None:
            self.send(chat_id, NOT_LINKED)
            return
        if word == "/unlink":
            self.rows("delete from public.tg_links where owner_id = %s", (owner,))
            self.rows(
                "update public.tg_requests set status = 'expired' "
                "where owner_id = %s and status in ('new', 'taken')",
                (owner,),
            )
            self.send(chat_id, "Чат отвязан. Привязать снова можно из приложения.")
            return
        if text.startswith(OLD_BUTTONS):
            self.send(chat_id, HELP, {"remove_keyboard": True})
            return
        if word.startswith("/") and word != "/new":
            self.send(chat_id, HELP)
            return
        # «/new» travels like a question: the app starts a new chat and says so.
        self.ask(owner, chat_id, "/new" if word == "/new" else text)

    def link(self, chat_id: int, code: str, sender: dict) -> None:
        now = self.clock()
        tries = [moment for moment in self.code_tries.get(chat_id, []) if now - moment < 3600]
        if len(tries) >= MAX_CODE_TRIES:
            self.send(chat_id, "Слишком много попыток. Попробуйте через час.")
            return
        code = code.upper()
        found = []
        if CODE.match(code):
            found = self.rows(
                "delete from public.tg_link_codes where code = %s "
                "returning owner_id, expires_at > now() as fresh",
                (code,),
            )
        if not found or not found[0]["fresh"]:
            self.code_tries[chat_id] = [*tries, now]
            self.send(
                chat_id,
                "Код не подошёл или устарел. Получите новый в приложении: «Настройки» → "
                "«Ассистент» → «Подключить Telegram».",
            )
            return
        owner = str(found[0]["owner_id"])
        active = self.as_user(owner, "select public.is_active() as ok")
        if not active or not active[0]["ok"]:
            self.send(chat_id, "Аккаунт заблокирован или не активирован.")
            return
        name = str(sender.get("username") or "")[:64]
        with self.db().transaction():
            self.rows(
                "delete from public.tg_links where chat_id = %s and owner_id <> %s",
                (chat_id, owner),
            )
            self.rows(
                "insert into public.tg_links (owner_id, chat_id, tg_username) values (%s, %s, %s) "
                "on conflict (owner_id) do update set chat_id = excluded.chat_id, "
                "tg_username = excluded.tg_username, linked_at = now()",
                (owner, chat_id, name),
            )
        self.code_tries.pop(chat_id, None)
        # The old bot's button menu goes away with the first message.
        self.send(chat_id, "✅ Telegram привязан к аккаунту.\n\n" + HELP, {"remove_keyboard": True})

    # ---------- questions and answers ----------

    def ask(self, owner: str, chat_id: int, text: str) -> None:
        if len(text) > MAX_QUESTION:
            self.send(chat_id, f"Слишком длинно: не больше {MAX_QUESTION} символов.")
            return
        waiting = self.rows(
            "select count(*) as n from public.tg_requests "
            "where owner_id = %s and status in ('new', 'taken')",
            (owner,),
        )
        if waiting[0]["n"] >= MAX_OPEN:
            self.send(chat_id, "Ассистент ещё не ответил на прошлые вопросы. Подождите ответа.")
            return
        self.rows(
            "insert into public.tg_requests (owner_id, chat_id, text) values (%s, %s, %s)",
            (owner, chat_id, text),
        )
        alive = self.rows(
            "select app_seen_at > now() - %s::interval as ok from public.tg_links "
            "where owner_id = %s",
            (APP_ALIVE, owner),
        )
        if alive and alive[0]["ok"]:
            self.typing(chat_id)
        else:
            self.send(chat_id, APP_CLOSED)

    def tick(self) -> None:
        self.deliver()
        self.expire()
        for row in self.rows(
            "select distinct chat_id from public.tg_requests where status = 'taken'"
        ):
            self.typing(row["chat_id"])

    def deliver(self) -> None:
        """Answers the app has written go to their chats, each once."""
        answers = self.rows(
            "select id, chat_id, answer from public.tg_requests where status = 'answered' "
            "order by id limit 20"
        )
        for row in answers:
            # Marked first: a message Telegram refuses is not tried again and again.
            self.rows(
                "update public.tg_requests set status = 'sent', sent_at = now() where id = %s",
                (row["id"],),
            )
            self.send(row["chat_id"], row["answer"] or "Ассистент ничего не ответил.")

    def expire(self) -> None:
        """A question the app took and dropped is reported; one nobody took in a day is not."""
        dropped = self.rows(
            "update public.tg_requests set status = 'expired' "
            "where status = 'taken' and taken_at < now() - %s::interval returning chat_id",
            (ANSWER_WAIT,),
        )
        for chat_id in {row["chat_id"] for row in dropped}:
            self.send(chat_id, NO_ANSWER)
        self.rows(
            "update public.tg_requests set status = 'expired' "
            "where status = 'new' and created_at < now() - %s::interval",
            (QUESTION_WAIT,),
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.warning("telegram_bot_disabled: no TELEGRAM_BOT_TOKEN")
        while True:
            time.sleep(3600)
    bot = Bot(lambda: psycopg.connect("", autocommit=True, row_factory=dict_row), Telegram(token))
    bot.run_forever()


if __name__ == "__main__":
    main()
