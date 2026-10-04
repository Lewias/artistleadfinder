"""OutreachWorker: hands due jobs to the sender's browser window, one at a time, and
records the result.

The desktop shell polls `next_job`; the chosen job is claimed (ready → sending, with a
token) in the same transaction that re-checks it. The shell opens the recipient's profile
in the sender's browser window, sends the message through Instagram's interface there
(`direct_message`) and returns the result to `commit`.

No duplicate messages:
- one job and at most one message per idempotency key initial-outreach:<campaign>:<lead>
  (both unique in the database);
- a claim is a conditional update, so two workers cannot take one job;
- a result is accepted only for the job's current claim token;
- a job left in `sending` (crash, lost result, timeout) is never resent blindly: when
  no message record exists it is marked failed with `needs_review`, and the lead is
  blocked until the user checks the Instagram thread.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select, update

from ..errors import UserError
from ..models import (
    BrowserQueue,
    CampaignRecipient,
    Conversation,
    Lead,
    LeadScoutProfile,
    Message,
    OutboundMessageJob,
    OutreachCampaign,
    SearchJob,
    utcnow,
)
from . import events, reasons
from .campaigns import refresh_counts, skip_recipient
from .eligibility import can_send_initial_outreach
from .followups import FollowUpScheduler
from .senders import SenderCheck, availability, sender_row, set_status
from .settings import outreach_settings

log = logging.getLogger("artist_lead_finder.outreach")

# A claim older than this without a result is treated like a crash during the send.
STALE_AFTER = timedelta(minutes=10)
RETRY_BACKOFF_MINUTES = (2, 10, 30, 60)
# Send script error -> (kind, reason, details). Kinds:
#   retry        temporary, nothing was sent: queued again with backoff
#   sender       the sender cannot send: sender stopped, job waits for it
#   failed       recipient-level: not retried
#   unconfirmed  the message may have gone out: failed + needs_review, never resent
OUTCOMES = {
    "network": ("retry", reasons.NETWORK_ERROR, "Сетевая ошибка до отправки"),
    "dialog": ("retry", reasons.NETWORK_ERROR, "Не открылся диалог с получателем"),
    "typing_mismatch": (
        "retry",
        reasons.SEND_ERROR,
        "Текст в поле сообщения не совпал — не отправлено",
    ),
    "no_instagram_tab": (
        "sender",
        reasons.SENDER_UNAVAILABLE,
        "В окне аккаунта не открыт instagram.com",
    ),
    "login": ("sender", reasons.AUTH_REQUIRED, "Instagram требует войти в аккаунт"),
    "checkpoint": ("sender", reasons.CHECKPOINT, "Instagram требует подтверждение (checkpoint)"),
    "rate_limited": ("sender", reasons.RATE_LIMITED, "Instagram ограничил действия аккаунта"),
    "not_found": ("failed", reasons.RECIPIENT_UNAVAILABLE, "Профиль получателя недоступен"),
    "messages_closed": (
        "failed",
        reasons.MESSAGES_CLOSED,
        "В профиле только «Подписаться» — этому аккаунту нельзя написать",
    ),
    "rejected": ("failed", reasons.MESSAGE_REJECTED, "Instagram отклонил сообщение"),
    "bad_request": ("failed", reasons.SEND_ERROR, "Некорректные данные отправки"),
    "unconfirmed": (
        "unconfirmed",
        reasons.SEND_ERROR,
        "Нет ответа Instagram — отправка не подтверждена",
    ),
    "browser": ("unconfirmed", reasons.SEND_ERROR, "Сбой браузера во время отправки"),
}
UNKNOWN_OUTCOME = ("failed", reasons.SEND_ERROR, "Неизвестный ответ Instagram")
SENDER_STATUS = {
    reasons.AUTH_REQUIRED: "auth_required",
    reasons.CHECKPOINT: "checkpoint",
    reasons.RATE_LIMITED: "rate_limited",
    reasons.SENDER_UNAVAILABLE: "paused",
}


def client_context(key: str) -> str:
    """Stable per idempotency key: the same message always carries the same id."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key).int >> 65)


def classify(result: object) -> tuple[str, str | None, str]:
    if not isinstance(result, dict):
        return UNKNOWN_OUTCOME
    if result.get("outcome") == "sent":
        return ("sent", None, "")
    kind, reason, details = OUTCOMES.get(str(result.get("error") or ""), UNKNOWN_OUTCOME)
    # Instagram's HTTP status, so a refusal can be told apart later.
    status = result.get("status")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        details = f"{details} (HTTP {status})"
    expected, actual = result.get("expected_length"), result.get("actual_length")
    if isinstance(expected, int) and isinstance(actual, int):
        details = f"{details} (набрано {actual} из {expected} символов)"
    return kind, reason, details


class OutreachWorker:
    def __init__(
        self,
        sessions,
        settings: Callable[[], dict],
        window_open: Callable[[str], bool],
        followups: FollowUpScheduler | None = None,
        now=utcnow,
    ):
        self.sessions = sessions
        self.settings = settings
        self.window_open = window_open
        self.followups = followups or FollowUpScheduler()
        self.now = now
        # Last sender:unavailable reason per sender, so the feed is not flooded.
        self.notified: dict[str, tuple[str, str]] = {}

    # ---------- Recovery ----------

    def recover(self) -> int:
        """After a restart: every job still `sending` gets its idempotency check."""
        with self.sessions.begin() as session:
            jobs = session.scalars(
                select(OutboundMessageJob).where(OutboundMessageJob.status == "sending")
            ).all()
            for job in jobs:
                self._unconfirmed(session, job, "Приложение закрылось во время отправки")
            return len(jobs)

    def _unconfirmed(self, session, job: OutboundMessageJob, details: str) -> None:
        recipient = session.get(CampaignRecipient, job.recipient_id)
        message = session.scalar(
            select(Message).where(Message.idempotency_key == job.idempotency_key)
        )
        if message is not None:
            # The message was recorded; only the job bookkeeping was lost.
            self._mark_sent(session, job, recipient, message)
            return
        now = self.now()
        job.status, job.last_error, job.claim_token = "failed", details, None
        recipient.status, recipient.failure_reason = "failed", reasons.SEND_ERROR
        recipient.reason_details, recipient.failed_at = details, now
        recipient.needs_review = True
        events.emit(
            session,
            job.campaign_id,
            "recipient:failed",
            recipient_id=recipient.id,
            username=recipient.username,
            reason=reasons.SEND_ERROR,
            details=details,
            needs_review=True,
        )
        refresh_counts(session, session.get(OutreachCampaign, job.campaign_id))

    # ---------- Picking the next job ----------

    def next_job(self) -> dict | None:
        now = self.now()
        settings = outreach_settings(self.settings())
        with self.sessions.begin() as session:
            for job in session.scalars(
                select(OutboundMessageJob).where(
                    OutboundMessageJob.status == "sending",
                    OutboundMessageJob.claimed_at < now - STALE_AFTER,
                )
            ).all():
                self._unconfirmed(session, job, "Нет результата отправки — проверьте диалог")
            self._start_scheduled(session, now)
            running = select(OutreachCampaign.id).where(OutreachCampaign.status == "running")
            session.execute(
                update(OutboundMessageJob)
                .where(
                    OutboundMessageJob.status == "pending",
                    OutboundMessageJob.scheduled_at <= now,
                    OutboundMessageJob.campaign_id.in_(running),
                )
                .values(status="ready")
            )
            busy = set(
                session.scalars(
                    select(BrowserQueue.profile_id)
                    .join(SearchJob, SearchJob.id == BrowserQueue.job_id)
                    .where(SearchJob.status == "running")
                )
            )
            candidates = session.scalars(
                select(OutboundMessageJob)
                .where(
                    OutboundMessageJob.status == "ready",
                    OutboundMessageJob.campaign_id.in_(running),
                )
                .order_by(OutboundMessageJob.scheduled_at, OutboundMessageJob.id)
                .limit(200)
            ).all()
            blocked: set[str] = set()
            for job in candidates:
                sender = job.sender_account_id
                if sender in blocked:
                    continue
                check = availability(
                    session,
                    sender,
                    settings,
                    window_open=self.window_open(sender),
                    busy=sender in busy,
                    now=now,
                )
                if not check.available:
                    blocked.add(sender)
                    if check.reason:
                        self._notify(session, job.campaign_id, sender, check)
                    continue
                self.notified.pop(sender, None)
                picked = self._claim(session, job, settings, now)
                if picked:
                    return picked
        return None

    def _start_scheduled(self, session, now) -> None:
        due = session.scalars(
            select(OutreachCampaign).where(
                OutreachCampaign.status == "scheduled", OutreachCampaign.scheduled_at <= now
            )
        ).all()
        for campaign in due:
            campaign.status, campaign.started_at = "running", now
            events.emit(
                session,
                campaign.id,
                "campaign:started",
                queued=campaign.queued_count,
                skipped=campaign.skipped_count,
            )
            refresh_counts(session, campaign)

    def _notify(self, session, campaign_id: int, sender: str, check: SenderCheck) -> None:
        state = (check.reason, check.details)
        if self.notified.get(sender) == state:
            return
        self.notified[sender] = state
        events.emit(
            session,
            campaign_id,
            "sender:unavailable",
            sender_id=sender,
            reason=check.reason,
            details=check.details,
        )

    def _claim(self, session, job: OutboundMessageJob, settings, now) -> dict | None:
        recipient = session.get(CampaignRecipient, job.recipient_id)
        message = session.scalar(
            select(Message).where(Message.idempotency_key == job.idempotency_key)
        )
        if message is not None:
            self._mark_sent(session, job, recipient, message)
            return None
        check = can_send_initial_outreach(
            session,
            recipient.lead_id,
            job.campaign_id,
            skip_previously_contacted=settings.outreach_skip_previously_contacted,
        )
        if not check.allowed:
            job.status = "cancelled"
            skip_recipient(session, recipient, check.reason, check.details)
            refresh_counts(session, session.get(OutreachCampaign, job.campaign_id))
            return None
        token = uuid.uuid4().hex
        claimed = session.execute(
            update(OutboundMessageJob)
            .where(OutboundMessageJob.id == job.id, OutboundMessageJob.status == "ready")
            .values(
                status="sending",
                claim_token=token,
                claimed_at=now,
                attempt_count=OutboundMessageJob.attempt_count + 1,
            )
        ).rowcount
        if claimed != 1:
            return None
        recipient.status = "sending"
        events.emit(
            session,
            job.campaign_id,
            "recipient:sending",
            recipient_id=recipient.id,
            username=recipient.username,
            sender_id=job.sender_account_id,
        )
        refresh_counts(session, session.get(OutreachCampaign, job.campaign_id))
        lead = session.get(Lead, recipient.lead_id)
        profile = session.get(LeadScoutProfile, lead.id)
        return {
            "job_id": job.id,
            "token": token,
            "profile_id": job.sender_account_id,
            "args": {
                "username": lead.username,
                "user_id": lead.platform_user_id or (profile.instagram_id if profile else None),
                "text": recipient.rendered_message,
                "client_context": client_context(job.idempotency_key),
            },
        }

    # ---------- Results ----------

    def commit(self, params: dict) -> dict:
        job_id, token, result = (
            int(params["job_id"]),
            str(params.get("token", "")),
            params.get("result"),
        )
        try:
            with self.sessions.begin() as session:
                return self._commit(session, job_id, token, result)
        except Exception as error:
            # Nothing of the send was recorded. Never resend: the lead waits for review.
            log.error("outreach_commit_failed", extra={"error_type": type(error).__name__})
            with self.sessions.begin() as session:
                job = session.get(OutboundMessageJob, job_id)
                if job is not None and job.status == "sending" and job.claim_token == token:
                    self._unconfirmed(
                        session, job, "Результат отправки не сохранён — проверьте диалог"
                    )
            return {"ok": False}

    def _commit(self, session, job_id: int, token: str, result) -> dict:
        job = session.get(OutboundMessageJob, job_id)
        if job is None or job.status != "sending" or not token or job.claim_token != token:
            # A late or repeated result of a job that was already settled.
            return {"ok": True, "ignored": True}
        recipient = session.get(CampaignRecipient, job.recipient_id)
        campaign = session.get(OutreachCampaign, job.campaign_id)
        kind, reason, details = classify(result)
        now = self.now()
        job.claim_token = None
        if kind == "sent":
            self._record_sent(session, job, recipient, campaign, result, now)
            return {"ok": True, "outcome": "sent"}
        job.last_error = details
        if kind == "unconfirmed":
            self._unconfirmed(session, job, details)
            return {"ok": True, "outcome": "unconfirmed"}
        if campaign.status == "cancelled" and kind in {"retry", "sender"}:
            job.status, recipient.status = "cancelled", "cancelled"
            recipient.failure_reason = reasons.CAMPAIGN_CANCELLED
            refresh_counts(session, campaign)
            return {"ok": True, "outcome": "cancelled"}
        settings = outreach_settings(self.settings())
        if kind == "retry" and job.attempt_count < settings.outreach_max_attempts:
            delay = RETRY_BACKOFF_MINUTES[min(job.attempt_count, len(RETRY_BACKOFF_MINUTES)) - 1]
            job.status, job.scheduled_at = "pending", now + timedelta(minutes=delay)
            recipient.status = "queued"
            events.emit(
                session,
                campaign.id,
                "recipient:queued",
                recipient_id=recipient.id,
                username=recipient.username,
                sender_id=job.sender_account_id,
                retry=job.attempt_count,
                reason=reason,
            )
            refresh_counts(session, campaign)
            return {"ok": True, "outcome": "retry"}
        if kind == "sender":
            until = (
                now + timedelta(minutes=settings.outreach_rate_limit_pause_minutes)
                if reason == reasons.RATE_LIMITED
                else None
            )
            set_status(session, job.sender_account_id, SENDER_STATUS[reason], details, until)
            # Not sent and not the recipient's fault: the job waits for this sender.
            job.status, recipient.status = "ready", "queued"
            self.notified[job.sender_account_id] = (reason, details)
            events.emit(
                session,
                campaign.id,
                "sender:unavailable",
                sender_id=job.sender_account_id,
                reason=reason,
                details=details,
            )
            refresh_counts(session, campaign)
            return {"ok": True, "outcome": "sender"}
        job.status = "failed"
        recipient.status, recipient.failure_reason = "failed", reason
        recipient.reason_details, recipient.failed_at = details, now
        events.emit(
            session,
            campaign.id,
            "recipient:failed",
            recipient_id=recipient.id,
            username=recipient.username,
            reason=reason,
            details=details,
        )
        refresh_counts(session, campaign)
        return {"ok": True, "outcome": "failed"}

    def _record_sent(self, session, job, recipient, campaign, result: dict, now) -> None:
        """One transaction: recipient sent + message + conversation + lead + campaign
        metrics + follow-up plan. Any failure rolls all of it back."""
        lead = session.get(Lead, recipient.lead_id)
        conversation = session.scalar(
            select(Conversation).where(
                Conversation.lead_id == lead.id,
                Conversation.platform == "instagram",
                Conversation.sender_account_id == job.sender_account_id,
            )
        )
        if conversation is None:
            conversation = Conversation(
                lead_id=lead.id,
                platform="instagram",
                sender_account_id=job.sender_account_id,
                status="waiting_reply",
                first_outbound_at=now,
            )
            session.add(conversation)
        conversation.last_outbound_at = now
        if conversation.status != "replied":
            conversation.status = "waiting_reply"
        thread = str(result.get("thread_id") or "")[:80]
        if thread:
            conversation.platform_thread_id = thread
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            direction="outbound",
            type="initial",
            body=recipient.rendered_message or "",
            sender_account_id=job.sender_account_id,
            sent_at=now,
            platform_message_id=str(result.get("message_id") or "")[:80] or None,
            campaign_id=campaign.id,
            idempotency_key=job.idempotency_key,
        )
        session.add(message)
        session.flush()
        lead.last_contacted_at, lead.status = now, "contacted"
        user_id = str(result.get("user_id") or "")
        if user_id.isdigit() and not lead.platform_user_id:
            taken = session.scalar(
                select(Lead.id).where(
                    Lead.platform == lead.platform, Lead.platform_user_id == user_id
                )
            )
            if taken is None:
                lead.platform_user_id = user_id
        sender_row(session, job.sender_account_id).last_sent_at = now
        if campaign.followup_sequence_id:
            self.followups.start(
                session,
                conversation_id=conversation.id,
                sequence_id=campaign.followup_sequence_id,
                sent_at=now,
            )
        self._mark_sent(session, job, recipient, message)

    def _mark_sent(self, session, job, recipient, message: Message) -> None:
        job.status, job.sent_at, job.claim_token = "sent", message.sent_at, None
        recipient.status, recipient.sent_at = "sent", message.sent_at
        recipient.message_id, recipient.needs_review = message.id, False
        recipient.failure_reason = recipient.reason_details = None
        events.emit(
            session,
            job.campaign_id,
            "recipient:sent",
            recipient_id=recipient.id,
            username=recipient.username,
            sender_id=job.sender_account_id,
        )
        refresh_counts(session, session.get(OutreachCampaign, job.campaign_id))

    # ---------- Manual review of an unconfirmed send ----------

    def resolve_review(self, params: dict) -> dict:
        """The user checked the Instagram thread: `sent` records the message as sent (no
        platform ids); otherwise the recipient stays failed and the lead is unblocked."""
        sent = bool(params.get("sent"))
        with self.sessions.begin() as session:
            recipient = session.get(CampaignRecipient, int(params["recipient_id"]))
            if recipient is None or not recipient.needs_review:
                raise UserError("Нет отправки, ожидающей проверки.")
            job = session.scalar(
                select(OutboundMessageJob).where(OutboundMessageJob.recipient_id == recipient.id)
            )
            if not sent:
                recipient.needs_review = False
                return {"ok": True}
            campaign = session.get(OutreachCampaign, recipient.campaign_id)
            self._record_sent(session, job, recipient, campaign, {}, self.now())
            return {"ok": True}
