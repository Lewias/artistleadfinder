"""Cloud outreach jobs: the app's «Рассылка» on the server, `python -m cloud_worker.outreach`.

A job is a list of usernames, the message variants and the app's outreach settings. The
user's own core on the server (a folder of its own, apart from the parser's) turns them
into an ordinary campaign, so the queue, the pace and daily limit, the skipping of people
already in Direct and the never-resend rule are exactly the app's. This process plays the
desktop shell's part (src-tauri/src/main.rs, `run_outreach_driver`): it opens the
recipient's profile in the sender's Chromium, sends through Instagram's own interface and
hands the result back to the core.

Messages leave only through the account's proxy (the database refuses a job without one).
Every result goes to the job's results; a sent message also moves the CRM contact's last
contact date.
"""

import logging
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import crm
from .runner import JobFailed, Runner

log = logging.getLogger(__name__)

USERNAME = re.compile(r"^[a-z0-9_.]{1,30}$")
UNFINISHED = ("draft", "scheduled", "running", "paused")
# Seconds between looks at the queue when nothing is due (the core paces the sends).
IDLE = 3.0
REFRESH_SECONDS = 15
DONE = ("sent", "failed", "skipped")
NO_PROXY = (
    "Для облачной рассылки у аккаунта должен быть прокси. Добавьте его в «Аккаунтах» "
    "и отправьте задачу снова."
)
SENDER_FATAL = {
    "auth_required": "Instagram просит войти в аккаунт. Откройте его в приложении, войдите "
    "и запустите рассылку снова — сессия отправится заново.",
    "checkpoint": "Instagram просит подтвердить аккаунт (checkpoint). Пройдите проверку в "
    "приложении и запустите рассылку снова.",
    "disabled": "Аккаунт-отправитель выключен.",
}
REASONS = {
    "ALREADY_IN_DIRECT": "уже есть переписка в Директе",
    "MESSAGES_CLOSED": "закрыты сообщения",
    "RECIPIENT_UNAVAILABLE": "профиль недоступен",
    "ALREADY_CONTACTED": "уже писали раньше",
    "DO_NOT_CONTACT": "«не связываться»",
    "MESSAGE_REJECTED": "Instagram отклонил сообщение",
    "SEND_ERROR": "ошибка отправки",
    "NETWORK_ERROR": "сетевая ошибка",
}


def profile_url(username: str) -> str | None:
    return f"https://www.instagram.com/{username}/" if USERNAME.match(username or "") else None


class OutreachRunner(Runner):
    kind = "outreach"

    def __init__(self, *args, sleep: Callable[[float], None] = time.sleep, **kwargs):
        super().__init__(*args, **kwargs)
        self.sleep = sleep
        # Outcomes already in the job's results: (job, username) -> outcome.
        self.recorded: dict[tuple[str, str], str] = {}

    # ---------- the job ----------

    def collect(self, job: dict, engine, row: dict) -> None:
        job_id = str(job["id"])
        params = job["params"]
        profile = job["profile_id"]
        record = row["record"] or {}
        self.recorded.clear()
        if not isinstance(record.get("proxy"), dict):
            raise JobFailed(NO_PROXY)
        engine.put_session(profile, row["name"], record)
        engine.apply_outreach_settings(params.get("settings") or {})
        campaign_id = self.campaign_of(engine, (job.get("progress") or {}).get("campaign_id"))
        if campaign_id is None:
            campaign_id = self.start_campaign(engine, profile, params)
        self.update(
            job_id,
            stage="collecting",
            error=None,
            started_at=job.get("started_at") or self.now(),
            progress={"status": "sending", "campaign_id": campaign_id},
        )
        result, campaign = self.send_all(job, engine, profile, campaign_id)
        try:
            engine.call("browser.runtime.save", {"id": profile})
            self.save_session(job, engine.session(profile))
        except Exception:
            log.warning("cloud_session_save_failed", extra={"job": job_id})
        if result == "lost":
            return
        if result in ("cancelled", "fatal"):
            # Unsent messages are cancelled; what was sent stays sent.
            try:
                engine.call("outreach.workspace_stop", {})
            except Exception:
                log.warning("cloud_outreach_stop_failed", extra={"job": job_id})
            self.record(job, engine, campaign_id)
        if result == "cancelled":
            self.finish(job_id, "cancelled", None)
            return
        if result == "fatal":
            raise JobFailed(SENDER_FATAL.get(campaign["sender"], "Аккаунт не может писать."))
        counters = self.update_counters(job_id)
        if not counters.get("sent") and counters.get("failed"):
            raise JobFailed("Ни одно сообщение не ушло. Причины — в результатах задачи.")
        self.finish(job_id, "completed", None)

    def campaign_of(self, engine, campaign_id) -> int | None:
        """The campaign this job started before a restart, if the core still has it."""
        if not campaign_id:
            return None
        try:
            engine.call("outreach.campaign", {"id": int(campaign_id)})
        except Exception:
            return None
        return int(campaign_id)

    def start_campaign(self, engine, profile: str, params: dict) -> int:
        try:
            # A campaign a stopped process left running in this folder is not continued.
            engine.call("outreach.workspace_stop", {})
            engine.call(
                "outreach.workspace_update",
                {
                    "usernames": list(params["usernames"]),
                    "messages": list(params["messages"]),
                    "sender_ids": [profile],
                },
            )
            state = engine.call("outreach.workspace_start", {})
        except Exception as error:
            # The core's checks speak to the user («В списке нет аккаунтов, …»).
            raise JobFailed(f"Рассылка не запустилась: {error}") from None
        campaign = (state or {}).get("campaign") or {}
        if not campaign.get("id"):
            raise JobFailed("Рассылка не запустилась.")
        return int(campaign["id"])

    # ---------- the send loop ----------

    def send_all(self, job: dict, engine, profile: str, campaign_id: int) -> tuple[str, dict]:
        """Sends until the campaign ends; (how it ended, the last campaign view)."""
        job_id = str(job["id"])
        refreshed = None
        view: dict = {}
        while True:
            now = self.alive = self.clock()
            if refreshed is None or now - refreshed >= REFRESH_SECONDS:
                refreshed = now
                current = self.renew(job_id)
                if current is None:
                    return "lost", view
                if current["cancel_requested"]:
                    return "cancelled", view
                view = self.look(job, engine, campaign_id)
                if view["status"] not in UNFINISHED:
                    return "done", view
                if view["sender"] in SENDER_FATAL:
                    return "fatal", view
            opened = engine.call("browser.runtime.is_open", {"id": profile}) or {}
            if opened.get("open") is not True:
                engine.call("browser.runtime.open", {"id": profile})
            item = engine.call("outreach.next_internal", {}) or {}
            if item.get("job_id"):
                self.send_one(engine, item)
                refreshed = None  # the result shows at once
                continue
            self.sleep(IDLE)

    def send_one(self, engine, item: dict) -> None:
        """One message, the way the desktop shell sends it; the core records the result."""
        profile = item["profile_id"]
        args = item.get("args") or {}
        url = profile_url(str(args.get("username") or ""))
        if url is None:
            result = {"outcome": "error", "error": "bad_request"}
        else:
            try:
                page = engine.call("browser.runtime.navigate", {"id": profile, "url": url}) or {}
            except Exception:
                page = None
            if page is None:
                result = {"outcome": "error", "error": "network"}
            elif page.get("rate_limited") is True:
                result = {"outcome": "error", "error": "rate_limited"}
            else:
                try:
                    result = engine.call(
                        "browser.runtime.send_message",
                        {"id": profile, "username": args["username"], "text": args.get("text")},
                    )
                except Exception:
                    result = {"outcome": "error", "error": "browser"}
        try:
            engine.call(
                "outreach.commit_internal",
                {"job_id": item["job_id"], "token": item.get("token"), "result": result},
            )
        except Exception:
            # The claim stays; the core settles it for review and never resends it.
            log.warning("cloud_outreach_commit_failed")

    # ---------- what the user sees ----------

    def look(self, job: dict, engine, campaign_id: int) -> dict:
        """Results into the job and the CRM, and the job's progress; the campaign view."""
        campaign = engine.call("outreach.campaign", {"id": campaign_id}) or {}
        self.record(job, engine, campaign_id)
        sender = (campaign.get("senders") or [{}])[0]
        view = {
            "status": campaign.get("status") or "running",
            "sender": sender.get("status") or "active",
        }
        self.update(
            str(job["id"]),
            progress={
                "status": "sending",
                "campaign_id": campaign_id,
                "total": int(campaign.get("total_recipients") or 0),
                "sent": int(campaign.get("sent_count") or 0),
                "failed": int(campaign.get("failed_count") or 0),
                "skipped": int(campaign.get("skipped_count") or 0),
                "queued": int(campaign.get("queued_count") or 0),
                "sender_status": view["sender"],
                "sender_reason": sender.get("reason"),
                "until": sender.get("until"),
                "sent_24h": sender.get("sent_24h"),
                "daily_limit": sender.get("daily_limit"),
            },
        )
        return view

    def record(self, job: dict, engine, campaign_id: int) -> None:
        """Recipients the core has settled, each into the job's results once (and again
        when the outcome changes); a sent message moves the CRM contact's last contact."""
        job_id, owner = str(job["id"]), str(job["owner_id"])
        page = 1
        while True:
            listing = engine.call(
                "outreach.recipients", {"id": campaign_id, "page": page, "page_size": 200}
            )
            items = (listing or {}).get("items") or []
            for item in items:
                key = (job_id, str(item.get("username") or ""))
                if item.get("status") in DONE and self.recorded.get(key) != item["status"]:
                    self.save_result(job_id, owner, item)
                    self.recorded[key] = item["status"]
            if len(items) < 200:
                break
            page += 1
        self.update_counters(job_id)

    def save_result(self, job_id: str, owner: str, item: dict) -> None:
        username = str(item.get("username") or "")
        outcome = item["status"]
        reason = item.get("failure_reason") or item.get("skip_reason") or ""
        note = REASONS.get(reason, "") if outcome != "sent" else ""
        details = item.get("reason_details") or ""
        if details and outcome != "sent":
            note = f"{note}: {details}" if note else details
        with self.db().transaction(), self.db().cursor() as cur:
            cur.execute(
                """
                insert into public.cloud_candidates (job_id, owner_id, username, outcome, note)
                values (%s, %s, %s, %s, %s)
                on conflict (job_id, username) do update
                   set outcome = excluded.outcome, note = excluded.note
                 where cloud_candidates.outcome is distinct from excluded.outcome
                returning id
                """,
                (job_id, owner, username, outcome, note[:300] or None),
            )
            changed = cur.fetchone()
            if changed is None or outcome != "sent":
                return
            contact = crm.mark_written(cur, owner, username, self.now())
            cur.execute(
                "update public.cloud_candidates set contact_id = %s, profile = %s where id = %s",
                (contact, Jsonb({"username": username}), changed["id"]),
            )

    def update_counters(self, job_id: str) -> dict:
        with self.db().cursor() as cur:
            cur.execute(
                """
                select coalesce(jsonb_array_length(j.params -> 'usernames'), 0) as total,
                       count(c.id) filter (where c.outcome = 'sent') as sent,
                       count(c.id) filter (where c.outcome = 'failed') as failed,
                       count(c.id) filter (where c.outcome = 'skipped') as skipped
                  from public.cloud_jobs j
                  left join public.cloud_candidates c on c.job_id = j.id
                 where j.id = %s
                 group by j.id
                """,
                (job_id,),
            )
            counts = dict(cur.fetchone() or {})
        self.update(job_id, counters=counts)
        return counts


def main() -> None:
    from .__main__ import _Extra
    from .engine import Engine

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Extra("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # A folder per user apart from the parser's: the two processes never share a core.
    data = Path(os.environ.get("CLOUD_DATA", "/data")) / "outreach"
    runner = OutreachRunner(
        lambda: psycopg.connect("", autocommit=True, row_factory=dict_row),
        lambda owner: Engine(data, owner),
        lambda engine: None,
        worker_id=os.environ.get("HOSTNAME", "cloud-outreach"),
    )
    runner.run_forever()


if __name__ == "__main__":
    main()
