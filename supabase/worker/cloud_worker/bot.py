"""Telegram bot of the cloud parser: `python -m cloud_worker.bot`.

A user links a chat with a one-time code from the app (`/start <code>`), then repeats the
last cloud search with a new goal, watches it, stops it and reads the leads it saved, and
hears from the bot when a job ends. Jobs are started and cancelled through cloud_start and
cloud_cancel with the user's own rights, so the bot passes the same checks as the app.
Admins' linked chats hear about failed jobs and a parser that stopped working.

The bot asks Telegram for updates itself (long polling): no port is opened on the server.
"""

import html
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
POLL_SECONDS = 10
TICK_SECONDS = 10
# Finished jobs older than this are not announced (a bot that was down, a first start).
ANNOUNCE_HOURS = 6
STALLED = "3 minutes"
WAITING = "20 minutes"
CODE = re.compile(r"^[0-9A-F]{10}$")
# Buttons of «Найти артистов» (acc, cnt, own) and «Написать лидам» (wac, wcn, wow): the
# account (and a number given with the command), the number, a number typed by hand.
PICK = re.compile(r"^(acc|cnt|own|wac|wcn|wow):([0-9a-f]{32})(?::(\d{1,4}))?$")
MAX_CODE_TRIES = 5
LEADS_SHOWN = 10
DEFAULT_TARGET = 50
MAX_TARGET = 500
# How long a status message follows a search by itself; after that, «Обновить».
LIVE_SECONDS = 6 * 3600

BUTTON_FIND = "🔎 Найти артистов"
BUTTON_WRITE = "✉️ Написать лидам"
BUTTON_STATUS = "📊 Статус"
BUTTON_STOP = "⏹ Остановить"
BUTTON_LEADS = "📋 Последние лиды"
MENU = {
    "keyboard": [
        [{"text": BUTTON_FIND}, {"text": BUTTON_WRITE}],
        [{"text": BUTTON_STATUS}, {"text": BUTTON_LEADS}],
        [{"text": BUTTON_STOP}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
LIVE_BUTTONS = {
    "inline_keyboard": [
        [
            {"text": "🔄 Обновить", "callback_data": "status"},
            {"text": BUTTON_STOP, "callback_data": "stop"},
        ]
    ]
}
COMMANDS = [
    {"command": "find", "description": "Найти артистов: /find 50"},
    {"command": "write", "description": "Написать найденным лидам: /write 20"},
    {"command": "status", "description": "Что делает парсер"},
    {"command": "stop", "description": "Остановить поиск"},
    {"command": "leads", "description": "Последние найденные лиды"},
    {"command": "notify", "description": "Включить или выключить уведомления"},
    {"command": "unlink", "description": "Отвязать Telegram от аккаунта"},
    {"command": "help", "description": "Что умеет бот"},
]
CATEGORY = {
    "ARTIST": "артист",
    "PRODUCER": "продюсер",
    "MEDIA": "медиа",
    "OTHER": "другое",
    "UNKNOWN": "не определено",
}
STEP = {
    "source": "сетка источника",
    "post": "публикация",
    "tagged_grid": "отметки источника",
    "tagged_post": "публикация с отметками",
    "followers": "подписчики",
    "following": "подписки",
    "profile": "профиль",
}

HELP = (
    "Я запускаю облачный парсер Artist Lead Finder, пока ваш компьютер выключен.\n\n"
    f"{BUTTON_FIND} — выбрать аккаунт Instagram и сколько лидов найти. Источники и "
    "настройки парсера — из последнего облачного поиска в приложении. Можно сразу числом: "
    "<code>/find 100</code>.\n"
    f"{BUTTON_WRITE} — разослать сообщения новым лидам облачного парсера, которым ещё не "
    "писали. Пишет аккаунт с прокси, сообщения и паузы — из последней облачной рассылки в "
    "приложении.\n"
    f"{BUTTON_STATUS} — что сейчас делают парсер и рассылка.\n"
    f"{BUTTON_STOP} — остановить поиск и рассылку.\n"
    f"{BUTTON_LEADS} — кто попал в CRM.\n"
    "/notify — уведомления о конце поиска вкл/выкл.\n"
    "/unlink — отвязать этот чат.\n\n"
    "Источники, сообщения и настройки меняются в приложении, в разделе «Облачный парсер»."
)
NOT_LINKED = (
    "Этот чат не привязан к аккаунту.\n\nОткройте приложение → «Облачный парсер» → "
    "«Подключить Telegram» и нажмите ссылку или отправьте сюда <code>/start КОД</code>."
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


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def number(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def database_message(error: Exception) -> str:
    """The text a server check raised for the user (all of them are written for people)."""
    diag = getattr(error, "diag", None)
    text = getattr(diag, "message_primary", None) if diag is not None else None
    return text or "Сервер не принял запрос. Попробуйте позже."


def target_from(text: str) -> int | None:
    match = re.search(r"\d+", text or "")
    if not match:
        return None
    return max(1, min(MAX_TARGET, int(match.group())))


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
        # Chats asked to type a number: chat -> ("find" or "write", account).
        self.awaiting: dict[int, tuple[str, str]] = {}
        # Problems already reported to admins (job id + kind), so each is reported once.
        self.reported: set[tuple[str, str]] = set()
        # One status message per chat, rewritten while a search goes on (chat -> card).
        self.cards: dict[int, dict] = {}
        # The newest message id seen in each chat, to know whether the card is still last.
        self.latest: dict[int, int] = {}
        self.removed: int | None = None

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

    def send(self, chat_id: int, text: str, markup: dict | None = None) -> int | None:
        """The message's id, or None when Telegram refused it."""
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if markup is not None:
            params["reply_markup"] = markup
        try:
            sent = self.telegram.call("sendMessage", **params)
        except TelegramError as error:
            # 403: the user blocked the bot; nothing more to do for this message.
            log.warning("tg_send_failed %s", error.code)
            return None
        message_id = (sent or {}).get("message_id") if isinstance(sent, dict) else None
        if message_id:
            self.latest[chat_id] = max(self.latest.get(chat_id, 0), int(message_id))
        return message_id

    def edit(self, chat_id: int, message_id: int, text: str, markup: dict | None) -> bool:
        """The message rewritten in place; False when it is gone (deleted, too old)."""
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": markup or {"inline_keyboard": []},
        }
        try:
            self.telegram.call("editMessageText", **params)
            return True
        except TelegramError as error:
            if "not modified" in str(error):
                return True
            log.warning("tg_edit_failed %s", error.code)
            return False

    def delete(self, chat_id: int, message_id: int | None) -> bool:
        if not message_id:
            return False
        try:
            self.telegram.call("deleteMessage", chat_id=chat_id, message_id=message_id)
            return True
        except TelegramError:
            return False  # older than 48 hours or already gone: it simply stays

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
        params = {"timeout": POLL_SECONDS, "allowed_updates": ["message", "callback_query"]}
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
        if "callback_query" in update:
            query = update["callback_query"]
            try:
                self.telegram.call("answerCallbackQuery", callback_query_id=query["id"])
            except TelegramError:
                pass
            message = query.get("message") or {}
            chat = message.get("chat") or {}
            if chat.get("type") != "private":
                return
            self.command(chat["id"], query.get("data") or "", query.get("from") or {}, update)
            return
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private" or not isinstance(message.get("text"), str):
            return
        self.removed = None
        self.command(chat["id"], message["text"].strip(), message.get("from") or {}, update)
        # A tap the bot removed (the «Статус» button) does not push the status card up.
        mid = message.get("message_id")
        if mid and mid != self.removed:
            self.latest[chat["id"]] = max(self.latest.get(chat["id"], 0), mid)

    def command(self, chat_id: int, text: str, sender: dict, update: dict) -> None:
        word, _, rest = text.partition(" ")
        word = word.split("@", 1)[0].lower()
        if word == "/start":
            if rest.strip():
                self.link(chat_id, rest.strip(), sender)
            elif self.owner_of(chat_id):
                self.send(chat_id, "Чат привязан. " + HELP, MENU)
            else:
                self.send(chat_id, NOT_LINKED)
            return
        if word in ("/help", "help"):
            self.send(chat_id, HELP, MENU if self.owner_of(chat_id) else None)
            return
        owner = self.owner_of(chat_id)
        if owner is None:
            self.send(chat_id, NOT_LINKED)
            return
        # One job per Telegram update, however many times Telegram delivers it.
        request = f"telegram-{update.get('update_id')}"
        choice = PICK.match(text)
        if choice:
            kind, profile, count = choice.group(1), choice.group(2), int(choice.group(3) or 0)
            if not any(item["profile_id"] == profile for item in self.accounts(owner)):
                self.send(chat_id, "Этого аккаунта больше нет на сервере.")
            elif kind == "acc":
                self.choose_account(owner, chat_id, profile, count or None, request)
            elif kind == "cnt":
                self.launch(owner, chat_id, profile, count, request)
            elif kind == "own":
                self.ask_number(chat_id, "find", profile)
            elif kind == "wac":
                self.choose_writer(owner, chat_id, profile, count or None, request)
            elif kind == "wcn":
                self.launch_write(owner, chat_id, profile, count, request)
            else:
                self.ask_number(chat_id, "write", profile)
            return
        if chat_id in self.awaiting and re.fullmatch(r"\d{1,4}", text):
            action, profile = self.awaiting.pop(chat_id)
            start = self.launch if action == "find" else self.launch_write
            start(owner, chat_id, profile, int(text), request)
            return
        self.awaiting.pop(chat_id, None)
        if word in ("/find", "find") or text.startswith(BUTTON_FIND):
            self.find(owner, chat_id, target_from(rest), request)
        elif word in ("/write", "write") or text.startswith(BUTTON_WRITE):
            self.write(owner, chat_id, target_from(rest), request)
        elif word in ("/status", "status") or text == BUTTON_STATUS:
            if "callback_query" in update:
                # A button under a message: that message becomes the status.
                pressed = (update["callback_query"].get("message") or {}).get("message_id")
                self.status(owner, chat_id, pressed=pressed)
            else:
                self.status(owner, chat_id, tap=(update.get("message") or {}).get("message_id"))
        elif word in ("/stop", "stop") or text == BUTTON_STOP:
            self.stop(owner, chat_id, quiet="callback_query" in update)
        elif word in ("/leads", "leads") or text == BUTTON_LEADS:
            self.leads(owner, chat_id)
        elif word == "/notify":
            self.toggle_notify(owner, chat_id)
        elif word == "/unlink":
            self.rows("delete from public.tg_links where owner_id = %s", (owner,))
            self.send(
                chat_id,
                "Чат отвязан. Привязать снова можно из приложения.",
                {"remove_keyboard": True},
            )
        else:
            self.send(chat_id, HELP, MENU)

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
                "Код не подошёл или устарел. Получите новый в приложении: «Облачный парсер» → "
                "«Подключить Telegram».",
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
        self.send(chat_id, "✅ Telegram привязан к аккаунту.\n\n" + HELP, MENU)

    # ---------- actions ----------

    def accounts(self, owner: str) -> list[dict]:
        """Instagram accounts whose session the app has sent to the server."""
        return self.rows(
            "select profile_id, name, (record -> 'proxy') is not null "
            "and record -> 'proxy' <> 'null' as proxy "
            "from public.cloud_sessions where owner_id = %s order by name, profile_id",
            (owner,),
        )

    def find(self, owner: str, chat_id: int, target: int | None, request: str) -> None:
        """«Найти артистов»: which account, then how many leads, then the job starts."""
        accounts = self.accounts(owner)
        if not accounts:
            self.send(
                chat_id,
                "На сервере ещё нет ни одного аккаунта Instagram. Запустите первый облачный "
                "поиск из приложения («Облачный парсер») — аккаунт и источники отправятся "
                "на сервер, и дальше можно запускать отсюда.",
            )
            return
        if len(accounts) == 1:
            self.choose_account(owner, chat_id, accounts[0]["profile_id"], target, request)
            return
        rows = [
            [
                {
                    "text": f"{account['name'] or 'Instagram'}{' 🛡' if account['proxy'] else ''}",
                    "callback_data": f"acc:{account['profile_id']}:{target or 0}",
                }
            ]
            for account in accounts[:20]
        ]
        self.send(
            chat_id,
            "С какого аккаунта Instagram искать? (🛡 — через прокси)",
            {"inline_keyboard": rows},
        )

    def choose_account(
        self, owner: str, chat_id: int, profile: str, target: int | None, request: str
    ) -> None:
        self.awaiting.pop(chat_id, None)
        if target:
            self.launch(owner, chat_id, profile, target, request)
            return
        name = self.account_name(owner, profile)
        counts = [10, 25, 50, 100, 200]
        self.send(
            chat_id,
            f"Аккаунт <b>{esc(name)}</b>. Сколько лидов найти?",
            {
                "inline_keyboard": [
                    [{"text": str(n), "callback_data": f"cnt:{profile}:{n}"} for n in counts[:3]],
                    [{"text": str(n), "callback_data": f"cnt:{profile}:{n}"} for n in counts[3:]],
                    [{"text": "✏️ Другое число", "callback_data": f"own:{profile}"}],
                ]
            },
        )

    def ask_number(self, chat_id: int, action: str, profile: str) -> None:
        self.awaiting[chat_id] = (action, profile)
        what = "сколько лидов найти" if action == "find" else "скольким лидам написать"
        self.send(chat_id, f"Напишите числом, {what} (от 1 до {MAX_TARGET}).")

    def account_name(self, owner: str, profile: str) -> str:
        found = self.rows(
            "select name from public.cloud_sessions where owner_id = %s and profile_id = %s",
            (owner, profile),
        )
        return (found[0]["name"] if found else "") or "Instagram"

    def launch(self, owner: str, chat_id: int, profile: str, target: int, request: str) -> None:
        """The job with this account and goal; sources, categories and Scout settings are the
        last ones the app sent (for this account if it has been used, else any)."""
        last = self.rows(
            "select params from public.cloud_jobs where owner_id = %s and kind = 'scout' "
            "order by (profile_id = %s) desc, created_at desc limit 1",
            (owner, profile),
        )
        if not last:
            self.send(
                chat_id,
                "Источники ещё не заданы. Запустите первый облачный поиск из приложения "
                "(«Облачный парсер»): бот берёт его источники и настройки.",
            )
            return
        params = dict(last[0]["params"])
        params["profile_id"] = profile
        params["target"] = max(1, min(MAX_TARGET, int(target)))
        try:
            started = self.as_user(
                owner,
                "select public.cloud_start(%s, %s::jsonb) as id",
                (request, json.dumps(params)),
            )
        except psycopg.Error as error:
            self.send(chat_id, "Не запустил: " + esc(database_message(error)))
            return
        text = (
            f"🚀 Запустил поиск: {number(params['target'])} лидов, "
            f"{number(len(params.get('sources') or []))} источников, аккаунт "
            f"<b>{esc(self.account_name(owner, profile))}</b>.\n"
            "Ход поиска будет здесь, это сообщение обновляется само. Напишу, когда закончу."
        )
        sent = self.send(chat_id, text, LIVE_BUTTONS)
        if sent:
            self.cards[chat_id] = self.card(owner, sent, text, live=True)
        log.info("tg_job_started", extra={"job": str(started[0]["id"])})

    # ---------- «Написать лидам» ----------

    def unwritten(self, owner: str, limit: int = MAX_TARGET) -> list[str]:
        """New leads of the cloud parser no cloud outreach has written to, newest first."""
        rows = self.as_user(owner, "select username from public.cloud_unwritten(%s)", (int(limit),))
        return [row["username"] for row in rows]

    def last_outreach(self, owner: str) -> dict | None:
        """Messages and pace of the last cloud outreach the app started."""
        rows = self.rows(
            "select params from public.cloud_jobs where owner_id = %s and kind = 'outreach' "
            "order by created_at desc limit 1",
            (owner,),
        )
        return dict(rows[0]["params"]) if rows else None

    def write(self, owner: str, chat_id: int, count: int | None, request: str) -> None:
        """«Написать лидам»: which account (one with a proxy), then to how many."""
        if self.last_outreach(owner) is None:
            self.send(
                chat_id,
                "Сообщения ещё не заданы. Запустите первую облачную рассылку из приложения "
                "(«Облачный парсер» → «Рассылка»): бот берёт её сообщения и паузы.",
            )
            return
        waiting = len(self.unwritten(owner))
        if not waiting:
            self.send(
                chat_id,
                f"Новых лидов, которым ещё не писали, нет. Сначала найдите их: {BUTTON_FIND}.",
            )
            return
        writers = [item for item in self.accounts(owner) if item["proxy"]]
        if not writers:
            self.send(
                chat_id,
                "Писать можно только с аккаунта с прокси, а на сервере таких нет. Добавьте "
                "прокси аккаунту в приложении и запустите с него облачный поиск или рассылку.",
            )
            return
        if len(writers) == 1:
            self.choose_writer(owner, chat_id, writers[0]["profile_id"], count, request)
            return
        rows = [
            [
                {
                    "text": f"{account['name'] or 'Instagram'} 🛡",
                    "callback_data": f"wac:{account['profile_id']}:{count or 0}",
                }
            ]
            for account in writers[:20]
        ]
        self.send(
            chat_id,
            f"Ждут сообщения: {number(waiting)}. С какого аккаунта писать?",
            {"inline_keyboard": rows},
        )

    def choose_writer(
        self, owner: str, chat_id: int, profile: str, count: int | None, request: str
    ) -> None:
        self.awaiting.pop(chat_id, None)
        if count:
            self.launch_write(owner, chat_id, profile, count, request)
            return
        waiting = len(self.unwritten(owner))
        counts = [n for n in (10, 25, 50, 100) if n < waiting]
        buttons = [{"text": str(n), "callback_data": f"wcn:{profile}:{n}"} for n in counts]
        buttons.append(
            {"text": f"Всем ({number(waiting)})", "callback_data": f"wcn:{profile}:{waiting}"}
        )
        self.send(
            chat_id,
            f"Аккаунт <b>{esc(self.account_name(owner, profile))}</b>. Ждут сообщения: "
            f"{number(waiting)}. Скольким написать?",
            {
                "inline_keyboard": [
                    buttons[:3],
                    *([buttons[3:]] if buttons[3:] else []),
                    [{"text": "✏️ Другое число", "callback_data": f"wow:{profile}"}],
                ]
            },
        )

    def launch_write(
        self, owner: str, chat_id: int, profile: str, count: int, request: str
    ) -> None:
        last = self.last_outreach(owner)
        if last is None:
            self.write(owner, chat_id, count, request)
            return
        usernames = self.unwritten(owner, max(1, min(MAX_TARGET, int(count))))
        if not usernames:
            self.send(chat_id, "Новых лидов, которым ещё не писали, нет.")
            return
        params = {
            "kind": "outreach",
            "profile_id": profile,
            "usernames": usernames,
            "messages": last.get("messages") or [],
            "settings": last.get("settings") or {},
        }
        try:
            started = self.as_user(
                owner,
                "select public.cloud_start(%s, %s::jsonb) as id",
                (request, json.dumps(params)),
            )
        except psycopg.Error as error:
            self.send(chat_id, "Не запустил: " + esc(database_message(error)))
            return
        text = (
            f"✉️ Запустил рассылку: {number(len(usernames))} лидам с аккаунта "
            f"<b>{esc(self.account_name(owner, profile))}</b>, "
            f"{number(len(params['messages']))} вариантов сообщения.\n"
            "Ход рассылки будет здесь, это сообщение обновляется само. Напишу, когда закончу."
        )
        sent = self.send(chat_id, text, LIVE_BUTTONS)
        if sent:
            self.cards[chat_id] = self.card(owner, sent, text, live=True)
        log.info("tg_outreach_started", extra={"job": str(started[0]["id"])})

    # ---------- the status card ----------

    def card(self, owner: str, message_id: int, text: str, live: bool) -> dict:
        return {
            "owner": owner,
            "id": message_id,
            "text": text,
            "live": live,
            "until": self.clock() + LIVE_SECONDS,
        }

    def status_view(self, owner: str) -> tuple[str, dict | None, bool]:
        """The status text, its buttons and whether a search is going on."""
        jobs = self.rows(
            "select id, kind, profile_id, stage, cancel_requested, progress, counters, params, "
            "error, created_at from public.cloud_jobs where owner_id = %s "
            "order by created_at desc limit 4",
            (owner,),
        )
        active = [job for job in jobs if job["stage"] in ("queued", "collecting")]
        if not jobs:
            return "Облачных поисков ещё не было.", None, False
        if not active:
            return (
                "Сейчас ничего не ищу.\n\n"
                + ("Последняя рассылка: " if jobs[0]["kind"] == "outreach" else "Последний поиск: ")
                + finished_text(jobs[0]),
                find_more(jobs[0]),
                False,
            )
        text = "\n\n".join(progress_text(job) for job in active)
        return text + "\n\n<i>Обновляется само, пока идёт поиск.</i>", LIVE_BUTTONS, True

    def status(
        self, owner: str, chat_id: int, pressed: int | None = None, tap: int | None = None
    ) -> None:
        """One status message per chat, rewritten in place rather than sent again."""
        text, markup, active = self.status_view(owner)
        if pressed and self.edit(chat_id, pressed, text, markup):
            self.cards[chat_id] = self.card(owner, pressed, text, active)
            return
        if tap and self.delete(chat_id, tap):
            self.removed = tap
        card = self.cards.get(chat_id)
        if card and card["id"] >= self.latest.get(chat_id, 0):
            # Still the last message in the chat: rewrite it.
            if self.edit(chat_id, card["id"], text, markup):
                self.cards[chat_id] = self.card(owner, card["id"], text, active)
                return
        elif card:
            self.delete(chat_id, card["id"])  # far up the chat: a fresh one below instead
        sent = self.send(chat_id, text, markup)
        if sent:
            self.cards[chat_id] = self.card(owner, sent, text, active)
        else:
            self.cards.pop(chat_id, None)

    def refresh_cards(self) -> None:
        """Status cards of running searches follow the search until it ends."""
        now = self.clock()
        for chat_id, card in list(self.cards.items()):
            if not card["live"]:
                continue
            if now > card["until"] or self.owner_of(chat_id) != card["owner"]:
                card["live"] = False
                continue
            text, markup, active = self.status_view(card["owner"])
            if text == card["text"]:
                continue  # nothing new: the message is not touched
            if not self.edit(chat_id, card["id"], text, markup):
                self.cards.pop(chat_id, None)
                continue
            card.update(text=text, live=active)

    def stop(self, owner: str, chat_id: int, quiet: bool = False) -> None:
        """«Остановить»; from a button under the status, the status itself shows it."""
        active = self.rows(
            "select id from public.cloud_jobs where owner_id = %s "
            "and stage in ('queued', 'collecting') and not cancel_requested",
            (owner,),
        )
        if not active:
            self.send(chat_id, "Сейчас ничего не ищу.")
            return
        for job in active:
            try:
                self.as_user(owner, "select public.cloud_cancel(%s)", (str(job["id"]),))
            except psycopg.Error as error:
                self.send(chat_id, "Не остановил: " + esc(database_message(error)))
                return
        card = self.cards.get(chat_id)
        if card:
            text, markup, live = self.status_view(owner)
            if self.edit(chat_id, card["id"], text, markup):
                card.update(text=text, live=live, until=self.clock() + LIVE_SECONDS)
                if quiet:
                    return
        self.send(chat_id, "⏹ Останавливаю. Найденные лиды остаются в CRM.")

    def leads(self, owner: str, chat_id: int) -> None:
        rows = self.rows(
            "select username, category, confidence, outcome, profile from public.cloud_candidates "
            "where owner_id = %s and outcome in ('added', 'updated') order by id desc limit %s",
            (owner, LEADS_SHOWN),
        )
        if not rows:
            self.send(chat_id, "В CRM от облачного парсера пока никого.")
            return
        lines = []
        for row in rows:
            profile = row["profile"] or {}
            name = esc(row["username"])
            parts = [f'<a href="https://www.instagram.com/{name}/">@{name}</a>']
            if profile.get("followers") is not None:
                parts.append(f"{number(profile['followers'])} подп.")
            if row["category"]:
                label = CATEGORY.get(row["category"], row["category"])
                parts.append(
                    f"{label} {row['confidence']}%" if row["confidence"] is not None else label
                )
            emails = profile.get("emails") or []
            if emails:
                parts.append(esc(emails[0]))
            lines.append(" · ".join(parts))
        self.send(chat_id, "📋 Последние в CRM:\n\n" + "\n".join(lines))

    def toggle_notify(self, owner: str, chat_id: int) -> None:
        changed = self.rows(
            "update public.tg_links set notify = not notify where owner_id = %s returning notify",
            (owner,),
        )
        on = bool(changed and changed[0]["notify"])
        self.send(chat_id, "🔔 Уведомления включены." if on else "🔕 Уведомления выключены.")

    # ---------- what happened meanwhile ----------

    def tick(self) -> None:
        self.refresh_cards()
        self.announce()
        self.watch()

    def admin_chats(self) -> list[int]:
        rows = self.rows(
            "select l.chat_id from public.tg_links l join public.profiles p on p.id = l.owner_id "
            "where p.role = 'admin' and not p.blocked"
        )
        return [row["chat_id"] for row in rows]

    def announce(self) -> None:
        """Ended jobs go to their owners (and failures to admins), each once."""
        jobs = self.rows(
            "select j.id, j.kind, j.owner_id, j.profile_id, j.stage, j.error, j.counters, "
            "j.progress, j.params, "
            f"j.finished_at > now() - interval '{ANNOUNCE_HOURS} hours' as recent, "
            "l.chat_id, l.notify, u.email "
            "from public.cloud_jobs j "
            "left join public.tg_links l on l.owner_id = j.owner_id "
            "left join auth.users u on u.id = j.owner_id "
            "where j.stage in ('completed', 'failed', 'cancelled') "
            "and j.notified is distinct from j.stage "
            "order by j.finished_at limit 50"
        )
        admins = self.admin_chats() if any(job["stage"] == "failed" for job in jobs) else []
        for job in jobs:
            # Marked first: a message Telegram refuses is not tried again and again.
            self.rows(
                "update public.cloud_jobs set notified = %s where id = %s",
                (job["stage"], job["id"]),
            )
            if not job["recent"]:
                continue
            if job["chat_id"] and job["notify"]:
                self.send(job["chat_id"], finished_text(job), find_more(job))
            if job["stage"] == "failed":
                what = (
                    "Облачная рассылка упала"
                    if job["kind"] == "outreach"
                    else "Облачный поиск упал"
                )
                for chat in admins:
                    if chat != job["chat_id"]:
                        self.send(
                            chat,
                            f"⚠️ {what} у {esc(job['email'] or job['owner_id'])}\n"
                            f"Задача <code>{esc(job['id'])}</code>\n"
                            f"{esc(job['error'] or 'без текста')}",
                        )

    def watch(self) -> None:
        """A process that holds a job without working on it, or a queue nobody takes (a
        job waiting for its account, busy with another job, is not a problem)."""
        problems = self.rows(
            "select id, kind as job_kind, 'stalled' as kind from public.cloud_jobs "
            f"where stage = 'collecting' and locked_until < now() - interval '{STALLED}' "
            "union all "
            "select j.id, j.kind as job_kind, 'waiting' as kind from public.cloud_jobs j "
            f"where j.stage = 'queued' and j.created_at < now() - interval '{WAITING}' "
            "and not exists (select 1 from public.cloud_jobs o where o.owner_id = j.owner_id "
            "and o.profile_id = j.profile_id and o.id <> j.id and o.stage = 'collecting')"
        )
        fresh = {(str(row["id"]), row["kind"]): row["job_kind"] for row in problems}
        new = [item for item in fresh if item not in self.reported]
        if not new:
            return
        self.reported.update(new)
        text = {
            "stalled": "процесс перестал продлевать задачу (контейнер упал или завис)",
            "waiting": f"задача ждёт в очереди дольше {WAITING.split()[0]} минут",
        }
        containers = sorted({CONTAINER[fresh[item]] for item in new})
        for chat in self.admin_chats():
            self.send(
                chat,
                "⚠️ Облачный парсер и рассылка:\n"
                + "\n".join(
                    f"<code>{job}</code> ({KIND[fresh[(job, kind)]]}) — {text[kind]}"
                    for job, kind in new
                )
                + "\n\nЖурнал: "
                + ", ".join(f"<code>docker logs --tail 100 {name}</code>" for name in containers),
            )


KIND = {"scout": "поиск", "outreach": "рассылка"}
CONTAINER = {"scout": "alf-cloud-parser", "outreach": "alf-cloud-outreach"}


def progress_text(job: dict) -> str:
    if job.get("kind") == "outreach":
        return outreach_progress(job)
    p = job["progress"] or {}
    params = job["params"] or {}
    if job["stage"] == "queued":
        return "⏳ Поиск ждёт своей очереди на сервере."
    target = p.get("target") or params.get("target")
    lines = [f"🔎 Ищу: найдено <b>{number(p.get('found') or 0)}</b> из {number(target)}"]
    lines.append(
        f"Кандидатов просмотрено: {number(p.get('candidates') or 0)} · источников пройдено: "
        f"{number(p.get('sources_done') or 0)} из {number(len(params.get('sources') or []))}"
    )
    if p.get("step"):
        lines.append(f"Сейчас: {esc(STEP.get(p['step'], p['step']))}")
    if p.get("status") == "paused" and p.get("error"):
        lines.append(f"Пауза: {esc(p['error'])}")
    if job.get("cancel_requested"):
        lines.append("⏹ Останавливаю…")
    c = job["counters"] or {}
    lines.append(
        f"В CRM: новых {number(c.get('added') or 0)}, обновлено {number(c.get('updated') or 0)}"
    )
    return "\n".join(lines)


def outreach_progress(job: dict) -> str:
    p = job["progress"] or {}
    total = p.get("total") or len((job["params"] or {}).get("usernames") or [])
    if job["stage"] == "queued":
        return (
            f"⏳ Рассылка {number(total)} лидам ждёт своей очереди (или пока аккаунт "
            "закончит поиск)."
        )
    lines = [f"✉️ Пишу: отправлено <b>{number(p.get('sent') or 0)}</b> из {number(total)}"]
    lines.append(
        f"Пропущено: {number(p.get('skipped') or 0)} · не ушло: {number(p.get('failed') or 0)}"
    )
    if p.get("sender_status") == "rate_limited":
        lines.append("Instagram ограничил действия аккаунта — жду конца перерыва.")
    elif p.get("daily_limit") and (p.get("sent_24h") or 0) >= p["daily_limit"]:
        lines.append(
            f"Дневной лимит {number(p['daily_limit'])} сообщений — продолжу, когда пройдут сутки."
        )
    elif p.get("queued"):
        lines.append("Следующее сообщение — после паузы между сообщениями.")
    if job.get("cancel_requested"):
        lines.append("⏹ Останавливаю…")
    return "\n".join(lines)


def finished_text(job: dict) -> str:
    c = job["counters"] or {}
    if job.get("kind") == "outreach":
        summary = (
            f"отправлено {number(c.get('sent') or 0)} из {number(c.get('total') or 0)}; "
            f"пропущено {number(c.get('skipped') or 0)}, не ушло {number(c.get('failed') or 0)}"
        )
        if job["stage"] == "completed":
            return "✅ Рассылка закончена: " + summary + "."
        if job["stage"] == "cancelled":
            return "⏹ Рассылка остановлена: " + summary + "."
        if job["stage"] == "failed":
            error = esc(job.get("error") or "ошибка на сервере")
            return f"❌ Рассылка прервалась: {error}\n({summary})"
        return progress_text(job)
    params = job["params"] or {}
    target = (job.get("progress") or {}).get("target") or params.get("target")
    summary = (
        f"найдено {number(c.get('found') or 0)} из {number(target)}; в CRM новых "
        f"{number(c.get('added') or 0)}, обновлено {number(c.get('updated') or 0)}"
    )
    if c.get("filtered"):
        summary += f", не прошли фильтр {number(c['filtered'])}"
    if job["stage"] == "completed":
        return "✅ Поиск закончен: " + summary + "."
    if job["stage"] == "cancelled":
        return "⏹ Поиск остановлен: " + summary + "."
    if job["stage"] == "failed":
        return f"❌ Поиск прервался: {esc(job.get('error') or 'ошибка на сервере')}\n({summary})"
    return progress_text(job)


def find_more(job: dict) -> dict:
    """After a search: the same account and goal again (or a fresh choice when the job has
    no account) and writing to what it found. After an outreach: the next one."""
    if job.get("kind") == "outreach":
        return {
            "inline_keyboard": [
                [
                    {"text": BUTTON_WRITE, "callback_data": "write"},
                    {"text": BUTTON_LEADS, "callback_data": "leads"},
                ]
            ]
        }
    target = int((job.get("params") or {}).get("target") or DEFAULT_TARGET)
    profile = job.get("profile_id") or ""
    again = f"cnt:{profile}:{target}" if re.fullmatch(r"[0-9a-f]{32}", profile) else "find"
    rows = [[{"text": f"🔎 Найти ещё {target}", "callback_data": again}]]
    if (job.get("counters") or {}).get("added"):
        rows[0].append({"text": "✉️ Написать найденным", "callback_data": "write"})
    rows.append([{"text": BUTTON_LEADS, "callback_data": "leads"}])
    return {"inline_keyboard": rows}


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
