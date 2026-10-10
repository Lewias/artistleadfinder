"""«Найти и написать»: the chosen accounts find N new leads each, then the primary outreach
writes to all of them.

The windows start the parser the usual way (`browser_action` «scout»); the core only
remembers what was asked and watches it. `tick` runs with the outreach queue every few
seconds: when every account's parser run is over, the leads the completed runs created are
put in the outreach list and «Рассылка» starts with the list's messages, as if started by
hand. Runs stopped by the user or failed add nothing; when none completed, nothing is sent.

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
# The windows have this long to start the parser after «Найти и написать».
START_GRACE = timedelta(minutes=3)
ACTIVE = ("starting", "scouting", "sending")
RUNNING_JOB = ("queued", "running", "paused")
MAX_PROFILES = 20


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _profiles(current: dict) -> list[str]:
    """The accounts of a run; a state saved before several accounts had one."""
    return list(current.get("profile_ids") or [current.get("profile_id") or ""])


def _after(current: dict, profile: str) -> int:
    after = current.get("after")
    if isinstance(after, dict):
        return int(after.get(profile) or 0)
    return int(current.get("after_job") or 0)


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

    @staticmethod
    def _latest_job(session, profile: str, after: int) -> SearchJob | None:
        return session.scalar(
            select(SearchJob)
            .join(BrowserQueue, BrowserQueue.job_id == SearchJob.id)
            .where(BrowserQueue.profile_id == profile, SearchJob.id > after)
            .order_by(SearchJob.id.desc())
            .limit(1)
        )

    def start(self, params: dict) -> dict:
        names = self.sender_names()
        raw = params.get("profile_ids")
        if raw is None and params.get("profile_id"):
            raw = [params["profile_id"]]
        profiles = list(dict.fromkeys(str(item) for item in (raw or [])))
        if not profiles or any(profile not in names for profile in profiles):
            raise UserError("Выберите аккаунты.")
        if len(profiles) > MAX_PROFILES:
            raise UserError(f"Не больше {MAX_PROFILES} аккаунтов за раз.")
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
            after = {}
            for profile in profiles:
                job = self._latest_job(session, profile, 0)
                if job and job.status in RUNNING_JOB and job.stage != "interrupted":
                    raise UserError(
                        f"На аккаунте {names[profile]} уже идёт парсинг. "
                        "Остановите его или дождитесь конца."
                    )
                after[profile] = job.id if job else 0
            if not workspace.sender_ids:
                # The accounts that find the leads write to them.
                workspace.sender_ids = profiles
            self._save(
                session,
                {
                    "status": "starting",
                    "profile_ids": profiles,
                    "target": target,
                    "after": after,
                    "jobs": {},
                    "campaign_id": None,
                    "message": "",
                    "started_at": self.now().isoformat(),
                },
            )
        # Found counts from zero: the goal is N new leads of this run on each account.
        for profile in profiles:
            self.set_target({"profile_id": profile, "target": target, "reset": True})
        log.info("autopilot_started", extra={"detail": f"{len(profiles)} x {target}"})
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
            jobs = {
                profile: self._latest_job(session, profile, _after(current, profile))
                for profile in _profiles(current)
            }
            started = _parse(current["started_at"])
            late = bool(started and self.now() - started > START_GRACE)
            if not any(jobs.values()):
                if late:
                    self._save(
                        session,
                        {
                            **current,
                            "status": "failed",
                            "message": "Парсинг не запустился. Откройте окна и повторите.",
                        },
                    )
                return
            current = {
                **current,
                "status": "scouting",
                "jobs": {profile: job.id if job else None for profile, job in jobs.items()},
            }
            waiting = any(job is None for job in jobs.values()) and not late
            if waiting or any(job and job.status in RUNNING_JOB for job in jobs.values()):
                self._save(session, current)
                return
            completed = [job.id for job in jobs.values() if job and job.status == "completed"]
            if not completed:
                self._save(
                    session,
                    {
                        **current,
                        "status": "stopped",
                        "message": "Парсинг остановлен, рассылка не запускалась.",
                    },
                )
                return
            # The runs' new leads go to the list even when adding them is off in Scout.
            names = list(
                dict.fromkeys(
                    session.scalars(
                        select(ScoutDecision.username)
                        .where(
                            ScoutDecision.job_id.in_(completed),
                            ScoutDecision.decision == "lead_created",
                        )
                        .order_by(ScoutDecision.id)
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
            profiles = _profiles(current)
            campaign = (
                session.get(OutreachCampaign, current["campaign_id"])
                if current.get("campaign_id")
                else None
            )
            found = 0
            for profile in profiles:
                account = session.get(ScoutAccount, profile)
                found += account.found if account else 0
            names = self.sender_names()
            return {
                "status": current["status"],
                "message": current.get("message") or "",
                "profile_ids": profiles,
                "accounts": [names.get(profile, "") for profile in profiles],
                "target": current.get("target") or 0,
                "goal": (current.get("target") or 0) * len(profiles),
                "found": current.get("found"),
                "scout_found": found,
                "sent": campaign.sent_count if campaign else 0,
                "total": campaign.total_recipients if campaign else 0,
            }
