"""The signed-in account of this computer: sign-in, access key, status checks.

The refresh token is kept encrypted for the OS user (secret_box); the short-lived access
token only in memory. Without the server the app does not work: two failed checks in a
row lock it until the server answers again.
"""

import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .. import secret_box
from ..errors import UserError
from .client import AuthError, OfflineError, SupabaseClient

log = logging.getLogger(__name__)

CHECK_SECONDS = 300
RETRY_SECONDS = 30
FAILURES_TO_LOCK = 2
REFRESH_MARGIN = 60
EMAIL_MAX = 254
NAME_MAX = 80
PASSWORD_MIN = 8


class AccountService:
    def __init__(
        self,
        data_dir: Path,
        client: SupabaseClient | None,
        clock: Callable[[], float] = time.monotonic,
        protect: Callable[..., bytes] = secret_box.protect,
    ):
        self.client = client
        self.clock = clock
        self.protect = protect
        self.info_path = data_dir / "account.json"
        self.token_path = data_dir / "account.session"
        self.lock = threading.RLock()
        self.access: str | None = None
        self.access_until = 0.0
        self.refresh_token: str | None = None
        self.status: dict | None = None
        self.online = True
        self.failures = 0
        self.checked_at: float | None = None
        self.reason: str | None = None
        info = self._read_info()
        self.device_id = info.get("device_id") or secrets.token_hex(16)
        self.user: dict | None = info.get("user")
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
        payload = {"device_id": self.device_id, "user": self.user}
        temporary = self.info_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.info_path)

    def _save_refresh(self) -> None:
        if not self.refresh_token:
            self.token_path.unlink(missing_ok=True)
            return
        try:
            self.token_path.write_bytes(self.protect(self.refresh_token.encode()))
        except (ValueError, OSError):
            # Without an OS secret store the session lasts until the app closes.
            log.warning("account_session_not_saved")

    def _load_refresh(self) -> str | None:
        try:
            return self.protect(self.token_path.read_bytes(), decrypt=True).decode()
        except (OSError, ValueError, UnicodeDecodeError):
            return None

    # ---------- state ----------

    @property
    def signed_in(self) -> bool:
        return bool(self.refresh_token)

    @property
    def licensed(self) -> bool:
        return bool(self.status and self.status.get("licensed"))

    @property
    def ready(self) -> bool:
        """The app may work: no accounts configured, or a signed-in, licensed, active
        account whose server answers."""
        if not self.configured:
            return True
        return (
            self.signed_in
            and self.online
            and self.status is not None
            and self.licensed
            and not self.status.get("blocked")
        )

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
            if self.configured and self.signed_in and not self.online:
                # The lock screen polls this: retry the server now and then.
                self.check()
            return {
                "configured": self.configured,
                "signed_in": self.signed_in,
                "licensed": self.licensed,
                "online": self.online,
                "ready": self.ready,
                "user": self.user,
                "role": (self.status or {}).get("role") or (self.user or {}).get("role"),
                "reason": self.reason,
            }

    # ---------- actions ----------

    def _take(self, tokens: dict) -> None:
        self.access = tokens.get("access_token")
        self.refresh_token = tokens.get("refresh_token") or self.refresh_token
        self.access_until = self.clock() + max(0, int(tokens.get("expires_in") or 0))
        if not self.access or not self.refresh_token:
            raise UserError("Сервер не выдал сессию. Повторите вход.")
        self._save_refresh()

    def _apply_status(self, status) -> None:
        if not isinstance(status, dict) or not status.get("user_id"):
            self._sign_out_locally("Аккаунт не найден.")
            raise AuthError("Аккаунт не найден.")
        if status.get("blocked"):
            self._sign_out_locally("Аккаунт заблокирован. Обратитесь к админу.")
            raise AuthError("Аккаунт заблокирован. Обратитесь к админу.")
        self.status = status
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
        self._write_info()

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
        with self.lock:
            status = self.client.rpc(
                "activate_key", {"p_key": key, "p_device": self.device_id}, self.token()
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
        self.online, self.failures = True, 0
        self.reason = reason
        self._save_refresh()

    def token(self) -> str:
        """A valid access token, refreshed shortly before it expires."""
        with self.lock:
            if not self.refresh_token:
                raise AuthError("Войдите в аккаунт.")
            if not self.access or self.clock() >= self.access_until - REFRESH_MARGIN:
                try:
                    self._take(self.client.refresh(self.refresh_token))
                except AuthError:
                    self._sign_out_locally("Сессия истекла. Войдите снова.")
                    raise
            return self.access

    def restore(self) -> None:
        """At start: the saved session comes back after a status check."""
        if not self.configured:
            return
        with self.lock:
            self.refresh_token = self._load_refresh()
            if self.refresh_token:
                self.check(force=True)

    def check(self, force: bool = False) -> None:
        """Status check: every few minutes while online, more often while locked."""
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
            try:
                status = self.client.rpc(
                    "session_status", {"p_device": self.device_id}, self.token()
                )
                self._apply_status(status)
            except OfflineError:
                self.failures += 1
                if self.failures >= FAILURES_TO_LOCK or self.status is None:
                    self.online = False
                    self.reason = (
                        "Нет связи с сервером. Приложение продолжит работу, когда связь появится."
                    )
                log.warning("account_check_offline")
            except AuthError:
                log.warning("account_check_refused")

    def require_ready(self) -> None:
        if self.ready:
            return
        if not self.signed_in:
            raise UserError("Войдите в аккаунт.")
        if not self.online:
            raise UserError("Нет связи с сервером. Работа продолжится, когда связь появится.")
        raise UserError(self.reason or "Введите ключ доступа.")
