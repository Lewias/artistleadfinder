"""Persistent background activity, consumed only in the mailing driver's idle slots.

DM claims and sends keep their current path. Activity uses another tab and advances one
short browser step per tick. Selection is random, while scheduling uses fixed resource
budgets and the existing sender pace. No timing or input is varied to disguise automation.
"""

import logging
import random
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from ..instagram_warmup import warming_profiles
from ..models import (
    BrowserQueue,
    CampaignRecipient,
    FollowUpJob,
    FollowUpRun,
    InboxScan,
    InboxScanItem,
    InstagramActivityState,
    InstagramActivityTask,
    Lead,
    OutboundMessageJob,
    OutreachCampaign,
    SearchJob,
    utcnow,
)
from .senders import as_utc, current_status, set_status
from .settings import outreach_settings

log = logging.getLogger("artist_lead_finder.outreach.activity")
KINDS = ("story", "feed", "profile", "like", "navigate")
MIN_SLACK = timedelta(seconds=8)
CLAIM_TTL = timedelta(minutes=2)
DISCOVERY_INTERVAL = timedelta(minutes=1)
ACCOUNT_ERRORS = {
    "login": "auth_required",
    "session_expired": "auth_required",
    "checkpoint": "checkpoint",
    "code_required": "checkpoint",
    "phone_required": "checkpoint",
    "password_reset": "checkpoint",
    "rate_limited": "rate_limited",
    "action_blocked": "rate_limited",
}


class ActivityEngine:
    def __init__(self, sessions, browser, settings, *, now=utcnow, rng=None):
        self.sessions, self.browser, self.settings = sessions, browser, settings
        self.now, self.rng = now, rng or random.SystemRandom()
        self.lock = threading.Lock()
        self.claims = set()

    @staticmethod
    def _log(task, status, reason=None):
        log.info(
            "instagram_activity_" + status,
            extra={
                "campaign_id": task.campaign_id,
                "job_id": task.id,
                "reason": reason,
                "detail": task.kind,
            },
        )

    def recover(self):
        with self.sessions.begin() as session:
            for task in session.scalars(
                select(InstagramActivityTask).where(InstagramActivityTask.status == "running")
            ):
                task.status, task.reason, task.finished_at = "interrupted", "restart", self.now()
                self._log(task, "interrupted", "restart")

    def reconcile(self):
        """Stop background pages as soon as a campaign or its account stops."""
        with self.sessions.begin() as session:
            states = session.execute(
                select(InstagramActivityState, OutreachCampaign).join(
                    OutreachCampaign, OutreachCampaign.id == InstagramActivityState.campaign_id
                )
            ).all()
            closing = []
            for state, campaign in states:
                if campaign.status == "running" and not state.stopped:
                    continue
                closing.append(state.sender_id)
                tasks = session.scalars(
                    select(InstagramActivityTask).where(
                        InstagramActivityTask.sender_id == state.sender_id,
                        InstagramActivityTask.status.in_(("pending", "running")),
                    )
                )
                for task in tasks:
                    if (
                        task.status == "pending"
                        and campaign.status == "paused"
                        and not state.stopped
                    ):
                        continue
                    task.status = "interrupted" if task.status == "running" else "cancelled"
                    task.reason, task.finished_at = "campaign_stopped", self.now()
                    self.claims.discard(task.id)
                    self._log(task, task.status, task.reason)
        close = getattr(self.browser, "activity_close", None)
        if callable(close):
            for sender in closing:
                close(sender)

    def _enqueue(self, session, campaign_id, sender_id, kind, target, payload):
        session.execute(
            insert(InstagramActivityTask)
            .values(
                campaign_id=campaign_id,
                sender_id=sender_id,
                kind=kind,
                target=target,
                payload=payload,
                status="pending",
                phase="open",
                created_at=self.now(),
            )
            .on_conflict_do_nothing(index_elements=["sender_id", "target"])
        )

    def yield_sender(self, sender):
        """Release only the background tab before the existing DM driver takes over."""
        close = getattr(self.browser, "activity_close", None)
        reason = close(sender) if callable(close) else None
        with self.sessions.begin() as session:
            for task in session.scalars(
                select(InstagramActivityTask).where(
                    InstagramActivityTask.sender_id == sender,
                    InstagramActivityTask.status == "running",
                )
            ):
                task.status, task.reason, task.finished_at = (
                    "interrupted",
                    "dm_priority",
                    self.now(),
                )
                self.claims.discard(task.id)
                self._log(task, "interrupted", "dm_priority")
            if reason in ACCOUNT_ERRORS:
                set_status(session, sender, ACCOUNT_ERRORS[reason], reason, until=None)
                state = session.get(InstagramActivityState, sender)
                if state:
                    state.stopped = True
        return reason if reason in ACCOUNT_ERRORS else None

    def _plan(self, session):
        from ..instagram_activity_browser import canonical

        campaigns = list(
            session.scalars(
                select(OutreachCampaign)
                .where(OutreachCampaign.status == "running")
                .order_by(OutreachCampaign.id)
            )
        )
        terminal = select(OutreachCampaign.id).where(
            OutreachCampaign.status.in_(("completed", "cancelled", "failed"))
        )
        session.execute(
            update(InstagramActivityTask)
            .where(
                InstagramActivityTask.campaign_id.in_(terminal),
                InstagramActivityTask.status.in_(("pending", "running")),
            )
            .values(status="cancelled", reason="campaign_finished", finished_at=self.now())
        )
        stopped = select(OutreachCampaign.id).where(OutreachCampaign.status != "running")
        session.execute(
            update(InstagramActivityTask)
            .where(
                InstagramActivityTask.campaign_id.in_(stopped),
                InstagramActivityTask.status == "running",
            )
            .values(status="interrupted", reason="campaign_stopped", finished_at=self.now())
        )
        senders = {}
        for campaign in campaigns:
            for sender in campaign.sender_ids:
                senders.setdefault(sender, campaign)
        for sender, campaign in senders.items():
            state = session.get(InstagramActivityState, sender)
            if state is None:
                state = InstagramActivityState(sender_id=sender, campaign_id=campaign.id)
                session.add(state)
                session.flush()
            elif state.campaign_id != campaign.id:
                state.campaign_id, state.disabled, state.stopped = campaign.id, [], False
                state.discovery_at, state.discovery_number = None, 0
            if state.stopped:
                continue
            if state.discovery_at is None or as_utc(state.discovery_at) <= self.now():
                if "discover" not in state.disabled:
                    self._enqueue(
                        session,
                        campaign.id,
                        sender,
                        "discover",
                        f"feed:{campaign.id}:{state.discovery_number}",
                        {},
                    )
                    state.discovery_number += 1
                state.discovery_at = self.now() + DISCOVERY_INTERVAL
            names = session.scalars(
                select(CampaignRecipient.username)
                .join(Lead, Lead.id == CampaignRecipient.lead_id)
                .where(
                    CampaignRecipient.campaign_id == campaign.id,
                    CampaignRecipient.sender_account_id == sender,
                    Lead.do_not_contact.is_(False),
                )
                .order_by(CampaignRecipient.id)
                .limit(30)
            )
            for name in names:
                url = canonical(f"https://www.instagram.com/{name}/", "profile")
                if url and "profile" not in state.disabled:
                    self._enqueue(session, campaign.id, sender, "profile", url, {"url": url})
        return list(senders)

    def _slot(self, session, sender):
        """Leave enough time before the next DM. A DM already claimed always wins."""
        state = session.get(InstagramActivityState, sender)
        campaign = session.get(OutreachCampaign, state.campaign_id) if state else None
        if not campaign or campaign.status != "running" or state.stopped:
            return False
        if current_status(session, sender, self.now()).status != "active":
            return False
        if sender in warming_profiles(session) or not self.browser.is_open(sender):
            return False
        parsing = session.scalar(
            select(BrowserQueue.job_id)
            .join(SearchJob)
            .where(BrowserQueue.profile_id == sender, SearchJob.status.in_(("running", "paused")))
            .limit(1)
        )
        reading = session.scalar(
            select(InboxScanItem.id)
            .join(InboxScan)
            .where(InboxScan.sender_account_id == sender, InboxScanItem.status == "reading")
            .limit(1)
        )
        if parsing or reading:
            return False
        initial = list(
            session.scalars(
                select(OutboundMessageJob)
                .join(OutreachCampaign)
                .where(
                    OutboundMessageJob.sender_account_id == sender,
                    OutboundMessageJob.status.in_(("pending", "ready", "sending")),
                    OutreachCampaign.status == "running",
                )
            )
        )
        followups = list(
            session.scalars(
                select(FollowUpJob)
                .join(FollowUpRun)
                .where(
                    FollowUpJob.sender_account_id == sender,
                    FollowUpRun.status == "active",
                    FollowUpJob.status.in_(("pending", "sending")),
                )
            )
        )
        jobs = [*initial, *followups]
        if any(job.status == "sending" for job in jobs):
            return False
        row = current_status(session, sender, self.now())
        pace = outreach_settings(self.settings()).outreach_send_interval_seconds
        ready = (
            as_utc(row.last_sent_at) + timedelta(seconds=pace) if row.last_sent_at else self.now()
        )
        return not any(
            max(as_utc(job.scheduled_at), ready) <= self.now() + MIN_SLACK for job in jobs
        )

    def guard(self, task_id, token):
        with self.sessions() as session:
            task = session.get(InstagramActivityTask, task_id)
            campaign = session.get(OutreachCampaign, task.campaign_id) if task else None
            state = session.get(InstagramActivityState, task.sender_id) if task else None
            return bool(
                task
                and task.status == "running"
                and task.claim_token == token
                and campaign
                and campaign.status == "running"
                and state
                and state.campaign_id == task.campaign_id
                and self._slot(session, task.sender_id)
            )

    def _pick(self):
        with self.sessions.begin() as session:
            senders = self._plan(session)
            active = set(
                session.scalars(
                    select(InstagramActivityTask.sender_id).where(
                        InstagramActivityTask.id.in_(self.claims),
                        InstagramActivityTask.status == "running",
                    )
                )
            )
            earliest = datetime.min.replace(tzinfo=timezone.utc)
            senders.sort(
                key=lambda sender: (
                    sender not in active,
                    as_utc(session.get(InstagramActivityState, sender).last_step_at) or earliest,
                )
            )
            for sender in senders:
                if not self._slot(session, sender):
                    continue
                state = session.get(InstagramActivityState, sender)
                running = session.scalar(
                    select(InstagramActivityTask).where(
                        InstagramActivityTask.sender_id == sender,
                        InstagramActivityTask.status == "running",
                    )
                )
                if running:
                    if as_utc(running.claimed_at) + CLAIM_TTL <= self.now():
                        running.status, running.reason = "interrupted", "deadline"
                        running.finished_at = self.now()
                        self.claims.discard(running.id)
                        self._log(running, "interrupted", "deadline")
                    elif running.id in self.claims:
                        state.last_step_at = self.now()
                        return running
                    else:
                        continue
                pending = list(
                    session.scalars(
                        select(InstagramActivityTask)
                        .where(
                            InstagramActivityTask.sender_id == sender,
                            InstagramActivityTask.campaign_id == state.campaign_id,
                            InstagramActivityTask.status == "pending",
                            InstagramActivityTask.kind.not_in(state.disabled),
                        )
                        .order_by(InstagramActivityTask.id)
                        .limit(200)
                    )
                )
                if not pending:
                    continue
                kinds = list(dict.fromkeys(task.kind for task in pending))
                alternatives = [kind for kind in kinds if kind != state.last_kind]
                kind = self.rng.choice(alternatives or kinds)
                task = self.rng.choice([task for task in pending if task.kind == kind])
                token = uuid.uuid4().hex
                claimed = session.execute(
                    update(InstagramActivityTask)
                    .where(
                        InstagramActivityTask.id == task.id,
                        InstagramActivityTask.status == "pending",
                    )
                    .values(status="running", claim_token=token, claimed_at=self.now())
                ).rowcount
                if claimed:
                    session.refresh(task)
                    self.claims.add(task.id)
                    state.last_kind = kind
                    state.last_step_at = self.now()
                    self._log(task, "started")
                    return task
        return None

    def _finish(self, task, result):
        from ..instagram_activity_browser import canonical

        with self.sessions.begin() as session:
            row = session.get(InstagramActivityTask, task.id)
            if not row or row.status != "running" or row.claim_token != task.claim_token:
                return
            state = session.get(InstagramActivityState, row.sender_id)
            outcome = result.get("outcome")
            if outcome == "pending":
                row.phase = result.get("phase", row.phase)
                return
            reason = str(result.get("reason") or "")[:80]
            row.status = {"done": "done", "skipped": "skipped"}.get(outcome, "failed")
            row.reason, row.finished_at = reason or None, self.now()
            self.claims.discard(row.id)
            self._log(row, row.status, row.reason)
            if reason in ACCOUNT_ERRORS:
                # No timer resumes an account stopped by activity errors.
                set_status(session, row.sender_id, ACCOUNT_ERRORS[reason], reason, until=None)
                state.stopped = True
            elif outcome == "error" or reason == "unsupported":
                state.disabled = list(dict.fromkeys([*state.disabled, row.kind]))
            if outcome == "done":
                choices = {}
                for item in result.get("candidates", [])[:100]:
                    kind = item.get("kind")
                    if kind not in KINDS or kind in state.disabled:
                        continue
                    url = canonical(item.get("url", ""), kind)
                    origin = canonical(item.get("from_url", ""), "navigate")
                    if url:
                        payload = {"url": url, "from_url": origin}
                        choices.setdefault(url, []).append((kind, payload))
                for url, options in choices.items():
                    kind, payload = self.rng.choice(options)
                    self._enqueue(session, row.campaign_id, row.sender_id, kind, url, payload)

    def tick(self, params=None):
        if not self.lock.acquire(blocking=False):
            return {"worked": False}
        try:
            self.reconcile()
            task = self._pick()
            if task is None:
                return {"worked": False}

            def guard():
                return self.guard(task.id, task.claim_token)

            if not guard():
                return {"worked": False}
            step = getattr(self.browser, "activity_step", None)
            try:
                result = (
                    step(task, guard)
                    if callable(step)
                    else {
                        "outcome": "skipped",
                        "reason": "unsupported",
                    }
                )
                if not isinstance(result, dict):
                    result = {"outcome": "error", "reason": "invalid_browser_result"}
            except Exception as error:
                log.warning(
                    "instagram_activity_browser_failed",
                    extra={
                        "error_type": type(error).__name__,
                        "campaign_id": task.campaign_id,
                    },
                )
                result = {"outcome": "error", "reason": "browser_error"}
            self._finish(task, result)
            if task.kind == "story" and result.get("outcome") != "pending":
                close = getattr(self.browser, "activity_close", None)
                if callable(close):
                    close(task.sender_id)
            if result.get("outcome") == "error":
                self.reconcile()
            return {"worked": True, "task_id": task.id, "outcome": result.get("outcome")}
        finally:
            self.lock.release()
