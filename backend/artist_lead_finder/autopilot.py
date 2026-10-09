"""«Найти и написать»: one account finds N new leads, then the primary outreach writes to them.

The window starts the parser the usual way (`browser_action` «scout»); the core only
remembers what was asked and watches it. `tick` runs with the outreach queue every few
seconds: when that account's parser run is completed, the leads it created are put in the
outreach list and «Рассылка» starts with the list's messages, as if started by hand. A
parser run stopped by the user or failed starts nothing.

The state lives in one settings row, so a restart of the app keeps watching.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select

from .errors import UserError
from .models import (
    BrowserQueue,
    OutreachCampaign,
    ScoutAccount,
    ScoutDecision,
    SearchJob,
    Setting,
    utcnow,
)
from .outreach.campaigns import UNFINISHED
from .outreach.workspace import WorkspaceService, add_to_list

log = logging.getLogger(__name__)

KEY = "autopilot"
# The window has this long to start the parser after «Найти и написать».
START_GRACE = timedelta(minutes=3)
ACTIVE = ("starting", "scouting", "sending")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Autopilot:
    def __init__(
        self,
        sessions,
        set_target: Callable[[dict], dict],
        start_outreach: Callable[[dict], dict],
        sender_names: Callable[[], dict[str, str]],
        now: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.set_target = set_target
        self.start_outreach = start_outreach
        self.sender_names = sender_names
        self.now = now

    def _load(self, session) -> dict | None:
        row = session.get(Setting, KEY)
        return dict(row.value) if row and isinstance(row.value, dict) else None

    def _save(self, session, value: dict) -> None:
        row = session.get(Setting, KEY) or Setting(key=KEY)
        row.value = value
        session.add(row)

    def start(self, params: dict) -> dict:
        profile = str(params.get("profile_id") or "")
        if profile not in self.sender_names():
            raise UserError("Выберите аккаунт.")
        target = int(params.get("target") or 0)
        if not 1 <= target <= 1000:
            raise UserError("Сколько лидов найти: от 1 до 1000.")
        with self.sessions.begin() as session:
            current = self._load(session)
            if current and current["status"] in ACTIVE:
                raise UserError("Автопилот уже работает. Остановите его, чтобы начать заново.")
            workspace = WorkspaceService._row(session)
            campaign = (
                session.get(OutreachCampaign, workspace.campaign_id)
                if workspace.campaign_id
                else None
            )
            if campaign and campaign.status in UNFINISHED:
                raise UserError("Рассылка уже идёт. Дождитесь конца или остановите её.")
            if not workspace.messages:
                raise UserError("Добавьте хотя бы одно сообщение для рассылки.")
            if not workspace.sender_ids:
                # The account that finds the leads writes to them.
                workspace.sender_ids = [profile]
            after = session.scalar(
                select(BrowserQueue.job_id)
                .where(BrowserQueue.profile_id == profile)
                .order_by(BrowserQueue.job_id.desc())
                .limit(1)
            )
            self._save(
                session,
                {
                    "status": "starting",
                    "profile_id": profile,
                    "target": target,
                    "after_job": after or 0,
                    "job_id": None,
                    "campaign_id": None,
                    "message": "",
                    "started_at": self.now().isoformat(),
                },
            )
        # Found counts from zero: the goal is N new leads of this run.
        self.set_target({"profile_id": profile, "target": target, "reset": True})
        log.info("autopilot_started", extra={"detail": f"target {target}"})
        return self.state({})

    def cancel(self, params: dict) -> dict:
        """Stops the chain only; a running parser or outreach is stopped where it runs."""
        with self.sessions.begin() as session:
            current = self._load(session)
            if current and current["status"] in ACTIVE:
                self._save(
                    session,
                    {**current, "status": "cancelled", "message": "Автопилот остановлен."},
                )
        return self.state({})

    def tick(self) -> None:
        with self.sessions.begin() as session:
            current = self._load(session)
            if not current or current["status"] not in ACTIVE:
                return
            if current["status"] == "sending":
                campaign = session.get(OutreachCampaign, current["campaign_id"] or 0)
                if campaign is None or campaign.status not in UNFINISHED:
                    sent = campaign.sent_count if campaign else 0
                    self._save(
                        session,
                        {**current, "status": "done", "message": f"Готово: отправлено {sent}."},
                    )
                return
            job = session.scalar(
                select(SearchJob)
                .join(BrowserQueue, BrowserQueue.job_id == SearchJob.id)
                .where(
                    BrowserQueue.profile_id == current["profile_id"],
                    SearchJob.id > current["after_job"],
                )
                .order_by(SearchJob.id.desc())
                .limit(1)
            )
            if job is None:
                started = _parse(current["started_at"])
                if started and self.now() - started > START_GRACE:
                    self._save(
                        session,
                        {
                            **current,
                            "status": "failed",
                            "message": "Парсинг не запустился. Откройте окно аккаунта и повторите.",
                        },
                    )
                return
            if current["status"] == "starting":
                current = {**current, "status": "scouting", "job_id": job.id}
                self._save(session, current)
            if job.status in ("queued", "running", "paused"):
                return
            if job.status != "completed":
                self._save(
                    session,
                    {
                        **current,
                        "status": "stopped",
                        "message": "Парсинг остановлен, рассылка не запускалась.",
                    },
                )
                return
            # The run's new leads go to the list even when adding them is off in Scout.
            names = list(
                session.scalars(
                    select(ScoutDecision.username).where(
                        ScoutDecision.job_id == job.id,
                        ScoutDecision.decision == "lead_created",
                    )
                )
            )
            add_to_list(session, names)
            current = {**current, "found": len(names)}
            self._save(session, current)
        self._send(current)

    def _send(self, current: dict) -> None:
        """«Рассылка» after the parser; outside the watch transaction, like a click."""
        try:
            state = self.start_outreach({})
            campaign = state.get("campaign") or {}
            update = {
                "status": "sending",
                "campaign_id": campaign.get("id"),
                "message": f"Парсинг нашёл {current['found']}, идёт рассылка.",
            }
        except UserError as error:
            update = {"status": "failed", "message": f"Рассылка не запустилась: {error}"}
        log.info("autopilot_outreach", extra={"detail": update["status"]})
        with self.sessions.begin() as session:
            self._save(session, {**current, **update})

    def state(self, params: dict) -> dict:
        self.tick()
        with self.sessions() as session:
            current = self._load(session)
            if not current:
                return {"status": "idle"}
            job = session.get(SearchJob, current["job_id"]) if current.get("job_id") else None
            campaign = (
                session.get(OutreachCampaign, current["campaign_id"])
                if current.get("campaign_id")
                else None
            )
            account = session.get(ScoutAccount, current["profile_id"])
            return {
                **current,
                "account": self.sender_names().get(current["profile_id"], ""),
                "scout_found": account.found if account else 0,
                "scout_status": job.status if job else None,
                "sent": campaign.sent_count if campaign else 0,
                "total": campaign.total_recipients if campaign else 0,
            }
