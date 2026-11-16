"""«Threads → Публикации»: accounts, text posts and the publishing queue.

The queue runs in the core, in its own thread: hiding the window changes nothing. Times are
kept in UTC; posting slots are wall-clock times of this computer. Nothing is published while
the computer sleeps or the app is closed: what fell due meanwhile becomes «пропущено» and
waits for the user instead of going out all at once.

One post makes at most one Threads post. The container is created once and kept. The
moment the publish request goes out is saved first; any unclear answer, a crash or a
restart after that leads to a check of the container and the profile's recent posts, and
the post is published again only when it is certain it did not come out.
"""

import base64
import html
import logging
import secrets
import threading
from collections import deque
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select

from .. import secret_box
from ..errors import UserError
from ..models import Setting, ThreadsAccount, ThreadsPost, utcnow
from .api import ThreadsClient, ThreadsError, authorize_url, text_errors, text_length
from .browser import BrowserClient

log = logging.getLogger(__name__)

TICK_SECONDS = 15
# A post more than this late (the app was closed, the computer slept) is not published by
# itself: it waits as «пропущено».
GRACE = timedelta(minutes=10)
# A retried post this late is given up the same way.
RETRY_HORIZON = timedelta(hours=6)
BACKOFF = (60, 300, 900, 3600, 3 * 3600)
MAX_ATTEMPTS = 5
# After an unclear publish answer: wait before checking, and how long checks may fail.
VERIFY_AFTER = timedelta(seconds=45)
REVIEW_AFTER = timedelta(hours=6)
CONTAINER_LIFETIME = timedelta(hours=23)
# Long-lived tokens can be refreshed once they are a day old; done about weekly.
REFRESH_MIN_AGE = timedelta(hours=24)
REFRESH_EVERY = timedelta(days=7)
EXPIRY_WARNING = timedelta(days=7)
WAKE_GAP = timedelta(minutes=2)
AUTH_WAIT = timedelta(minutes=10)
DEFAULT_REDIRECT = ""
MAX_SLOTS = 12
OPEN = ("scheduled", "publishing", "review", "missed")
STATUS_LABELS = {
    "draft": "Черновик",
    "scheduled": "Запланирован",
    "publishing": "Публикуется",
    "published": "Опубликован",
    "failed": "Ошибка",
    "review": "Требует проверки",
    "cancelled": "Отменён",
    "missed": "Пропущен",
}
ACCOUNT_LABELS = {
    "active": "Подключён",
    "reauth": "Нужно переподключить",
    "disconnected": "Отключён",
}


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def iso(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.isoformat() if value else None


def parse_time(value) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise UserError("Некорректное время публикации.") from None
    if moment.tzinfo is None:
        raise UserError("Время публикации без часового пояса.")
    return moment.astimezone(timezone.utc)


def local_to_utc(naive: datetime) -> datetime:
    """A wall-clock time of this computer (its zone and daylight saving) in UTC."""
    return naive.astimezone(timezone.utc)


def utc_to_local(moment: datetime) -> datetime:
    return moment.astimezone()


def clean_slots(values) -> list[str]:
    slots = set()
    for value in values or []:
        try:
            hours, minutes = str(value).strip().split(":")
            hour, minute = int(hours), int(minutes)
        except ValueError:
            raise UserError(f"Некорректное время слота: {value}.") from None
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise UserError(f"Некорректное время слота: {value}.")
        slots.add(f"{hour:02d}:{minute:02d}")
    if not slots:
        raise UserError("Добавьте хотя бы одно время публикации.")
    if len(slots) > MAX_SLOTS:
        raise UserError(f"Не больше {MAX_SLOTS} публикаций в день.")
    return sorted(slots)


class ThreadsService:
    def __init__(
        self,
        sessions,
        now: Callable[[], datetime] = utcnow,
        client: Callable[[str | None], ThreadsClient] = lambda token: ThreadsClient(token),
        to_utc: Callable[[datetime], datetime] = local_to_utc,
        to_local: Callable[[datetime], datetime] = utc_to_local,
        sleep: Callable[[float], None] | None = None,
        browser=None,
        browser_profiles: Callable[[], list[dict]] = lambda: [],
    ):
        self.sessions = sessions
        self.now = now
        self.client = client
        self.browser = browser
        self.browser_profiles = browser_profiles
        self.browser_auth: dict | None = None
        self._login_diagnostic = None
        self.to_utc = to_utc
        self.to_local = to_local
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.sleep = sleep or (lambda seconds: self.stopping.wait(seconds))
        self.thread: threading.Thread | None = None
        self.last_tick: datetime | None = None
        # «Опубликовать сейчас» goes out even while the queue is paused.
        self.manual: set[int] = set()
        self.notices: deque[dict] = deque(maxlen=50)
        self.auth: dict | None = None
        self.server: ThreadingHTTPServer | None = None

    # ---------- RPC ----------

    def call(self, method: str, params: dict):
        handlers = {
            "threads.state": lambda p: self.state(),
            "threads.app_save": self.app_save,
            "threads.auth_start": self.auth_start,
            "threads.auth_finish": self.auth_finish,
            "threads.auth_cancel": lambda p: self.auth_cancel(),
            "threads.token_add": self.token_add,
            "threads.browser_start": self.browser_start,
            "threads.browser_cancel": lambda p: self.browser_cancel(),
            "threads.account_control": self.account_control,
            "threads.account_slots": self.account_slots,
            "threads.queue_control": self.queue_control,
            "threads.validate": lambda p: self.validate(p.get("text", "")),
            "threads.post_save": self.post_save,
            "threads.post_schedule": self.post_schedule,
            "threads.post_publish_now": self.post_publish_now,
            "threads.post_cancel": self.post_cancel,
            "threads.post_delete": self.post_delete,
            "threads.post_resolve": self.post_resolve,
            "threads.missed_reschedule": self.missed_reschedule,
            "threads.shell_internal": lambda p: self.shell(),
            "threads.pause_internal": self.queue_control,
        }
        handler = handlers.get(method)
        if handler is None:
            raise UserError("Неизвестный метод приложения.")
        return handler(params or {})

    # ---------- lifecycle ----------

    def restore(self) -> None:
        """After a restart: a post cut off before its publish request goes back to the
        queue; one cut off after it stays «публикуется» and is checked before anything."""
        with self.lock, self.sessions.begin() as session:
            for post in session.scalars(
                select(ThreadsPost).where(ThreadsPost.status == "publishing")
            ):
                if post.publish_sent_at is None:
                    post.status = "scheduled"
                    post.next_attempt_at = None
                else:
                    post.next_attempt_at = self.now()
        self._ensure_thread()

    def shutdown(self) -> None:
        self.stopping.set()
        self.wakeup.set()
        self._stop_server()

    def _ensure_thread(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.wakeup.set()
                return
            self.stopping.clear()
            self.thread = threading.Thread(target=self._run, name="threads-queue", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        while not self.stopping.is_set():
            busy = False
            try:
                busy = self.tick()
            except Exception as error:  # the queue must survive any single failure
                log.warning(
                    "threads_tick_failed",
                    extra={"error_type": type(error).__name__, "where": "threads.tick"},
                )
            if not busy:
                self.wakeup.wait(TICK_SECONDS)
            self.wakeup.clear()

    def tick(self) -> bool:
        """One step: refresh tokens, mark what was missed, check unclear publications, then
        publish at most one due post. True when another post may be due right away."""
        self._capture_browser()
        now = self.now()
        if self.last_tick is not None and now - self.last_tick > WAKE_GAP:
            log.info("threads_queue_resumed", extra={"where": "threads.tick"})
        self.last_tick = now
        self._refresh_tokens(now)
        self._mark_missed(now)
        self._verify(now)
        return self._publish_due(now)

    # ---------- settings ----------

    def _setting(self, session, key: str) -> dict:
        row = session.get(Setting, key)
        return dict(row.value) if row and isinstance(row.value, dict) else {}

    def _put(self, session, key: str, value: dict) -> None:
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value

    def _paused(self, session) -> bool:
        return bool(self._setting(session, "threads_queue").get("paused"))

    def queue_control(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            self._put(session, "threads_queue", {"paused": bool(params.get("paused"))})
        self._ensure_thread()
        return self.state()

    # ---------- the user's Meta app and authorization ----------

    def browser_start(self, params: dict) -> dict:
        profile_id = str(params.get("profile_id") or "")
        if self.browser is None or profile_id not in {p["id"] for p in self.browser_profiles()}:
            raise UserError("Выберите существующий браузерный профиль Instagram.")
        self.browser_auth = {
            "profile_id": profile_id,
            "status": "waiting",
            "message": "Войдите в Threads в открытом окне.",
        }
        return self.state()

    def browser_cancel(self) -> dict:
        self.browser_auth = None
        return self.state()

    def _capture_browser(self) -> None:
        auth = self.browser_auth
        if not auth or auth["status"] != "waiting":
            return
        try:
            found = self.browser.threads_identity(auth["profile_id"])
            if not found.get("username"):
                diagnostic = found.get("diagnostic")
                if diagnostic is not None and diagnostic != self._login_diagnostic:
                    self._login_diagnostic = diagnostic
                    log.info("threads_login_waiting", extra={"detail": diagnostic})
                if found.get("message"):
                    self.browser_auth = {**auth, "message": found["message"]}
                return
            username = str(found["username"])
            with self.lock, self.sessions.begin() as session:
                linked = session.scalar(
                    select(ThreadsAccount).where(
                        ThreadsAccount.browser_profile_id == auth["profile_id"],
                        ThreadsAccount.status != "disconnected",
                    )
                )
                if linked and linked.username.lower() != username.lower():
                    raise UserError(
                        "В профиле другой аккаунт Threads. Сначала отключите прежний аккаунт, "
                        "чтобы не перенести его очередь."
                    )
                account = linked or session.scalar(
                    select(ThreadsAccount).where(
                        func.lower(ThreadsAccount.username) == username.lower()
                    )
                )
                if account is None:
                    account = ThreadsAccount(
                        threads_user_id="web-" + secrets.token_hex(16), slots=["10:00"]
                    )
                    session.add(account)
                account.username = username
                account.name = str(found.get("name") or username)[:120]
                account.auth_mode = "browser"
                account.browser_profile_id = auth["profile_id"]
                account.token_box = None
                account.token_expires_at = None
                account.token_refreshed_at = None
                account.status = "active"
                account.reason = ""
                account.connected_at = self.now()
            self.browser_auth = {
                **auth,
                "status": "done",
                "message": f"Подключён @{username}. Сессия сохранена автоматически.",
            }
            self._ensure_thread()
        except UserError as error:
            self.browser_auth = {**auth, "status": "error", "message": str(error)}
        except Exception as error:
            # A closing/loading tab is not a failed login; the next state poll retries.
            log.warning(
                "threads_login_check_failed",
                extra={
                    "error_type": type(error).__name__,
                    "where": "threads.browser_login",
                },
            )
            return

    def _client(self, account: ThreadsAccount, post: ThreadsPost):
        if account.auth_mode == "browser":
            if self.browser is None or not account.browser_profile_id:
                raise ThreadsError("auth", "Переподключите браузерный профиль Threads.")
            return BrowserClient(
                self.browser,
                account.browser_profile_id,
                account.username,
                post.text,
                post.publish_sent_at is not None,
            )
        return self.client(self._token(account))

    def _app(self, session) -> dict:
        return self._setting(session, "threads_app")

    def _secret(self, app: dict) -> str:
        if not app.get("secret"):
            raise UserError("Укажите App ID и секрет своего приложения Meta.")
        return secret_box.protect(base64.b64decode(app["secret"]), decrypt=True).decode()

    def app_save(self, params: dict) -> dict:
        app_id = str(params.get("app_id") or "").strip()
        if not app_id.isdigit() or len(app_id) > 30:
            raise UserError("Threads App ID — это число из кабинета Meta.")
        redirect = str(params.get("redirect_uri") or DEFAULT_REDIRECT).strip()
        if not redirect:
            raise UserError("Укажите адрес возврата, зарегистрированный в вашем приложении Meta.")
        parsed = urlparse(redirect)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise UserError("Адрес возврата должен начинаться с http:// или https://.")
        with self.lock, self.sessions.begin() as session:
            app = self._app(session)
            secret = str(params.get("app_secret") or "").strip()
            if secret:
                if len(secret) > 100 or not secret.isalnum():
                    raise UserError("Секрет приложения — строка из букв и цифр.")
                app["secret"] = base64.b64encode(secret_box.protect(secret.encode())).decode()
            elif not app.get("secret"):
                raise UserError("Укажите секрет приложения Threads.")
            app.update({"app_id": app_id, "redirect_uri": redirect})
            self._put(session, "threads_app", app)
        return self.state()

    def auth_start(self, params: dict) -> dict:
        """The authorization page for the browser. A plain http://localhost return address
        is caught by a one-time local listener; any other one is pasted back by the user."""
        with self.sessions() as session:
            app = self._app(session)
        if not app.get("app_id") or not app.get("secret"):
            raise UserError("Сначала сохраните App ID и секрет своего приложения Meta.")
        state = secrets.token_urlsafe(24)
        redirect = app["redirect_uri"]
        self._stop_server()
        listening = False
        parsed = urlparse(redirect)
        if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
            listening = self._listen(parsed, state)
        self.auth = {
            "state": state,
            "redirect_uri": redirect,
            "status": "waiting",
            "message": "",
            "listening": listening,
            "until": self.now() + AUTH_WAIT,
        }
        return {
            "url": authorize_url(app["app_id"], redirect, state),
            "listening": listening,
            "redirect_uri": redirect,
        }

    def auth_cancel(self) -> dict:
        self._stop_server()
        self.auth = None
        return self.state()

    def auth_finish(self, params: dict) -> dict:
        """The address the browser was sent back to, pasted by the user."""
        query = parse_qs(urlparse(str(params.get("url") or "")).query)
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        if not code:
            denied = (query.get("error_description") or query.get("error") or [""])[0]
            raise UserError(denied or "В адресе нет кода авторизации. Скопируйте адрес целиком.")
        self._complete(code.split("#")[0], state)
        if self.auth and self.auth["status"] == "error":
            raise UserError(self.auth["message"])
        return self.state()

    def _complete(self, code: str, state: str) -> None:
        auth = self.auth
        if not auth or not secrets.compare_digest(state, auth["state"]):
            self.auth = {
                **(auth or {}),
                "status": "error",
                "message": "Устаревшая авторизация: начните заново.",
            }
            return
        if self.now() > auth["until"]:
            self.auth = {
                **auth,
                "status": "error",
                "message": "Время авторизации вышло: начните заново.",
            }
            return
        auth["status"] = "exchanging"
        try:
            with self.sessions() as session:
                app = self._app(session)
            secret = self._secret(app)
            short = self.client(None).exchange_code(
                app["app_id"], secret, code, auth["redirect_uri"]
            )
            if not short.get("access_token"):
                raise ThreadsError("unknown", "Threads не выдал токен.")
            long = self.client(short["access_token"]).long_lived(secret)
            token = long.get("access_token") or short["access_token"]
            name = self._connect(token, long.get("expires_in"))
            self.auth = {**auth, "status": "done", "message": f"Подключён @{name}"}
        except (ThreadsError, UserError) as error:
            self.auth = {**auth, "status": "error", "message": str(error)}
        finally:
            self._stop_server(join=False)

    def _listen(self, parsed, state: str) -> bool:
        service = self
        path = parsed.path or "/"

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                url = urlparse(self.path)
                if url.path != path:
                    self.send_error(404)
                    return
                query = parse_qs(url.query)
                code = (query.get("code") or [""])[0]
                given = (query.get("state") or [""])[0]
                if code:
                    service._complete(code.split("#")[0], given)
                else:
                    reason = (query.get("error_description") or ["Доступ не выдан."])[0]
                    service.auth = {**(service.auth or {}), "status": "error", "message": reason}
                done = (service.auth or {}).get("status") == "done"
                text = (
                    "Аккаунт Threads подключён. Вернитесь в Artist Lead Finder."
                    if done
                    else "Не получилось: " + (service.auth or {}).get("message", "")
                )
                body = (
                    "<!doctype html><meta charset=utf-8><title>Threads</title>"
                    f"<p style='font:16px sans-serif;margin:40px'>{html.escape(text)}</p>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # the query holds the code: never logged
                return

        try:
            host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
            server = ThreadingHTTPServer((host, parsed.port or 80), Callback)
        except OSError:
            return False
        server.timeout = 1
        self.server = server
        threading.Thread(target=server.serve_forever, name="threads-auth", daemon=True).start()
        # The listener lives for the authorization window only.
        timer = threading.Timer(AUTH_WAIT.total_seconds(), lambda: self._stop_server(server))
        timer.daemon = True
        timer.start()
        return True

    def _stop_server(self, only=None, join=True) -> None:
        server = self.server
        if server is None or (only is not None and server is not only):
            return
        self.server = None

        def stop():
            server.shutdown()
            server.server_close()

        if join:
            stop()
        else:
            # Called from the listener's own request: it stops once the answer is sent.
            threading.Thread(target=stop, daemon=True).start()

    def token_add(self, params: dict) -> dict:
        """A token generated in the Meta app dashboard (Threads use case → token
        generator), pasted by the user. It is kept and refreshed like a connected one."""
        token = str(params.get("token") or "").strip()
        if len(token) < 20 or len(token) > 1000 or any(c.isspace() for c in token):
            raise UserError("Вставьте токен доступа Threads целиком.")
        expires_in = None
        client = self.client(token)
        try:
            client.me()
        except ThreadsError as error:
            raise UserError(f"Токен не подошёл: {error}") from None
        with self.sessions() as session:
            app = self._app(session)
        if app.get("secret"):
            try:
                found = client.long_lived(self._secret(app))
                token, expires_in = found.get("access_token") or token, found.get("expires_in")
            except ThreadsError:
                pass  # already long-lived: refreshed by the queue when a day old
        self._connect(token, expires_in)
        return self.state()

    def _connect(self, token: str, expires_in) -> str:
        me = self.client(token).me()
        user_id = str(me.get("id") or "")
        if not user_id:
            raise ThreadsError("unknown", "Threads не назвал профиль.")
        now = self.now()
        with self.lock, self.sessions.begin() as session:
            account = session.scalar(
                select(ThreadsAccount).where(ThreadsAccount.threads_user_id == user_id)
            )
            if account is None:
                account = ThreadsAccount(threads_user_id=user_id, slots=["10:00"])
                session.add(account)
            account.username = str(me.get("username") or "")[:64]
            account.name = str(me.get("name") or "")[:120]
            account.token_box = secret_box.protect(token.encode())
            account.auth_mode = "api"
            account.browser_profile_id = None
            account.token_expires_at = (
                now + timedelta(seconds=int(expires_in)) if expires_in else None
            )
            account.token_refreshed_at = now
            account.status = "active"
            account.reason = ""
            account.connected_at = now
        self._ensure_thread()
        return me.get("username") or user_id

    def _token(self, account: ThreadsAccount) -> str:
        if not account.token_box:
            raise ThreadsError("auth", "Аккаунт отключён: подключите его снова.")
        return secret_box.protect(account.token_box, decrypt=True).decode()

    def account_control(self, params: dict) -> dict:
        action = params.get("action")
        with self.lock, self.sessions.begin() as session:
            account = session.get(ThreadsAccount, int(params.get("id") or 0))
            if account is None:
                raise UserError("Аккаунт Threads не найден.")
            if action == "pause":
                account.paused = True
            elif action == "resume":
                account.paused = False
            elif action == "disconnect":
                # The token is forgotten; posts and history stay.
                account.token_box = None
                account.token_expires_at = None
                account.status = "disconnected"
                account.reason = "Отключён пользователем"
            else:
                raise UserError("Неизвестное действие.")
        self._ensure_thread()
        return self.state()

    def account_slots(self, params: dict) -> dict:
        slots = clean_slots(params.get("slots"))
        with self.lock, self.sessions.begin() as session:
            account = session.get(ThreadsAccount, int(params.get("id") or 0))
            if account is None:
                raise UserError("Аккаунт Threads не найден.")
            account.slots = slots
        return self.state()

    def _refresh_tokens(self, now: datetime) -> None:
        with self.sessions() as session:
            accounts = list(
                session.scalars(select(ThreadsAccount).where(ThreadsAccount.status == "active"))
            )
        for account in accounts:
            if account.auth_mode == "browser":
                continue
            expires = as_utc(account.token_expires_at)
            if expires is not None and expires <= now:
                self._needs_auth(account.id, "Срок доступа истёк: переподключите аккаунт.")
                continue
            refreshed = as_utc(account.token_refreshed_at) or as_utc(account.connected_at)
            if now - refreshed < REFRESH_MIN_AGE:
                continue
            due = (
                now - refreshed >= REFRESH_EVERY
                or (expires is not None and expires - now < EXPIRY_WARNING)
                or expires is None
            )
            if not due:
                continue
            try:
                found = self.client(self._token(account)).refresh()
            except ThreadsError as error:
                if error.kind == "auth":
                    self._needs_auth(account.id, str(error))
                continue  # offline: tried again on a later tick
            token = found.get("access_token")
            if not token:
                continue
            with self.lock, self.sessions.begin() as session:
                row = session.get(ThreadsAccount, account.id)
                row.token_box = secret_box.protect(token.encode())
                row.token_refreshed_at = now
                if found.get("expires_in"):
                    row.token_expires_at = now + timedelta(seconds=int(found["expires_in"]))

    def _needs_auth(self, account_id: int, reason: str) -> None:
        with self.lock, self.sessions.begin() as session:
            account = session.get(ThreadsAccount, account_id)
            if account.status == "reauth":
                return
            account.status = "reauth"
            account.reason = reason[:300]
            name = account.username
        self._notify("reauth", "Переподключите Threads", f"@{name}: {reason}")

    # ---------- posts ----------

    def validate(self, text: str) -> dict:
        return {"length": text_length(text), "errors": text_errors(text)}

    def _post(self, session, params: dict) -> ThreadsPost:
        post = session.get(ThreadsPost, int(params.get("id") or 0))
        if post is None:
            raise UserError("Пост не найден.")
        return post

    def _apply(self, session, post: ThreadsPost, params: dict) -> None:
        if "text" in params:
            text = str(params.get("text") or "")
            if len(text) > 4000:
                raise UserError("Слишком длинный текст.")
            if text != post.text:
                if post.container_id and post.status != "draft":
                    # A changed text needs a new container; the old one is never published.
                    post.container_id = None
                    post.container_at = None
                post.text = text
        if "account_id" in params:
            account_id = params.get("account_id")
            if account_id in (None, ""):
                post.account_id = None
            else:
                account = session.get(ThreadsAccount, int(account_id))
                if account is None:
                    raise UserError("Аккаунт Threads не найден.")
                if account.id != post.account_id:
                    post.container_id = None
                    post.container_at = None
                post.account_id = account.id

    def post_save(self, params: dict) -> dict:
        """A draft, or the text of a post still waiting in the queue."""
        with self.lock, self.sessions.begin() as session:
            if params.get("id"):
                post = self._post(session, params)
                if post.status not in ("draft", "scheduled", "missed", "failed", "cancelled"):
                    raise UserError("Этот пост уже нельзя менять.")
            else:
                post = ThreadsPost(status="draft", text="")
                session.add(post)
            self._apply(session, post, params)
            session.flush()
            post_id = post.id
        return {**self.state(), "saved": post_id}

    def _ready(self, session, post: ThreadsPost) -> ThreadsAccount:
        errors = text_errors(post.text)
        if errors:
            raise UserError(" ".join(errors))
        account = session.get(ThreadsAccount, post.account_id or 0)
        if account is None:
            raise UserError("Выберите аккаунт Threads.")
        if account.status == "disconnected":
            raise UserError("Аккаунт отключён: подключите его снова.")
        return account

    def _queue(self, post: ThreadsPost, at: datetime, slot: bool) -> None:
        post.status = "scheduled"
        post.scheduled_at = at
        post.slot = slot
        post.attempts = 0
        post.next_attempt_at = None
        post.publish_sent_at = None
        post.error = ""

    def post_schedule(self, params: dict) -> dict:
        """At a given time, or with `slot` into the account's next free posting time."""
        now = self.now()
        with self.lock, self.sessions.begin() as session:
            if params.get("id"):
                post = self._post(session, params)
                if post.status in ("publishing", "published"):
                    raise UserError("Пост уже публикуется или опубликован.")
                if post.status == "review":
                    raise UserError("Сначала проверьте, вышел ли этот пост.")
            else:
                post = ThreadsPost(status="draft", text="")
                session.add(post)
            self._apply(session, post, params)
            account = self._ready(session, post)
            if params.get("slot"):
                at = self._next_slot(session, account, now, exclude=post.id)
            else:
                at = parse_time(params.get("at"))
                if at < now - timedelta(minutes=1):
                    raise UserError("Это время уже прошло.")
            self._queue(post, at, bool(params.get("slot")))
            session.flush()
            post_id = post.id
        self._ensure_thread()
        return {**self.state(), "saved": post_id}

    def post_publish_now(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            if params.get("id"):
                post = self._post(session, params)
                if post.status in ("publishing", "published"):
                    raise UserError("Пост уже публикуется или опубликован.")
                if post.status == "review":
                    raise UserError("Сначала проверьте, вышел ли этот пост.")
            else:
                post = ThreadsPost(status="draft", text="")
                session.add(post)
            self._apply(session, post, params)
            account = self._ready(session, post)
            if account.status == "reauth":
                raise UserError("Доступ к аккаунту истёк: переподключите его.")
            self._queue(post, self.now(), False)
            session.flush()
            post_id = post.id
        self.manual.add(post_id)
        self._ensure_thread()
        return {**self.state(), "saved": post_id}

    def post_cancel(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            post = self._post(session, params)
            if post.status not in ("draft", "scheduled", "missed", "failed"):
                raise UserError("Этот пост уже нельзя отменить.")
            post.status = "cancelled"
            post.next_attempt_at = None
        self.manual.discard(int(params.get("id") or 0))
        return self.state()

    def post_delete(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            post = self._post(session, params)
            if post.status not in ("draft", "cancelled"):
                raise UserError("Удалить можно черновик или отменённый пост.")
            session.delete(post)
        return self.state()

    def post_resolve(self, params: dict) -> dict:
        """«Требует проверки»: the user looked at the profile."""
        with self.lock, self.sessions.begin() as session:
            post = self._post(session, params)
            if post.status != "review":
                raise UserError("Этот пост не ждёт проверки.")
            if params.get("published"):
                post.status = "published"
                post.published_at = post.publish_sent_at or self.now()
                link = str(params.get("permalink") or "").strip()
                if link.startswith("https://"):
                    post.permalink = link[:300]
                post.error = ""
            else:
                # Not out: back to a draft with a fresh container, nothing goes by itself.
                post.status = "draft"
                post.container_id = None
                post.container_at = None
                post.publish_sent_at = None
                post.error = ""
        return self.state()

    def missed_reschedule(self, params: dict) -> dict:
        """Missed posts into their accounts' next free slots, oldest first."""
        wanted = {int(value) for value in params.get("ids") or []}
        now = self.now()
        moved = 0
        with self.lock, self.sessions.begin() as session:
            query = (
                select(ThreadsPost)
                .where(ThreadsPost.status == "missed")
                .order_by(ThreadsPost.scheduled_at, ThreadsPost.id)
            )
            for post in session.scalars(query):
                if wanted and post.id not in wanted:
                    continue
                account = session.get(ThreadsAccount, post.account_id or 0)
                if account is None:
                    continue
                self._queue(post, self._next_slot(session, account, now, exclude=post.id), True)
                session.flush()
                moved += 1
        self._ensure_thread()
        return {**self.state(), "moved": moved}

    def _next_slot(self, session, account: ThreadsAccount, after: datetime, exclude=None):
        taken = [
            as_utc(value)
            for value in session.scalars(
                select(ThreadsPost.scheduled_at).where(
                    ThreadsPost.account_id == account.id,
                    ThreadsPost.status.in_(("scheduled", "publishing")),
                    ThreadsPost.id != (exclude or 0),
                )
            )
            if value is not None
        ]
        slots = clean_slots(account.slots)
        start = self.to_local(after).date()
        for offset in range(400):
            day = start + timedelta(days=offset)
            for slot in slots:
                hour, minute = (int(part) for part in slot.split(":"))
                candidate = self.to_utc(datetime(day.year, day.month, day.day, hour, minute))
                if candidate <= after + timedelta(minutes=1):
                    continue
                if any(abs(candidate - other) < timedelta(minutes=1) for other in taken):
                    continue
                return candidate
        raise UserError("Нет свободного времени в ближайший год.")

    # ---------- the queue ----------

    def _mark_missed(self, now: datetime) -> None:
        missed = []
        with self.lock, self.sessions.begin() as session:
            for post in session.scalars(
                select(ThreadsPost).where(ThreadsPost.status == "scheduled")
            ):
                at = as_utc(post.scheduled_at)
                late = now - at
                if post.id in self.manual:
                    continue
                if (post.attempts == 0 and late > GRACE) or late > RETRY_HORIZON:
                    post.status = "missed"
                    post.next_attempt_at = None
                    post.error = (
                        "Время публикации прошло, пока программа была закрыта, компьютер "
                        "спал или очередь стояла на паузе."
                    )
                    missed.append(post.id)
        if missed:
            self._notify(
                "missed",
                "Пропущены публикации Threads",
                f"Не вышло в срок: {len(missed)}. Перенесите их на следующие слоты.",
            )

    def _due(self, session, now: datetime) -> list[ThreadsPost]:
        paused = self._paused(session)
        posts = session.scalars(
            select(ThreadsPost)
            .where(ThreadsPost.status == "scheduled", ThreadsPost.scheduled_at <= now)
            .order_by(ThreadsPost.scheduled_at, ThreadsPost.id)
        )
        due = []
        for post in posts:
            if post.next_attempt_at is not None and as_utc(post.next_attempt_at) > now:
                continue
            if paused and post.id not in self.manual:
                continue
            account = session.get(ThreadsAccount, post.account_id or 0)
            if account is None or account.status != "active":
                continue
            if account.paused and post.id not in self.manual:
                continue
            due.append(post)
        return due

    def _publish_due(self, now: datetime) -> bool:
        with self.lock, self.sessions.begin() as session:
            due = self._due(session, now)
            if not due:
                return False
            # The native shell opens saved browser profiles (including their proxy
            # relay). Waiting for it is not a failed publish attempt.
            post = next(
                (
                    post
                    for post in due
                    if (
                        (account := session.get(ThreadsAccount, post.account_id)).auth_mode
                        != "browser"
                        or self.browser
                        and self.browser.is_open(account.browser_profile_id)
                    )
                ),
                None,
            )
            if post is None:
                return False
            post.status = "publishing"
            post.attempts += 1
            post.next_attempt_at = None
            post_id = post.id
            more = len(due) > 1
        self._publish(post_id)
        return more

    def _load(self, post_id: int) -> tuple[ThreadsPost, ThreadsAccount]:
        with self.sessions() as session:
            post = session.get(ThreadsPost, post_id)
            account = session.get(ThreadsAccount, post.account_id)
            return post, account

    def _update(self, post_id: int, **values) -> None:
        with self.lock, self.sessions.begin() as session:
            post = session.get(ThreadsPost, post_id)
            for key, value in values.items():
                setattr(post, key, value)

    def _publish(self, post_id: int) -> None:
        post, account = self._load(post_id)
        try:
            client = self._client(account, post)
            container = post.container_id
            fresh = (
                post.container_at and self.now() - as_utc(post.container_at) < CONTAINER_LIFETIME
            )
            if not container or not fresh:
                container = client.create_text(account.threads_user_id, post.text)
                self._update(post_id, container_id=container, container_at=self.now())
            if self._wait_ready(client, container) == "PUBLISHED":
                # A kept container that came out after all: it is never published again.
                found = self._find(client, account, post)
                if found:
                    self._published_as(post_id, found)
                else:
                    self._published(post_id, client, None)
                return
            # From here on an unclear answer means «check before trying again».
            self._update(post_id, publish_sent_at=self.now())
        except ThreadsError as error:
            self._failed_before_publish(post_id, account.id, error)
            return
        try:
            media_id = client.publish(account.threads_user_id, container)
        except ThreadsError as error:
            if error.kind in ("auth", "limit"):
                # Refused before anything happened: nothing came out.
                self._update(post_id, publish_sent_at=None)
                self._failed_before_publish(post_id, account.id, error)
            else:
                self._update(
                    post_id,
                    next_attempt_at=self.now() + VERIFY_AFTER,
                    error="Ответ Threads не получен: проверяем, вышел ли пост.",
                )
            return
        self._published(post_id, client, media_id)

    def _wait_ready(self, client: ThreadsClient, container: str) -> str:
        """Threads asks to wait for the container; a text one is usually ready at once."""
        for _ in range(6):
            status = client.container(container).get("status")
            if status in ("FINISHED", None):
                return "FINISHED"
            if status == "PUBLISHED":
                return status
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsError("invalid", f"Threads не принял пост ({status}).")
            self.sleep(5)
        raise ThreadsError("server", "Threads долго готовит пост.")

    def _failed_before_publish(self, post_id: int, account_id: int, error: ThreadsError) -> None:
        post, _ = self._load(post_id)
        if error.kind == "auth":
            # Waits for the account; the attempt does not count.
            self._update(
                post_id,
                status="scheduled",
                attempts=max(0, post.attempts - 1),
                error="Ждёт переподключения аккаунта.",
            )
            self._needs_auth(account_id, str(error))
            return
        if error.kind == "invalid":
            self._fail(post_id, str(error))
            return
        if post.attempts >= MAX_ATTEMPTS:
            self._fail(post_id, f"{error} Попыток: {post.attempts}.")
            return
        delay = BACKOFF[min(post.attempts - 1, len(BACKOFF) - 1)]
        self._update(
            post_id,
            status="scheduled",
            next_attempt_at=self.now() + timedelta(seconds=delay),
            error=f"{error} Повтор через {delay // 60 or 1} мин.",
        )

    def _fail(self, post_id: int, message: str) -> None:
        self._update(post_id, status="failed", error=message[:500], next_attempt_at=None)
        self.manual.discard(post_id)
        self._notify("failed", "Пост Threads не опубликован", message[:200])

    def _published(self, post_id: int, client: ThreadsClient, media_id: str | None) -> None:
        link = None
        if media_id:
            try:
                link = client.media(media_id).get("permalink")
            except ThreadsError:
                pass  # the link is looked up again on the history page
        self._update(
            post_id,
            status="published",
            media_id=media_id,
            permalink=link,
            published_at=self.now(),
            error="",
            next_attempt_at=None,
        )
        self.manual.discard(post_id)

    def _verify(self, now: datetime) -> None:
        """Posts whose publish answer was lost: never published twice."""
        with self.sessions() as session:
            waiting = [
                post.id
                for post in session.scalars(
                    select(ThreadsPost).where(
                        ThreadsPost.status == "publishing",
                        ThreadsPost.publish_sent_at.is_not(None),
                    )
                )
                if post.next_attempt_at is None or as_utc(post.next_attempt_at) <= now
            ]
        for post_id in waiting:
            self._check(post_id, now)

    def _check(self, post_id: int, now: datetime) -> None:
        post, account = self._load(post_id)
        sent = as_utc(post.publish_sent_at)
        if now - sent < VERIFY_AFTER:
            self._update(post_id, next_attempt_at=sent + VERIFY_AFTER)
            return
        try:
            client = self._client(account, post)
            found = self._find(client, account, post)
            if found:
                self._published_as(post_id, found)
                return
            status = client.container(post.container_id).get("status")
        except ThreadsError as error:
            if error.kind == "auth":
                self._needs_auth(account.id, str(error))
            if now - sent > REVIEW_AFTER:
                self._update(
                    post_id,
                    status="review",
                    next_attempt_at=None,
                    error="Не удалось проверить, вышел ли пост. Посмотрите профиль и отметьте.",
                )
                self._notify("failed", "Проверьте пост Threads", post.text[:80])
            else:
                self._update(post_id, next_attempt_at=now + timedelta(minutes=5))
            return
        if status == "PUBLISHED":
            # Out, though not among the recent posts yet: the link is looked up later.
            self._published(post_id, client, None)
        elif status in ("FINISHED", "EXPIRED"):
            # Certainly not out: back to the queue (a new container if this one expired).
            values = {"status": "scheduled", "publish_sent_at": None, "next_attempt_at": now}
            if status == "EXPIRED":
                values.update(container_id=None, container_at=None)
            values["error"] = "Пост не вышел: повторяем публикацию."
            self._update(post_id, **values)
            if post.attempts >= MAX_ATTEMPTS:
                self._fail(post_id, "Пост не вышел после нескольких попыток.")
        elif status == "ERROR":
            self._fail(post_id, "Threads не принял пост.")
        elif now - sent > REVIEW_AFTER:
            # A successful API read can still be inconclusive. Do not leave the
            # post publishing forever, and never replay an uncertain submission.
            self._update(
                post_id,
                status="review",
                next_attempt_at=None,
                error="Threads не подтвердил результат. Проверьте, появился ли пост в профиле.",
            )
            self._notify("failed", "Проверьте пост Threads", post.text[:80])
        else:
            self._update(post_id, next_attempt_at=now + timedelta(minutes=1))

    def _find(self, client: ThreadsClient, account: ThreadsAccount, post: ThreadsPost):
        since = as_utc(post.container_at) - timedelta(minutes=2)
        wanted = post.text.strip()
        for item in client.recent(account.threads_user_id):
            if not any(
                str(value).strip() == wanted
                for value in [item.get("text") or "", *item.get("text_candidates", [])]
            ):
                continue
            stamp = item.get("timestamp")
            try:
                when = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
            except ValueError:
                when = None
            if account.auth_mode == "browser" and (when is None or when.tzinfo is None):
                continue  # An old identical post without a timestamp is not recovery proof.
            if when is None or when >= since:
                return item
        return None

    def _published_as(self, post_id: int, item: dict) -> None:
        self._update(
            post_id,
            status="published",
            media_id=str(item.get("id") or "") or None,
            permalink=item.get("permalink"),
            published_at=self.now(),
            error="",
            next_attempt_at=None,
        )
        self.manual.discard(post_id)

    # ---------- state ----------

    def _notify(self, kind: str, title: str, text: str) -> None:
        self.notices.append({"kind": kind, "title": title, "text": text, "at": iso(self.now())})

    def shell(self) -> dict:
        """For the tray and the system notifications."""
        with self.sessions() as session:
            paused = self._paused(session)
            upcoming = session.scalar(
                select(ThreadsPost.scheduled_at)
                .where(ThreadsPost.status == "scheduled")
                .order_by(ThreadsPost.scheduled_at)
                .limit(1)
            )
            browser_profiles = sorted(
                {
                    account.browser_profile_id
                    for post in self._due(session, self.now() + timedelta(minutes=1))
                    if (account := session.get(ThreadsAccount, post.account_id))
                    and account.auth_mode == "browser"
                    and account.browser_profile_id
                }
                | {
                    account.browser_profile_id
                    for account in session.scalars(
                        select(ThreadsAccount)
                        .join(ThreadsPost)
                        .where(
                            ThreadsPost.status == "publishing",
                            ThreadsAccount.auth_mode == "browser",
                            ThreadsAccount.status != "disconnected",
                        )
                    )
                    if account.browser_profile_id
                }
            )
        notices = list(self.notices)
        self.notices.clear()
        upcoming = as_utc(upcoming)
        return {
            "paused": paused,
            "next_at": iso(upcoming),
            "next_in": (upcoming - self.now()).total_seconds() if upcoming else None,
            "notices": notices,
            "browser_profiles": browser_profiles,
        }

    def open_count(self) -> int:
        """The sidebar badge: posts waiting in the queue or for the user."""
        with self.sessions() as session:
            return session.scalar(
                select(func.count()).select_from(ThreadsPost).where(ThreadsPost.status.in_(OPEN))
            )

    def _account_dict(self, account: ThreadsAccount, now: datetime) -> dict:
        expires = as_utc(account.token_expires_at)
        return {
            "id": account.id,
            "username": account.username,
            "name": account.name,
            "status": account.status,
            "status_label": ACCOUNT_LABELS[account.status],
            "reason": account.reason,
            "paused": account.paused,
            "slots": account.slots or [],
            "expires_at": iso(expires),
            "expiring": bool(expires and expires - now < EXPIRY_WARNING),
            "connected_at": iso(account.connected_at),
            "auth_mode": account.auth_mode,
            "browser_profile_id": account.browser_profile_id,
        }

    def _post_dict(self, post: ThreadsPost) -> dict:
        return {
            "id": post.id,
            "account_id": post.account_id,
            "text": post.text,
            "status": post.status,
            "status_label": STATUS_LABELS[post.status],
            "scheduled_at": iso(post.scheduled_at),
            "slot": post.slot,
            "attempts": post.attempts,
            "next_attempt_at": iso(post.next_attempt_at),
            "permalink": post.permalink,
            "published_at": iso(post.published_at),
            "error": post.error,
            "updated_at": iso(post.updated_at),
            "manual": post.id in self.manual,
        }

    def state(self) -> dict:
        self._capture_browser()
        now = self.now()
        with self.sessions() as session:
            app = self._app(session)
            accounts = [
                self._account_dict(account, now)
                for account in session.scalars(select(ThreadsAccount).order_by(ThreadsAccount.id))
            ]
            posts = [
                self._post_dict(post)
                for post in session.scalars(
                    select(ThreadsPost).order_by(ThreadsPost.updated_at.desc()).limit(500)
                )
            ]
            paused = self._paused(session)
        auth = self.auth
        if auth and auth["status"] == "waiting" and now > auth["until"]:
            auth = {**auth, "status": "error", "message": "Время авторизации вышло."}
        local = self.to_local(now)
        return {
            "app": {
                "app_id": app.get("app_id", ""),
                "has_secret": bool(app.get("secret")),
                "redirect_uri": app.get("redirect_uri", DEFAULT_REDIRECT),
            },
            "auth": {k: v for k, v in auth.items() if k in ("status", "message", "listening")}
            if auth
            else None,
            "accounts": accounts,
            "browser_profiles": self.browser_profiles(),
            "browser_auth": self.browser_auth,
            "posts": posts,
            "paused": paused,
            "zone": {
                "name": local.tzname(),
                "offset_minutes": int(local.utcoffset().total_seconds() // 60),
            },
            "today": date.isoformat(local.date()),
        }
