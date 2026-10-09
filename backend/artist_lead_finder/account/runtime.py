"""The app behind the sign-in.

Every account has its own folder (`accounts/<user id>/`: database, Instagram sessions,
attachments), opened after sign-in and closed at sign-out, so two people on one computer
never see each other's data. The data of the version before accounts goes to the first
account that signs in here. Until the account is ready only `account.*`, `system.info`
and the Chromium self-test answer.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from ..crm.sync import CrmSync
from .admin import AdminService
from .session import AccountService

log = logging.getLogger(__name__)

# What a pre-accounts install kept in the data folder for its single user.
LEGACY_ITEMS = (
    "artist-leads.sqlite3",
    "artist-leads.sqlite3-wal",
    "artist-leads.sqlite3-shm",
    "browser-sessions",
    "imessage-attachments",
    "openrouter-key.vault",
    "imported-profiles.json",
    "scout-debug",
)
TICK_SECONDS = 30
# CRM calls that change contacts or statuses: the sync sends them right away.
CRM_WRITES = {
    "crm.save",
    "crm.trash",
    "crm.restore",
    "crm.purge",
    "crm.label",
    "crm.statuses_save",
    "crm.import_verse",
    "crm.import_file",
    # Phones and emails from Direct go to the iMessage CRM.
    "inbox.add_to_crm",
}


class Runtime:
    def __init__(
        self,
        data_dir: Path,
        account: AccountService,
        open_service: Callable[[Path], tuple[object, Callable[[], None]]],
        info: Callable[[], dict],
        probe: Callable[[], object] | None = None,
    ):
        self.data_dir = data_dir
        self.account = account
        self.open_service = open_service
        self.info = info
        self.probe_factory = probe
        self.probe = None
        self.service = None
        self.close_service: Callable[[], None] | None = None
        self.opened_for: str | None = None
        self.crm_sync: CrmSync | None = None
        self.admin = AdminService(account)
        self.locked = False
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.ticker: threading.Thread | None = None

    # ---------- lifecycle ----------

    def start(self) -> None:
        if not self.account.configured:
            self._open(self.data_dir, None)
            return
        self.account.restore()
        self.sync()
        self.ticker = threading.Thread(target=self._tick, daemon=True)
        self.ticker.start()

    def _tick(self) -> None:
        while not self.stopping.wait(TICK_SECONDS):
            try:
                self.account.check()
                self.sync()
            except Exception:  # noqa: BLE001 - the next tick tries again
                log.warning("account_tick_failed")

    def shutdown(self) -> None:
        self.stopping.set()
        with self.lock:
            self._close()
            if self.probe is not None:
                self.probe.shutdown()
                self.probe = None

    def account_dir(self, user_id: str) -> Path:
        root = self.data_dir / "accounts"
        target = root / user_id
        if target.exists():
            return target
        target.mkdir(parents=True)
        claimed = root / ".legacy-claimed"
        if not claimed.exists() and (self.data_dir / "artist-leads.sqlite3").exists():
            for name in LEGACY_ITEMS:
                source = self.data_dir / name
                if source.exists():
                    source.replace(target / name)
            claimed.write_text(user_id, encoding="utf-8")
            log.info("account_legacy_data_claimed")
        return target

    def _open(self, folder: Path, user_id: str | None) -> None:
        self.service, self.close_service = self.open_service(folder)
        self.opened_for = user_id
        self.locked = False
        crm = getattr(self.service, "crm", None)
        if user_id and crm is not None:
            crm.viewer = lambda: (self.account.user_id, self.account.sees_all_crm)
            self.crm_sync = CrmSync(self.service.sessions, self.account)
            self.crm_sync.start()
            self.crm_sync.trigger()

    def _close(self) -> None:
        if self.crm_sync is not None:
            self.crm_sync.stop()
            self.crm_sync = None
        if self.close_service is not None:
            try:
                self.close_service()
            finally:
                self.service = self.close_service = self.opened_for = None

    def sync(self) -> None:
        """Opens the signed-in account's data, closes it at sign-out, and stops the
        iPhone bridge while the app is locked."""
        if not self.account.configured:
            return
        with self.lock:
            user_id = self.account.user_id
            if not self.account.signed_in:
                self._close()
                return
            if self.account.ready and user_id and self.opened_for != user_id:
                self._close()
                self._open(self.account_dir(user_id), user_id)
            if self.service is None:
                return
            imessage = getattr(self.service, "imessage", None)
            if self.account.ready and self.locked:
                self.locked = False
                if imessage is not None:
                    imessage.restore()
            elif not self.account.ready and not self.locked:
                self.locked = True
                if imessage is not None:
                    imessage.shutdown()

    # ---------- requests ----------

    def call(self, method: str, params: dict):
        if method == "account.state":
            state = self.account.state()
            self.sync()
            return {**state, "ready": self.account.ready}
        if method in ("account.login", "account.register", "account.activate", "account.logout"):
            action = getattr(self.account, method.removeprefix("account."))
            state = action(params)
            self.sync()
            return state
        if method.startswith("admin."):
            with self.lock:
                self.account.check()
                self.sync()
                self.account.require_ready()
                result = self.admin.call(method, params)
            # Roles and blocks changed on the server: the next check picks them up.
            self.account.checked_at = None
            return result
        if method == "system.info":
            return {**self.info(), "account": self.account.state()}
        if method == "browser.runtime.self_test" and self.service is None:
            # The packaged core is checked before anyone signs in.
            if self.probe is None and self.probe_factory is not None:
                self.probe = self.probe_factory()
            return self.probe.self_test()
        with self.lock:
            if self.account.configured:
                self.account.check()
                self.sync()
                self.account.require_ready()
            result = self.service.call(method, params)
            if method in CRM_WRITES and self.crm_sync is not None:
                self.crm_sync.trigger()
            return result
