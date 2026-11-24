"""Persistent per-sender cycles: five confirmed DMs, Reels, feed, likes."""

import logging
import threading
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from ..instagram_warmup import warming_profiles
from ..models import (
    BrowserQueue,
    FollowUpJob,
    InboxScan,
    InboxScanItem,
    InstagramActivityState,
    InstagramActivityTask,
    OutboundMessageJob,
    OutreachCampaign,
    SearchJob,
    utcnow,
)
from .senders import as_utc, current_status, set_status

log = logging.getLogger("artist_lead_finder.outreach.activity")
DM_BATCH = 5
LIKE_LIMIT = 3
CLAIM_TTL = timedelta(minutes=2)
OPEN = ("pending", "running")
ACTIVE_CAMPAIGN = ("running", "paused")
BROWSING_STAGES = ("reels", "timeline")
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


def _reset_batch(state):
    state.stage, state.sent_in_batch = "send", 0


def note_confirmed_dm(session, sender, campaign_id):
    """Record a confirmation in the same transaction as its Message record."""
    state = session.get(InstagramActivityState, sender)
    owner = session.get(OutreachCampaign, state.campaign_id) if state else None
    if owner is None or owner.status not in ACTIVE_CAMPAIGN:
        campaign = session.get(OutreachCampaign, campaign_id) if campaign_id else None
        if campaign is None or campaign.status not in ACTIVE_CAMPAIGN:
            return
        if state is None:
            state = InstagramActivityState(sender_id=sender, campaign_id=campaign.id)
            session.add(state)
            session.flush()
        else:
            state.campaign_id = campaign.id
            _reset_batch(state)
    if state.stage != "send":
        return
    state.stopped = False
    state.sent_in_batch += 1
    if state.sent_in_batch == DM_BATCH:
        state.stage = "reels"
        state.batch_number += 1
        log.info("instagram_activity_break_started", extra={"campaign_id": state.campaign_id})


def senders_in_break():
    return (
        select(InstagramActivityState.sender_id)
        .join(OutreachCampaign, OutreachCampaign.id == InstagramActivityState.campaign_id)
        .where(InstagramActivityState.stage != "send", OutreachCampaign.status.in_(ACTIVE_CAMPAIGN))
    )


def holds_dm(session, sender):
    return (
        session.scalar(senders_in_break().where(InstagramActivityState.sender_id == sender))
        is not None
    )


class ActivityEngine:
    def __init__(self, sessions, browser, *, now=utcnow):
        self.sessions, self.browser, self.now = sessions, browser, now
        self.lock = threading.Lock()
        self.claims = {}

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

    def _settle(self, task, reason, *, resume=False):
        self.claims.pop(task.id, None)
        if resume and task.kind in BROWSING_STAGES:
            task.status, task.phase, task.claim_token = "pending", "open", None
            self._log(task, "suspended", reason)
            return
        task.status = "interrupted" if task.status == "running" else "cancelled"
        task.reason, task.finished_at = reason, self.now()
        self._log(task, task.status, reason)

    def recover(self):
        with self.sessions.begin() as session:
            for task in session.scalars(
                select(InstagramActivityTask).where(InstagramActivityTask.status == "running")
            ):
                self._settle(task, "restart", resume=True)

    def _block_sender(self, session, sender, reason):
        set_status(session, sender, ACCOUNT_ERRORS[reason], reason, until=None)
        state = session.get(InstagramActivityState, sender)
        if state:
            state.stopped = True
            _reset_batch(state)

    def _close(self, sender):
        close = getattr(self.browser, "activity_close", None)
        return close(sender) if callable(close) else None

    def reconcile(self, *, suspend=False):
        """Pause browsing on stop, app pause, sender errors, or a manual warm-up."""
        closing = []
        with self.sessions.begin() as session:
            warming = warming_profiles(session)
            rows = session.execute(
                select(InstagramActivityState, OutreachCampaign)
                .join(OutreachCampaign, OutreachCampaign.id == InstagramActivityState.campaign_id)
                .where((InstagramActivityState.stage != "send") | InstagramActivityState.stopped)
            )
            for state, campaign in rows:
                sender = state.sender_id
                resume = campaign.status in ACTIVE_CAMPAIGN and not state.stopped
                if (
                    resume
                    and campaign.status == "running"
                    and not suspend
                    and sender not in warming
                    and current_status(session, sender, self.now()).status == "active"
                ):
                    continue
                closing.append(sender)
                for task in session.scalars(
                    select(InstagramActivityTask).where(
                        InstagramActivityTask.sender_id == sender,
                        InstagramActivityTask.campaign_id == campaign.id,
                        InstagramActivityTask.status.in_(OPEN),
                    )
                ):
                    if not resume or task.status == "running":
                        reason = "suspended" if resume else "campaign_stopped"
                        self._settle(task, reason, resume=resume)
                if not resume:
                    _reset_batch(state)
        for sender in closing:
            reason = self._close(sender)
            if reason in ACCOUNT_ERRORS:
                with self.sessions.begin() as session:
                    self._block_sender(session, sender, reason)

    def suspend(self, params=None):
        self.reconcile(suspend=True)
        return {"ok": True}

    def yield_sender(self, sender):
        """Release the background tab and check for a refusal before handing off a DM."""
        reason = self._close(sender)
        if reason in ACCOUNT_ERRORS:
            with self.sessions.begin() as session:
                self._block_sender(session, sender, reason)
            return reason
        return None

    def _enqueue(self, session, state, kind, target, payload):
        return session.execute(
            insert(InstagramActivityTask)
            .values(
                campaign_id=state.campaign_id,
                sender_id=state.sender_id,
                kind=kind,
                target=target,
                payload=payload,
                status="pending",
                phase="open",
                created_at=self.now(),
            )
            .on_conflict_do_nothing(index_elements=["sender_id", "target"])
        ).rowcount

    def _slot(self, session, sender):
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
        sending = session.scalar(
            select(OutboundMessageJob.id)
            .where(
                OutboundMessageJob.sender_account_id == sender,
                OutboundMessageJob.status == "sending",
            )
            .union(
                select(FollowUpJob.id).where(
                    FollowUpJob.sender_account_id == sender, FollowUpJob.status == "sending"
                )
            )
            .limit(1)
        )
        return parsing is None and reading is None and sending is None

    def guard(self, task_id, token):
        with self.sessions() as session:
            task = session.get(InstagramActivityTask, task_id)
            if not task or task.status != "running" or task.claim_token != token:
                return False
            campaign = session.get(OutreachCampaign, task.campaign_id)
            state = session.get(InstagramActivityState, task.sender_id)
            return bool(
                campaign
                and campaign.status == "running"
                and state
                and state.campaign_id == task.campaign_id
                and state.stage == task.kind
                and not state.stopped
                and self._slot(session, task.sender_id)
            )

    def _finish_batch(self, state):
        _reset_batch(state)
        log.info("instagram_activity_break_done", extra={"campaign_id": state.campaign_id})

    def _pick(self):
        with self.sessions.begin() as session:
            states = list(
                session.scalars(
                    select(InstagramActivityState)
                    .join(
                        OutreachCampaign, OutreachCampaign.id == InstagramActivityState.campaign_id
                    )
                    .where(
                        OutreachCampaign.status == "running",
                        InstagramActivityState.stage != "send",
                        InstagramActivityState.stopped.is_(False),
                    )
                    .order_by(InstagramActivityState.last_step_at, InstagramActivityState.sender_id)
                )
            )
            for state in states:
                if not self._slot(session, state.sender_id):
                    continue
                query = select(InstagramActivityTask).where(
                    InstagramActivityTask.sender_id == state.sender_id,
                    InstagramActivityTask.campaign_id == state.campaign_id,
                    InstagramActivityTask.kind == state.stage,
                )
                if state.stage in BROWSING_STAGES:
                    key = f"batch:{state.campaign_id}:{state.batch_number}:{state.stage}"
                    self._enqueue(session, state, state.stage, key, {})
                    query = query.where(InstagramActivityTask.target == key)
                running = session.scalar(query.where(InstagramActivityTask.status == "running"))
                if running:
                    if as_utc(running.claimed_at) + CLAIM_TTL <= self.now():
                        self._settle(running, "deadline", resume=True)
                        session.flush()
                    elif self.claims.get(running.id) == running.claim_token:
                        state.last_step_at = self.now()
                        return running
                    else:
                        continue
                task = session.scalar(
                    query.where(InstagramActivityTask.status == "pending")
                    .order_by(InstagramActivityTask.id)
                    .limit(1)
                )
                if task is None:
                    # The last pending like can have been interrupted by a restart.
                    if state.stage == "like":
                        self._finish_batch(state)
                    continue
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
                    self.claims[task.id] = token
                    state.last_step_at = self.now()
                    self._log(task, "started")
                    return task
        return None

    def _finish(self, task, result):
        from ..instagram_activity_browser import canonical

        with self.sessions.begin() as session:
            row = session.get(InstagramActivityTask, task.id)
            if not row or row.status != "running" or row.claim_token != task.claim_token:
                return False
            state = session.get(InstagramActivityState, row.sender_id)
            outcome = result.get("outcome")
            if outcome == "pending":
                row.phase = result.get("phase", row.phase)
                row.claimed_at = self.now()
                if row.kind in BROWSING_STAGES and "progress" in result:
                    row.payload = {**row.payload, "progress": result["progress"]}
                return False
            reason = str(result.get("reason") or "")[:80]
            row.status = {"done": "done", "skipped": "skipped"}.get(outcome, "failed")
            row.reason, row.finished_at = reason or None, self.now()
            self.claims.pop(row.id, None)
            self._log(row, row.status, row.reason)
            if reason in ACCOUNT_ERRORS:
                self._block_sender(session, row.sender_id, reason)
                return True
            if row.kind == "reels":
                state.stage = "timeline"
                return True
            if row.kind == "timeline":
                queued = 0
                for url in result.get("posts", []) if outcome == "done" else []:
                    post = canonical(url, "like")
                    if post:
                        queued += self._enqueue(session, state, "like", post, {"url": post})
                    if queued >= LIKE_LIMIT:
                        break
                if queued:
                    state.stage = "like"
                else:
                    self._finish_batch(state)
                return True
            remaining = list(
                session.scalars(
                    select(InstagramActivityTask).where(
                        InstagramActivityTask.sender_id == row.sender_id,
                        InstagramActivityTask.campaign_id == row.campaign_id,
                        InstagramActivityTask.kind == "like",
                        InstagramActivityTask.status == "pending",
                    )
                )
            )
            if outcome == "error" or reason == "unsupported":
                for pending in remaining:
                    self._settle(pending, reason)
                remaining = []
            if not remaining:
                self._finish_batch(state)
                return True
            return False

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
                    else {"outcome": "skipped", "reason": "unsupported"}
                )
                if not isinstance(result, dict):
                    result = {"outcome": "error", "reason": "invalid_browser_result"}
            except Exception as error:
                log.warning(
                    "instagram_activity_browser_failed",
                    extra={"error_type": type(error).__name__, "campaign_id": task.campaign_id},
                )
                result = {"outcome": "error", "reason": "browser_error"}
            if self._finish(task, result):
                reason = self._close(task.sender_id)
                if reason in ACCOUNT_ERRORS:
                    with self.sessions.begin() as session:
                        self._block_sender(session, task.sender_id, reason)
            if result.get("outcome") == "error":
                self.reconcile()
            return {"worked": True, "task_id": task.id, "outcome": result.get("outcome")}
        finally:
            self.lock.release()
