"""A bounded cookie-browsing session in an existing NSTbrowser profile."""

import logging
import threading
import time
from copy import deepcopy
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import UserError
from .models import Setting

KEY = "cookie_warmup"
ACTIVE = ("running", "stopping")
log = logging.getLogger(__name__)


class CookieWarmupSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(default=15, ge=1, le=120)
    sites: list[str] = Field(
        default_factory=lambda: [
            "https://www.amazon.com/",
            "https://www.reddit.com/",
            "https://www.yahoo.com/",
            "https://www.bing.com/",
            "https://chatgpt.com/",
            "https://www.wikipedia.org/",
            "https://www.linkedin.com/",
            "https://www.walmart.com/",
            "https://www.ebay.com/",
        ],
        min_length=1,
        max_length=30,
    )

    @field_validator("sites")
    @classmethod
    def valid_sites(cls, values):
        sites = []
        for value in values:
            value = value.strip()
            try:
                address = urlsplit(value)
                valid = (
                    len(value) <= 2048
                    and address.scheme in ("http", "https")
                    and address.hostname
                    and address.username is None
                    and address.password is None
                    and (address.port is None or 1 <= address.port <= 65535)
                )
            except ValueError:
                valid = False
            if not valid:
                raise UserError("Укажите адреса сайтов с http:// или https:// без логина и пароля.")
            if address.hostname.removeprefix("www.") == "google.com":
                continue
            if value not in sites:
                sites.append(value)
        if not sites:
            raise UserError("Добавьте сайт помимо Google, который используется для поиска.")
        return sites


def active_profile(session):
    row = session.get(Setting, KEY)
    run = (row.value or {}).get("run") if row else None
    return run["profile_id"] if run and run["status"] in ACTIVE else None


class CookieWarmup:
    def __init__(self, sessions, profiles):
        self.sessions, self.profiles = sessions, profiles
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        value = self._load()
        if value.get("run", {}).get("status") in ACTIVE:
            value["run"]["status"] = "interrupted"
            self._event(value["run"], "Запуск прерван перезапуском приложения")
            self._save(value)

    def _load(self):
        with self.sessions() as session:
            row = session.get(Setting, KEY)
            return deepcopy(row.value) if row else {"settings": CookieWarmupSettings().model_dump()}

    def _save(self, value):
        with self.sessions.begin() as session:
            session.merge(Setting(key=KEY, value=deepcopy(value)))

    @staticmethod
    def _event(run, text):
        run["events"].append({"at": time.time(), "text": text})
        run["events"] = run["events"][-150:]

    def state(self, params=None):
        with self.lock:
            value = self._load()
        value["profiles"] = [
            row
            for row in self.profiles.call("browser.list", {})
            if row.get("external", {}).get("provider") == "nst"
        ]
        return value

    def start(self, params):
        from .browser_idle import window_work

        settings = CookieWarmupSettings.model_validate(params.get("settings") or {})
        identifier = str(params.get("profile_id") or "")
        with self.lock:
            value = self._load()
            if self.thread and self.thread.is_alive():
                raise UserError("Прогрев cookies уже работает. Дождитесь его остановки.")
            profiles = {row["id"]: row for row in self.state()["profiles"]}
            if identifier not in profiles:
                raise UserError("Выберите подключённый профиль NSTbrowser.")
            with self.sessions() as session:
                if window_work(session, identifier)[0]:
                    raise UserError("Профиль занят. Завершите другие задачи этого аккаунта.")
            connection = self.profiles.read(identifier)["external"]
            value["settings"] = settings.model_dump()
            value["run"] = {
                "profile_id": identifier,
                "profile_name": profiles[identifier]["name"],
                "status": "running",
                "started_at": time.time(),
                "completed_at": None,
                "visited": 0,
                "current": "",
                "events": [],
            }
            self._event(value["run"], "Прогрев cookies запущен")
            self._save(value)
            self.stopping.clear()
            self.thread = threading.Thread(
                target=self._run, args=(connection, settings), name="cookie-warmup", daemon=True
            )
            self.thread.start()
        return self.state()

    def stop(self, params=None):
        with self.lock:
            value = self._load()
            if value.get("run", {}).get("status") == "running":
                self.stopping.set()
                value["run"]["status"] = "stopping"
                self._event(value["run"], "Останавливаем прогрев")
                self._save(value)
        return self.state()

    def _report(self, text, *, current=None, visited=False):
        with self.lock:
            value = self._load()
            run = value["run"]
            self._event(run, text)
            if current is not None:
                run["current"] = current
            run["visited"] += int(visited)
            self._save(value)

    def _run(self, connection, settings):
        from .cookie_warmup_browser import browse

        status, message = "done", "Прогрев cookies завершён"
        try:
            browse(connection, settings, self.stopping, self._report)
        except InterruptedError:
            status, message = "stopped", "Прогрев cookies остановлен"
        except UserError as error:
            status, message = "failed", str(error)
        except Exception as error:
            log.warning("cookie_warmup_failed", extra={"error_type": type(error).__name__})
            status = "failed"
            message = "Просмотр прервался. Проверьте открытую вкладку и подключение NSTbrowser."
        finally:
            with self.lock:
                value = self._load()
                value["run"]["status"] = "stopped" if self.stopping.is_set() else status
                value["run"]["completed_at"] = time.time()
                self._event(value["run"], message)
                self._save(value)

    def shutdown(self):
        self.stop()
        if self.thread:
            self.thread.join(timeout=45)
