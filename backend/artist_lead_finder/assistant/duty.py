"""«Автоведение»: the app keeps «Рассылка» and the parser going by itself.

Turned on through the assistant (a proposal the user confirms). Every five minutes, with
the shell's round: the outreach limits are kept as configured; «Рассылка» starts when the
list has new usernames and nothing is being sent (senders with the fewest messages in the
last 24 hours go first); the parser starts on its account, at most once an hour, while the
list has fewer new usernames than the queue target. A parser run that is paused (a login,
a rate limit, the user) is never resumed here, and a run interrupted by a restart of the
app is closed so that the next one continues from the parser's memory.

Limits and checks of Instagram are not worked around: everything goes through the app's
own methods, the same as the buttons.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from ..errors import UserError
from ..models import Setting

log = logging.getLogger(__name__)

KEY = "assistant_duty"
EVERY = 300.0
SCOUT_EVERY = 3600.0
RUN_OPEN = ("running", "paused")
LOG_KEPT = 20


def _number(value: object, default: int, low: int, high: int, name: str) -> int:
    number = int(value or default)
    if not low <= number <= high:
        raise UserError(f"{name}: от {low} до {high}.")
    return number


class Duty:
    def __init__(
        self,
        sessions,
        core: Callable[[str, dict], Any],
        request_scout: Callable[[str, int | None], None],
        now: Callable[[], float] = time.time,
    ):
        self.sessions = sessions
        self.core = core
        self.request_scout = request_scout
        self.now = now
        self.last_tick = 0.0

    # ---------- configuration ----------

    @staticmethod
    def config(params: dict) -> dict:
        if not params.get("parser_id") or not params.get("sender_ids"):
            raise UserError("Нужны аккаунт парсера и хотя бы один отправитель.")
        return {
            "enabled": True,
            "parser_id": str(params["parser_id"]),
            "sender_ids": [str(item) for item in params["sender_ids"]],
            "daily_limit_per_sender": _number(
                params.get("daily_limit_per_sender"), 60, 1, 500, "Лимит в сутки"
            ),
            "send_interval_seconds": _number(
                params.get("send_interval_seconds"), 600, 30, 86400, "Пауза между сообщениями"
            ),
            "queue_target": _number(params.get("queue_target"), 120, 1, 10000, "Запас в списке"),
        }

    def load(self) -> dict:
        with self.sessions() as session:
            row = session.get(Setting, KEY)
            return dict(row.value) if row and isinstance(row.value, dict) else {}

    def _store(self, value: dict) -> None:
        with self.sessions.begin() as session:
            row = session.get(Setting, KEY) or Setting(key=KEY)
            row.value = value
            session.add(row)

    def save(self, params: dict) -> None:
        current = self.load()
        if params.get("enabled"):
            value = {**self.config(params), "log": current.get("log") or []}
        else:
            value = {**current, "enabled": False}
        self._store(value)
        self.last_tick = 0.0
        self._note("включено" if value["enabled"] else "выключено")

    def _note(self, text: str) -> None:
        current = self.load()
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.now()))
        current["log"] = [*(current.get("log") or []), f"{stamp} {text}"][-LOG_KEPT:]
        self._store(current)
        log.info("duty", extra={"detail": text[:200]})

    def _patch(self, **values) -> None:
        self._store({**self.load(), **values})

    def view(self, names: dict[str, str]) -> dict:
        current = self.load()
        if not current:
            return {"enabled": False}
        return {
            "enabled": bool(current.get("enabled")),
            "parser": names.get(current.get("parser_id"), ""),
            "senders": [names.get(key, "") for key in current.get("sender_ids") or []],
            "daily_limit_per_sender": current.get("daily_limit_per_sender"),
            "send_interval_seconds": current.get("send_interval_seconds"),
            "queue_target": current.get("queue_target"),
            "log": (current.get("log") or [])[-8:],
        }

    def started(self, job_id: object, error: str) -> None:
        """The shell's answer to a parser start; kept in the log when «Автоведение» is on."""
        if self.load().get("enabled"):
            self._note(f"парсер не запущен: {error}" if error else f"парсер запущен, №{job_id}")

    # ---------- the round ----------

    def tick(self) -> None:
        if self.now() - self.last_tick < EVERY:
            return
        self.last_tick = self.now()
        config = self.load()
        if not config.get("enabled"):
            return
        try:
            self._round(config)
        except UserError as error:
            self._note(f"ошибка: {error}")

    def _round(self, config: dict) -> None:
        core = self.core
        settings = core("settings.get", {})
        wanted = {
            "outreach_daily_limit_per_sender": config["daily_limit_per_sender"],
            "outreach_send_interval_seconds": config["send_interval_seconds"],
        }
        if any(settings.get(key) != value for key, value in wanted.items()):
            core("settings.save", {**settings, **wanted})
        work = core("outreach.workspace", {})
        if set(work["sender_ids"]) != set(config["sender_ids"]):
            if work.get("running"):
                raise UserError("отправители изменены, пока идёт рассылка; жду её конца")
            work = core("outreach.workspace_update", {"sender_ids": config["sender_ids"]})
        fresh = [item for item in work["usernames"] if item["status"] == "new"]
        if fresh and not work.get("running"):
            sent = {row["id"]: row.get("sent_24h", 0) for row in core("outreach.senders", {})}
            ordered = sorted(config["sender_ids"], key=lambda key: sent.get(key, 0))
            if list(work["sender_ids"]) != ordered:
                core("outreach.workspace_update", {"sender_ids": ordered})
            campaign = core("outreach.workspace_start", {}).get("campaign") or {}
            self._note(f"рассылка запущена, кампания №{campaign.get('id')}")
        account = next(
            (
                item
                for item in core("scout.accounts", {})
                if item["profile"]["id"] == config["parser_id"]
            ),
            None,
        )
        if account is None:
            raise UserError("аккаунт парсера удалён")
        run = account.get("run") or {}
        if run.get("stage") == "interrupted" and run.get("status") in RUN_OPEN:
            core("jobs.control", {"id": run["id"], "action": "cancel"})
            self._note(f"прерванный запуск парсера №{run['id']} закрыт")
            run = {}
            config = {**config, "next_scout_after": 0}
            self._patch(next_scout_after=0)
        if run.get("status") in RUN_OPEN:
            return  # a pause for a login, a rate limit or by hand is never resumed here
        if len(fresh) >= config["queue_target"]:
            return
        if self.now() < float(config.get("next_scout_after") or 0):
            return
        self._patch(next_scout_after=self.now() + SCOUT_EVERY)
        self.request_scout(config["parser_id"], config["queue_target"])
        self._note("запрошен запуск парсера")
