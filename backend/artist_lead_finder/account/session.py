"""The signed-in account of this computer: sign-in, access key, status checks.

The refresh token is kept encrypted for the OS user (secret_box); the short-lived access
token only in memory. Status checks run in the background (account/runtime.py ticks),
never on a request: a server that hangs cannot slow the interface down. Two failed checks
in a row mean the server is unreachable: the app then keeps working on its offline pass
(account/offline.py) until the pass expires, and locks without one. A server that answers
always decides.
"""

import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .. import secret_box
from ..errors import UserError
from . import offline
from .client import AuthError, OfflineError, SupabaseClient

log = logging.getLogger(__name__)

CHECK_SECONDS = 300
RETRY_SECONDS = 30
FAILURES_TO_LOCK = 2
REFRESH_MARGIN = 60
EMAIL_MAX = 254
NAME_MAX = 80
PASSWORD_MIN = 8
# The offline pass is renewed this long after it was issued, and asked for again this
# long after a failed attempt (an older server without the function).
PASS_RENEW_SECONDS = 6 * 3600
PASS_RETRY_SECONDS = 3600


class AccountService:
    def __init__(
        self,
        data_dir: Path,
        client: SupabaseClient | None,
        clock: Callable[[], float] = time.monotonic,
        protect: Callable[..., bytes] = secret_box.protect,
        offline_key: str | None = None,
        wall: Callable[[], float] = time.time,
    ):
        self.client = client
        self.clock = clock
        self.wall = wall
        self.protect = protect
        self.offline_key = offline.public_key(offline_key or "")
        self.info_path = data_dir / "account.json"
        self.token_path = data_dir / "account.session"
        self.pass_path = data_dir / "account.offline"
        self.lock = threading.RLock()
        # One status check at a time, and one refresh of the session: a refresh token is
        # used once, a second concurrent refresh with it would end the session.
        self.checking = threading.Lock()
        self.refreshing = threading.Lock()
        # Wakes the background tick: a check is due now (the lock screen's «Повторить»).
        self.wake = threading.Event()
        self.retry = False
        self.access: str | None = None
        self.access_until = 0.0
        self.refresh_token: str | None = None
        self.status: dict | None = None
        # The status was taken from the offline pass at start and the server has not
        # confirmed it yet.
        self.provisional = False
        self.online = True
        self.failures = 0
        self.checked_at: float | None = None
        self.reason: str | None = None
        self.pass_token: str | None = None
        self.pass_tried_at: float | None = None
        info = self._read_info()
        self.device_id = info.get("device_id") or secrets.token_hex(16)
        self.user: dict | None = info.get("user")
        # The latest wall time the app has seen: a clock set back cannot stretch a pass.
        self.seen_at = float(info.get("seen_at") or 0)
        if not info.get("device_id"):
            self._write_info()

    @property
    def configured(self) -> bool:
        return self.client is not None

    # ---------- storage ----------

    def _read_info(self) -> dict:
        try:
            data = json.loads(self.info_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_info(self) -> None:
        self.info_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"device_id": self.device_id, "user": self.user, "seen_at": self.seen_at}
        temporary = self.info_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.info_path)

    def _save_secret(self, path: Path, value: str | None) -> None:
        if not value:
            path.unlink(missing_ok=True)
            return
        try:
            path.write_bytes(self.protect(value.encode()))
        except (ValueError, OSError):
            # Without an OS secret store the value lasts until the app closes.
            log.warning("account_secret_not_saved")

    def _load_secret(self, path: Path) -> str | None:
        try:
            return self.protect(path.read_bytes(), decrypt=True).decode()
        except (OSError, ValueError, UnicodeDecodeError):
            return None

    def _save_refresh(self) -> None:
        self._save_secret(self.token_path, self.refresh_token)

    def _load_refresh(self) -> str | None:
        return self._load_secret(self.token_path)

    # ---------- offline pass ----------

    def _now(self) -> float:
        """Wall time, moved forward only: the high-water mark is kept on disk."""
        now = self.wall()
        if now > self.seen_at + 60:
            self.seen_at = now
            self._write_info()
        return now

    def _pass(self) -> offline.OfflinePass | None:
        """The stored pass when it is valid for this account, device and time."""
        if self.offline_key is None or not self.signed_in:
            return None
        if self.pass_token is None:
            self.pass_token = self._load_secret(self.pass_path)
        now = self._now()
        if now < self.seen_at - offline.CLOCK_SKEW:
            return None
        return offline.verify(self.pass_token, self.offline_key, self.user_id, self.device_id, now)

    def _drop_pass(self) -> None:
        self.pass_token = None
        self.pass_tried_at = None
        self.pass_path.unlink(missing_ok=True)

    def _renew_pass(self) -> None:
        """A new pass from the server while it answers; failures change nothing."""
        with self.lock:
            if self.offline_key is None or not self.access:
                return
            current = self._pass()
            if current is not None and self._now() - current.issued_at < PASS_RENEW_SECONDS:
                return
            if (
                self.pass_tried_at is not None
                and self.clock() - self.pass_tried_at < PASS_RETRY_SECONDS
            ):
                return
            self.pass_tried_at = self.clock()
            access, user_id = self.access, self.user_id
        try:
            answer = self.client.function("offline-token", {"device": self.device_id}, access)
        except UserError:
            log.warning("account_offline_pass_unavailable")
            return
        token = (answer or {}).get("token") if isinstance(answer, dict) else None
        with self.lock:
            if self.signed_in and self.user_id == user_id:
                self._store_pass(token)

    def _store_pass(self, token) -> None:
        if offline.verify(token, self.offline_key, self.user_id, self.device_id, self._now()):
            self.pass_token = token
            self.pass_tried_at = None
            self._save_secret(self.pass_path, token)
            log.info("account_offline_pass_renewed")
        else:
            log.warning("account_offline_pass_rejected")

    @property
    def offline_until(self) -> float | None:
        """Wall time until which the app works without the server; None while online."""
        if self.online:
            return None
        found = self._pass()
        return float(found.expires_at) if found else None

    def _offline_reason(self) -> str:
        until = self.offline_until
        if until is None:
            return "Нет связи с сервером. Приложение продолжит работу, когда связь появится."
        return (
            "Нет связи с сервером. Работа без сервера доступна до "
            f"{datetime.fromtimestamp(until):%d.%m %H:%M}."
        )

    # ---------- state ----------

    @property
    def signed_in(self) -> bool:
        return bool(self.refresh_token)

    @property
    def licensed(self) -> bool:
        return bool(self.status and self.status.get("licensed"))

    @property
    def ready(self) -> bool:
        """The app may work: no accounts configured; a signed-in, licensed, active account
        whose server answers; or, while the server is unreachable, a valid offline pass."""
        if not self.configured:
            return True
        if not self.signed_in:
            return False
        if not self.online:
            return self.offline_until is not None
        return self.status is not None and self.licensed and not self.status.get("blocked")

    @property
    def checking_first(self) -> bool:
        """Signed in, and the first status check since the start has not come back yet."""
        return self.configured and self.signed_in and self.online and self.status is None

    @property
    def user_id(self) -> str | None:
        return (self.user or {}).get("id")

    @property
    def is_admin(self) -> bool:
        return bool(self.status and self.status.get("role") == "admin")

    @property
    def sees_all_crm(self) -> bool:
        """Admin and moderator see and change every user's CRM."""
        return bool(self.status and self.status.get("role") in ("admin", "moderator"))

    def state(self) -> dict:
        with self.lock:
            if self.configured and self.signed_in and (not self.online or self.checking_first):
                # The lock screen polls this: the background tick retries the server.
                self.wake.set()
            until = self.offline_until
            return {
                "configured": self.configured,
                "signed_in": self.signed_in,
                "licensed": self.licensed,
                "online": self.online,
                "ready": self.ready,
                "checking": self.checking_first,
                "user": self.user,
                "role": (self.status or {}).get("role") or (self.user or {}).get("role"),
                "reason": self._offline_reason() if not self.online else self.reason,
                "offline_until": (
                    datetime.fromtimestamp(until).astimezone().isoformat() if until else None
                ),
            }

    # ---------- actions ----------

    def _take(self, tokens: dict) -> None:
        self.access = tokens.get("access_token")
        self.refresh_token = tokens.get("refresh_token") or self.refresh_token
        self.access_until = self.clock() + max(0, int(tokens.get("expires_in") or 0))
        if not self.access or not self.refresh_token:
            raise UserError("Сервер не выдал сессию. Повторите вход.")
        self._save_refresh()

    def _apply_status(self, status, renew: bool = True) -> None:
        if not isinstance(status, dict) or not status.get("user_id"):
            self._sign_out_locally("Аккаунт не найден.")
            raise AuthError("Аккаунт не найден.")
        if status.get("blocked"):
            self._sign_out_locally("Аккаунт заблокирован. Обратитесь к админу.")
            raise AuthError("Аккаунт заблокирован. Обратитесь к админу.")
        if self.user_id and self.user_id != status["user_id"]:
            self._drop_pass()
        self.status = status
        self.provisional = False
        self.user = {
            "id": status["user_id"],
            "email": status.get("email") or "",
            "display_name": status.get("display_name") or "",
            "role": status.get("role") or "user",
        }
        self.online, self.failures = True, 0
        self.checked_at = self.clock()
        self.reason = (
            None if status.get("licensed") else "Введите ключ доступа, чтобы открыть аккаунт."
        )
        self._now()
        self._write_info()
        if status.get("licensed"):
            if renew:
                self._renew_pass()
        else:
            # The server says no access: nothing may keep the app open offline.
            self._drop_pass()

    def _credentials(self, params: dict) -> tuple[str, str]:
        email = str(params.get("email") or "").strip().lower()
        password = str(params.get("password") or "")
        if not self.configured:
            raise UserError("Вход не настроен в этой сборке.")
        if not email or len(email) > EMAIL_MAX or "@" not in email:
            raise UserError("Введите email.")
        if not password:
            raise UserError("Введите пароль.")
        return email, password

    def _open_session(self, tokens: dict) -> None:
        self._take(tokens)
        status = self.client.rpc("session_status", {"p_device": self.device_id}, self.access)
        self._apply_status(status)

    def login(self, params: dict) -> dict:
        email, password = self._credentials(params)
        with self.lock:
            self._open_session(self.client.sign_in(email, password))
            log.info("account_signed_in")
        return self.state()

    def register(self, params: dict) -> dict:
        """A new account signs in at once; it works after a key is activated."""
        email, password = self._credentials(params)
        name = " ".join(str(params.get("name") or "").split())
        if not name:
            raise UserError("Введите имя.")
        if len(name) > NAME_MAX:
            raise UserError(f"Имя длиннее {NAME_MAX} символов.")
        if len(password) < PASSWORD_MIN:
            raise UserError(f"Пароль короче {PASSWORD_MIN} символов.")
        with self.lock:
            tokens = self.client.sign_up(email, password, name)
            if not (tokens or {}).get("access_token"):
                raise UserError("Аккаунт создан, но сервер требует подтверждения email.")
            self._open_session(tokens)
            log.info("account_registered")
        return self.state()

    def activate(self, params: dict) -> dict:
        key = str(params.get("key") or "").strip()
        if not 8 <= len(key) <= 64:
            raise UserError("Введите ключ доступа.")
        token = self.token()
        with self.lock:
            status = self.client.rpc(
                "activate_key", {"p_key": key, "p_device": self.device_id}, token
            )
            self._apply_status(status)
            log.info("account_key_activated")
        return self.state()

    def logout(self, params: dict | None = None) -> dict:
        with self.lock:
            if self.access and self.client:
                try:
                    self.client.sign_out(self.access)
                except UserError:
                    pass
            self._sign_out_locally(None)
            log.info("account_signed_out")
        return self.state()

    def _sign_out_locally(self, reason: str | None) -> None:
        self.access = self.refresh_token = None
        self.access_until = 0.0
        self.status = None
        self.provisional = False
        self.online, self.failures = True, 0
        self.reason = reason
        self._save_refresh()
        self._drop_pass()

    def token(self) -> str:
        """A valid access token, refreshed shortly before it expires.

        The refresh goes to the server outside the state lock, so the interface keeps
        answering while it waits; never call this holding `self.lock`.
        """
        with self.refreshing:
            with self.lock:
                if not self.refresh_token:
                    raise AuthError("Войдите в аккаунт.")
                if self.access and self.clock() < self.access_until - REFRESH_MARGIN:
                    return self.access
                current = self.refresh_token
            try:
                tokens = self.client.refresh(current)
            except AuthError:
                with self.lock:
                    if self.refresh_token == current:
                        self._sign_out_locally("Сессия истекла. Войдите снова.")
                raise
            with self.lock:
                if self.refresh_token != current:
                    raise AuthError("Войдите в аккаунт.")
                self._take(tokens)
                return self.access

    def restore(self, check: bool = True) -> None:
        """At start: the saved session comes back. A valid offline pass stands for the
        status until the server answers, so the app opens at once; without one the first
        check decides (here with `check`, or on the background tick)."""
        if not self.configured:
            return
        with self.lock:
            self.refresh_token = self._load_refresh()
            if not self.refresh_token:
                return
            found = self._pass()
            if found is not None:
                self.status, self.provisional = found.status(), True
        if check:
            self.check(force=True)

    def request_check(self) -> None:
        """The lock screen's «Повторить»: the background tick checks now."""
        self.retry = True
        self.wake.set()

    def take_retry(self) -> bool:
        retry, self.retry = self.retry, False
        return retry

    def check(self, force: bool = False) -> None:
        """One status check, run by the background tick: every few minutes while online,
        more often while locked or offline. Requests never call it, and nothing waits for
        the server under the state lock. A server in doubt is first probed quickly."""
        if not self.checking.acquire(blocking=False):
            return
        try:
            with self.lock:
                if not self.configured or not self.signed_in:
                    return
                interval = CHECK_SECONDS if self.online and not self.failures else RETRY_SECONDS
                if (
                    not force
                    and self.checked_at is not None
                    and self.clock() - self.checked_at < interval
                ):
                    return
                self.checked_at = self.clock()
                doubtful = self.doubtful
            if doubtful and not self.client.reachable():
                self._note_offline()
                return
            try:
                status = self.client.rpc(
                    "session_status", {"p_device": self.device_id}, self.token()
                )
            except OfflineError:
                self._note_offline()
                return
            except AuthError:
                log.warning("account_check_refused")
                return
            with self.lock:
                if not self.signed_in:
                    return
                try:
                    self._apply_status(status, renew=False)
                except AuthError:
                    log.warning("account_check_refused")
                    return
            self._renew_pass()
        finally:
            self.checking.release()

    @property
    def doubtful(self) -> bool:
        """The server is not known to answer right now: down, a check failed, or the first
        check since the start is still out. Calls to it then fail at once instead of
        holding every other request of the interface for a network timeout."""
        return not self.online or self.failures > 0 or self.provisional or self.status is None

    def note_offline(self) -> None:
        """A failed check, or a call to the server that could not reach it."""
        self.wake.set()
        self._note_offline()

    def _note_offline(self) -> None:
        with self.lock:
            self.failures += 1
            if self.failures >= FAILURES_TO_LOCK or self.status is None or self.provisional:
                self.online = False
                if self.status is None and (found := self._pass()) is not None:
                    # Started without the server: the pass stands for the last status.
                    self.status = found.status()
                self.reason = self._offline_reason()
        log.warning("account_check_offline")

    def require_ready(self) -> None:
        if self.ready:
            return
        if not self.signed_in:
            raise UserError("Войдите в аккаунт.")
        if self.checking_first:
            raise UserError("Проверяем аккаунт, подождите пару секунд.")
        if not self.online:
            raise UserError(
                "Нет связи с сервером, а срок работы без сервера закончился. "
                "Работа продолжится, когда связь появится."
            )
        raise UserError(self.reason or "Введите ключ доступа.")
