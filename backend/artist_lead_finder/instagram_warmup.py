"""A bounded, persistent Instagram session, advanced one step by the desktop driver."""

import hashlib
import logging
import re
import time
import traceback
import uuid
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from .errors import UserError
from .models import Lead, Setting

KEY = "instagram_warmup"
HISTORY_PREFIX = "ig_warmup_run:"
log = logging.getLogger(__name__)


def clear_history(session):
    """Hide completed warm-ups; keep active runs and duplicate-send protection."""
    for row in session.scalars(
        select(Setting).where(Setting.key.startswith(HISTORY_PREFIX, autoescape=True))
    ):
        if row.value.get("status") != "running" and not row.value.get("hidden"):
            row.value = {**row.value, "hidden": True}


class WarmupSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(
        default_factory=lambda: [
            "магазин одежды",
            "магазин косметики",
            "магазин подарков",
            "магазин украшений",
        ],
        min_length=1,
        max_length=10,
    )
    message: str = Field(default="", max_length=1000)
    messages: int = Field(default=6, ge=0, le=6)
    likes: int = Field(default=3, ge=0, le=5)
    follows: int = Field(default=5, ge=0, le=5)
    delay_seconds: int = Field(default=20, ge=10, le=120)

    @field_validator("queries")
    @classmethod
    def valid_queries(cls, values):
        result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not result or any(len(value) > 100 for value in result):
            raise UserError("Задайте от 1 до 10 поисковых фраз длиной до 100 символов.")
        return result


def active_profile(session) -> str | None:
    row = session.get(Setting, KEY)
    run = (row.value or {}).get("run") if row else None
    return run.get("profile_id") if run and run.get("status") == "running" else None


def warming_profiles(session) -> set[str]:
    from .cookie_warmup import active_profile as cookie_profile

    return {
        identifier
        for identifier in (active_profile(session), cookie_profile(session))
        if identifier
    }


def russian_shop(profile: dict) -> str:
    """Return an exclusion reason; unknown counts never qualify as small shops."""
    count = profile.get("followers_count")
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 10_000:
        return "Число подписчиков неизвестно или больше 10 000"
    if profile.get("is_private") is not False:
        return "Закрытый профиль или доступ не подтверждён"
    text = " ".join(
        str(profile.get(key) or "") for key in ("full_name", "biography", "category_name")
    ).lower()
    if not re.search(r"[а-яё]{3,}", text):
        return "Нет признаков русского языка в описании"
    if not re.search(r"магазин|доставк|заказ|бутик|шоурум|shop|store|shopping|retail", text):
        return "Нет признаков магазина в описании или категории"
    return ""


class InstagramWarmup:
    def __init__(self, sessions, browser, profiles, now=time.time):
        self.sessions, self.browser, self.profiles, self.now = sessions, browser, profiles, now
        value = self._load()
        if value.get("run", {}).get("status") == "running":
            value["run"]["status"] = "interrupted"
            self._event(
                value["run"], "Запуск прерван перезапуском приложения; повторной отправки нет"
            )
        # Import the last run saved by versions that did not have an archive yet.
        if value.get("run"):
            run = value["run"]
            if "profile_name" not in run:
                run["profile_name"] = next(
                    (p["name"] for p in self.profiles() if p["id"] == run["profile_id"]),
                    "Аккаунт удалён",
                )
            self._save(value)

    def _load(self):
        with self.sessions() as session:
            row = session.get(Setting, KEY)
            return deepcopy(row.value) if row else {"settings": WarmupSettings().model_dump()}

    def _save(self, value):
        run = value.get("run")
        if run:
            run.setdefault("id", uuid.uuid4().hex)
            if run["status"] != "running" and not run.get("completed_at"):
                events = run.get("events") or []
                run["completed_at"] = events[-1]["at"] if events else run["started_at"]
        with self.sessions.begin() as session:
            session.merge(Setting(key=KEY, value=deepcopy(value)))
            if run:
                key = HISTORY_PREFIX + run["id"]
                previous = session.get(Setting, key)
                record = {
                    name: deepcopy(run.get(name))
                    for name in (
                        "id",
                        "profile_id",
                        "profile_name",
                        "status",
                        "stage",
                        "started_at",
                        "completed_at",
                        "targets",
                        "sent",
                        "liked",
                        "followed",
                        "checked",
                        "events",
                        "settings",
                    )
                }
                record["hidden"] = bool(previous and previous.value.get("hidden"))
                session.merge(Setting(key=key, value=record))

    def history(self, params=None):
        with self.sessions() as session:
            rows = session.scalars(
                select(Setting)
                .where(
                    Setting.key.startswith(HISTORY_PREFIX, autoescape=True),
                    Setting.value["hidden"].as_boolean().is_not(True),
                )
                .order_by(Setting.updated_at.desc())
                .limit(200)
            )
            records = [
                {
                    key: value
                    for key, value in row.value.items()
                    if key not in {"settings", "events", "hidden"}
                }
                for row in rows
            ]
        return sorted(records, key=lambda row: row["started_at"], reverse=True)

    def detail(self, params):
        identifier = str(params.get("id") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise UserError("Запуск прогрева не найден.")
        with self.sessions() as session:
            row = session.get(Setting, HISTORY_PREFIX + identifier)
            if row is None or row.value.get("hidden"):
                raise UserError("Запуск прогрева не найден.")
            return deepcopy(row.value)

    def _event(self, run, text, username="", outcome="info"):
        run.setdefault("events", []).append(
            {"at": self.now(), "text": text, "username": username, "outcome": outcome}
        )
        run["events"] = run["events"][-150:]

    def state(self, params=None):
        value = self._load()
        value["profiles"] = self.profiles()
        return value

    def settings(self, params):
        value = self._load()
        if value.get("run", {}).get("status") == "running":
            raise UserError("Сначала остановите текущий прогрев.")
        value["settings"] = WarmupSettings.model_validate(params).model_dump()
        self._save(value)
        return self.state()

    def start(self, params):
        from .browser_idle import window_work
        from .outreach.senders import current_status

        value = self._load()
        if value.get("run", {}).get("status") == "running":
            raise UserError("Прогрев уже запущен. Сначала остановите его.")
        identifier = str(params.get("profile_id") or "")
        profiles = {row["id"]: row for row in self.profiles()}
        if identifier not in profiles:
            raise UserError("Выберите существующий аккаунт Instagram.")
        settings = WarmupSettings.model_validate(value["settings"])
        if settings.messages and not settings.message.strip():
            raise UserError("Задайте текст сообщения в настройках прогрева.")
        if not (settings.messages or settings.follows or settings.likes):
            raise UserError("Включите хотя бы одно действие прогрева.")
        with self.sessions() as session:
            if window_work(session, identifier)[0]:
                raise UserError("Аккаунт занят. Завершите парсинг, рассылку или чтение ответов.")
            if current_status(session, identifier).status != "active":
                raise UserError("Аккаунт приостановлен или ограничен. Проверьте его состояние.")
        value["run"] = {
            "id": uuid.uuid4().hex,
            "profile_name": profiles[identifier]["name"],
            "settings": settings.model_dump(),
            "targets": {
                "messages": settings.messages,
                "likes": settings.likes,
                "follows": settings.follows,
            },
            "profile_id": identifier,
            "status": "running",
            "stage": "open",
            "started_at": self.now(),
            "next_at": 0,
            "query_index": 0,
            "candidates": [],
            "seen": [],
            "current": "",
            "checked": 0,
            "sent": 0,
            "message_attempts": 0,
            "liked": 0,
            "followed": 0,
            "follow_attempts": 0,
            "like_attempts": 0,
            "events": [],
        }
        self._event(value["run"], "Прогрев запущен")
        self._save(value)
        return self.state()

    def stop(self, params=None):
        value = self._load()
        if value.get("run", {}).get("status") == "running":
            value["run"]["status"] = "stopped"
            self._event(value["run"], "Остановлено пользователем")
            self._save(value)
        return self.state()

    def _contact_key(self, identifier, username):
        digest = hashlib.sha256(f"{identifier}:{username}".encode()).hexdigest()
        return "ig_warmup_contact:" + digest[:60]

    def _reserve(self, identifier, username, action):
        key = self._contact_key(identifier, username)
        with self.sessions.begin() as session:
            row = session.get(Setting, key)
            history = dict(row.value) if row else {}
            if action in history:
                return False
            # Persist intent BEFORE any external click: an uncertain result is never retried.
            history[action] = self.now()
            session.merge(Setting(key=key, value=history))
        return True

    def _excluded(self, identifier, username):
        with self.sessions() as session:
            contact = session.get(Setting, self._contact_key(identifier, username))
            if contact and "message" in contact.value:
                return True
            lead = session.scalar(
                select(Lead).where(Lead.platform == "instagram", Lead.username == username)
            )
            return bool(lead and (lead.do_not_contact or lead.last_contacted_at))

    def tick(self, params=None):
        value = self._load()
        run = value.get("run")
        if not run or run["status"] != "running" or run["next_at"] > self.now():
            return {"ok": True}
        settings = WarmupSettings.model_validate(value["settings"])
        try:
            self._step(run, settings)
        except UserError as error:
            run["status"] = "failed"
            self._event(run, str(error), run["current"], "error")
        except Exception as error:
            frames = traceback.extract_tb(error.__traceback__)
            site = next(
                (
                    frame
                    for frame in reversed(frames)
                    if Path(frame.filename).name
                    in {"instagram_warmup_browser.py", "chromium_runtime.py", "instagram_warmup.py"}
                ),
                None,
            )
            log.error(
                "instagram_warmup_step_failed",
                extra={
                    "error_type": type(error).__name__,
                    "where": f"{Path(site.filename).name}:{site.lineno}" if site else None,
                    "detail": run["stage"],
                },
            )
            run["status"] = "failed"
            self._event(
                run,
                f"Шаг {run['stage']} не подтверждён ({type(error).__name__}). "
                "Запуск остановлен; повторной отправки нет",
                run["current"],
                "error",
            )
        run["next_at"] = self.now() + settings.delay_seconds
        self._save(value)
        return {"ok": True}

    def _step(self, run, settings):
        identifier, stage = run["profile_id"], run["stage"]
        if stage != "open":
            # Observe restrictions received between ticks before navigate/send clears flags.
            self.browser.warmup_action(identifier, "check", "")
        if stage == "open":
            self.browser.open(identifier)
            run["stage"] = "search"
        elif stage == "search":
            need_shops = (
                run["message_attempts"] < settings.messages
                or run["follow_attempts"] < settings.follows
            )
            if not need_shops or run["checked"] >= 100:
                run["stage"] = "feed"
            elif run["candidates"]:
                username = run["candidates"].pop(0)
                if username in run["seen"]:
                    return
                run["seen"].append(username)
                run["checked"] += 1
                if self._excluded(identifier, username):
                    self._event(run, "Уже писали или контакт исключён", username, "skipped")
                    return
                run["current"] = username
                self.browser.navigate(identifier, f"https://www.instagram.com/{username}/")
                run["stage"] = "qualify"
            elif run["query_index"] < len(settings.queries):
                query = settings.queries[run["query_index"]]
                run["query_index"] += 1
                result = self.browser.warmup_action(identifier, "search", query)
                run["candidates"] = result.get("usernames", [])[:50]
                self._event(run, f"Поиск «{query}»: {len(run['candidates'])} кандидатов")
            else:
                self._event(run, "Поиск завершён: доступные кандидаты закончились")
                run["stage"] = "feed"
        elif stage == "qualify":
            profile = self.browser.warmup_action(identifier, "profile", run["current"])
            reason = russian_shop(profile)
            if reason:
                self._event(run, reason, run["current"], "skipped")
                run["stage"] = "search"
            else:
                self._event(
                    run,
                    f"Магазин на русском · {profile['followers_count']} подписчиков",
                    run["current"],
                )
                run["stage"] = "follow"
        elif stage == "follow":
            run["stage"] = "message"
            if run["follow_attempts"] < settings.follows and self._reserve(
                identifier, run["current"], "follow"
            ):
                run["follow_attempts"] += 1
                result = self.browser.warmup_action(identifier, "follow", run["current"])
                if result.get("confirmed"):
                    run["followed"] += 1
                elif result.get("attempted") is False:
                    run["follow_attempts"] -= 1
                self._event(
                    run,
                    result.get("detail", "Подписка не подтверждена"),
                    run["current"],
                    "done" if result.get("confirmed") else "skipped",
                )
        elif stage == "message":
            run["stage"] = "search"
            if (
                run["message_attempts"] < settings.messages
                and not self._excluded(identifier, run["current"])
                and self._reserve(identifier, run["current"], "message")
            ):
                run["message_attempts"] += 1
                result = self.browser.send_message(identifier, run["current"], settings.message)
                if result.get("outcome") == "sent":
                    run["sent"] += 1
                    self._event(run, "Сообщение отправлено", run["current"], "done")
                else:
                    error = result.get("error", "unconfirmed")
                    if error == "typing_mismatch":
                        counts = ""
                        expected = result.get("expected_length")
                        actual = result.get("actual_length")
                        if type(expected) is int and type(actual) is int:
                            counts = f" (набрано {actual} из {expected} символов)"
                        raise UserError(
                            "Текст в поле сообщения не совпал с подготовленным"
                            + counts
                            + ". Прогрев остановлен, сообщение не отправлено."
                        )
                    self._event(
                        run, "Сообщение пропущено: " + str(error), run["current"], "skipped"
                    )
                    if error not in {"existing_thread", "not_found", "messages_closed"}:
                        raise UserError("Отправка остановлена: " + str(error))
                    run["message_attempts"] -= 1
        elif stage == "feed":
            if settings.likes:
                self.browser.navigate(identifier, "https://www.instagram.com/")
                run["stage"] = "like"
            else:
                self._finish(run)
        elif stage == "like":
            if run["liked"] >= settings.likes or run["like_attempts"] >= 12:
                self._finish(run)
            else:
                run["like_attempts"] += 1
                result = self.browser.warmup_action(identifier, "like", "")
                if result.get("confirmed"):
                    run["liked"] += 1
                self._event(
                    run,
                    result.get("detail", "В ленте нет доступной публикации"),
                    outcome="done" if result.get("confirmed") else "skipped",
                )

    def _finish(self, run):
        run["status"] = "done"
        run["current"] = ""
        self._event(
            run,
            f"Завершено: сообщений {run['sent']}, "
            f"лайков {run['liked']}, подписок {run['followed']}",
        )
