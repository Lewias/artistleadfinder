"""OutreachWorker: hands due jobs to the sender's browser window, one at a time, and
records the result. Initial messages and follow-up steps share this one queue.

The desktop shell polls `next_job`; the chosen job is claimed (ready → sending, with a
token) in the same transaction that re-checks it. The shell opens the recipient's profile
(or a follow-up's thread) in the sender's browser window, sends the message through
Instagram's interface there (`direct_message`) and returns the result to `commit`.

One queue, one pace: a sender's interval and daily limit count every outbound message,
initial or follow-up, and both kinds wait for the same sender health. A due follow-up is
taken before new initial messages of the same sender, so a long initial list never holds
the chains back; a follow-up is never moved to another account.

No duplicate messages:
- one job and at most one message per idempotency key: initial-outreach:<campaign>:<lead>
  or followup:<run>:<step> (both unique in the database);
- a claim is a conditional update, so two workers cannot take one job;
- a result is accepted only for the job's current claim token;
- `guard` lets the browser ask, right before Enter, whether the claim still holds and the
  message may still go out;
- a job left in `sending` (crash, lost result, timeout) is never resent blindly: when no
  message record exists it waits for the user's review, and so does the lead or the
  follow-up chain.
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
    FollowUpJob,
    FollowUpRun,
    Lead,
    LeadScoutProfile,
    Message,
    OutboundMessageJob,
    OutreachCampaign,
    SearchJob,
    utcnow,
)
from . import events, reasons
from .activity import holds_dm, note_confirmed_dm, senders_in_break
from .campaigns import record_reply, refresh_counts, skip_recipient
from .eligibility import can_send_followup, can_send_initial_outreach, followup_previous
from .followups import FollowUpScheduler
from .senders import SenderCheck, as_utc, availability, sender_row, set_status
from .settings import outreach_settings

log = logging.getLogger("artist_lead_finder.outreach")

# A claim older than this without a result is treated like a crash during the send.
STALE_AFTER = timedelta(minutes=10)
RETRY_BACKOFF_MINUTES = (2, 10, 30, 60)
THREAD_URL = "https://www.instagram.com/direct/t/{}/"
PROFILE_URL = "https://www.instagram.com/{}/"
# Send script error -> (kind, reason, details). Kinds:
#   retry        temporary, nothing was sent: queued again with backoff
#   hold         stopped before Enter on purpose (a stop, a new reason not to write):
#                nothing was sent, the job is checked again, no attempt is used up
#   sender       the sender cannot send: sender stopped, job waits for it
#   failed       recipient-level: not retried
#   skip         nothing was sent on purpose: the recipient is skipped, not failed
#   reply        follow-up: the person answered, the chain stops, nothing was sent
#   mismatch     follow-up: the thread does not show our previous message, nothing was
#                sent; the chain waits for the user
#   unconfirmed  the message may have gone out: needs review, never resent
OUTCOMES = {
    "network": ("retry", reasons.NETWORK_ERROR, "Сетевая ошибка до отправки"),
    "dialog": ("retry", reasons.NETWORK_ERROR, "Не открылся диалог с получателем"),
    "wrong_recipient": (
        "retry",
        reasons.SEND_ERROR,
        "Открылся чат другого человека — не отправлено",
    ),
    "typing_mismatch": (
        "retry",
        reasons.SEND_ERROR,
        "Текст в поле сообщения не совпал — не отправлено",
    ),
    "stopped": ("hold", None, "Остановлено до отправки"),
    "no_instagram_tab": (
        "sender",
        reasons.SENDER_UNAVAILABLE,
        "В окне аккаунта не открыт instagram.com",
    ),
    "login": ("sender", reasons.AUTH_REQUIRED, "Instagram требует войти в аккаунт"),
    "session_expired": ("sender", reasons.AUTH_REQUIRED, "Сеанс Instagram истёк — войдите снова"),
    "checkpoint": ("sender", reasons.CHECKPOINT, "Instagram требует подтверждение (checkpoint)"),
    "phone_required": (
        "sender",
        reasons.CHECKPOINT,
        "Instagram просит подтвердить номер телефона",
    ),
    "code_required": ("sender", reasons.CHECKPOINT, "Instagram просит код подтверждения"),
    "password_reset": ("sender", reasons.CHECKPOINT, "Instagram просит сменить пароль"),
    "rate_limited": ("sender", reasons.RATE_LIMITED, "Instagram ограничил действия аккаунта"),
    "action_blocked": (
        "sender",
        reasons.RATE_LIMITED,
        "Instagram временно ограничил действия («Попробуйте позже»)",
    ),
    "not_found": ("failed", reasons.RECIPIENT_UNAVAILABLE, "Профиль получателя недоступен"),
    "messages_closed": (
        "failed",
        reasons.MESSAGES_CLOSED,
        "Instagram не даёт написать этому аккаунту",
    ),
    "existing_thread": (
        "skip",
        reasons.ALREADY_IN_DIRECT,
        "С этим человеком уже есть переписка в Директе — не пишем",
    ),
    "reply_detected": ("reply", reasons.REPLY_RECEIVED, "Получен ответ — follow-up остановлен"),
    "thread_mismatch": (
        "mismatch",
        reasons.THREAD_MISMATCH,
        "В диалоге не видно прошлого сообщения — проверьте диалог вручную",
    ),
    "rejected": ("failed", reasons.MESSAGE_REJECTED, "Instagram отклонил сообщение"),
    "bad_request": ("failed", reasons.SEND_ERROR, "Некорректные данные отправки"),
    "unconfirmed": (
        "unconfirmed",
        reasons.SEND_ERROR,
        "Instagram не показал отправленное сообщение — отправка не подтверждена",
    ),
    "browser": ("unconfirmed", reasons.SEND_ERROR, "Сбой браузера во время отправки"),
}
UNKNOWN_OUTCOME = ("failed", reasons.SEND_ERROR, "Неизвестный ответ Instagram")
EVIDENCE = {
    "no_message": "нового сообщения в чате нет",
    "pending": "сообщение не вышло из состояния отправки",
    "not_cleared": "текст остался в поле",
    "composer_lost": "чат закрылся",
    "unreadable": "страница не читалась",
    "closed": "окно закрылось",
    "enter_failed": "Enter не дошёл до страницы",
}
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
    evidence = EVIDENCE.get(str(result.get("evidence") or ""))
    if kind == "unconfirmed" and evidence:
        details = f"{details}: {evidence}"
    return kind, reason, details


def account_outcome(result: object) -> tuple[str, str] | None:
    """The account problem a send saw beside its result (after Enter): (reason, details)."""
    if not isinstance(result, dict) or not result.get("account"):
        return None
    kind, reason, details = OUTCOMES.get(str(result["account"]), UNKNOWN_OUTCOME)
    return (reason, details) if kind == "sender" else None


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
            steps = session.scalars(
                select(FollowUpJob).where(FollowUpJob.status == "sending")
            ).all()
            for step in steps:
                self._followup_unconfirmed(session, step, "Приложение закрылось во время отправки")
            return len(jobs) + len(steps)

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

    def _followup_unconfirmed(self, session, job: FollowUpJob, details: str) -> None:
        run = session.get(FollowUpRun, job.run_id)
        message = session.scalar(
            select(Message).where(Message.idempotency_key == job.idempotency_key)
        )
        if message is not None:
            self.followups.record_sent(session, run, job, sent_at=as_utc(message.sent_at))
            return
        self.followups.mark_review(session, run, job, details)

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
            for step in session.scalars(
                select(FollowUpJob).where(
                    FollowUpJob.status == "sending", FollowUpJob.claimed_at < now - STALE_AFTER
                )
            ).all():
                self._followup_unconfirmed(
                    session, step, "Нет результата отправки — проверьте диалог"
                )
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
            from ..instagram_warmup import warming_profiles

            busy.update(warming_profiles(session))
            # Due follow-ups first: they are few, each is due once, and a long initial
            # list must not hold them back for days.
            on_break = senders_in_break()
            steps = session.scalars(
                select(FollowUpJob)
                .join(FollowUpRun, FollowUpRun.id == FollowUpJob.run_id)
                .where(
                    FollowUpJob.status == "pending",
                    FollowUpJob.scheduled_at <= now,
                    FollowUpRun.status == "active",
                    FollowUpJob.sender_account_id.not_in(on_break),
                )
                .order_by(FollowUpJob.scheduled_at, FollowUpJob.id)
                .limit(200)
            ).all()
            candidates = session.scalars(
                select(OutboundMessageJob)
                .where(
                    OutboundMessageJob.status == "ready",
                    OutboundMessageJob.campaign_id.in_(running),
                    OutboundMessageJob.sender_account_id.not_in(on_break),
                )
                .order_by(OutboundMessageJob.scheduled_at, OutboundMessageJob.id)
                .limit(200)
            ).all()
            blocked = set(
                session.scalars(
                    select(OutboundMessageJob.sender_account_id)
                    .where(OutboundMessageJob.status == "sending")
                    .union(
                        select(FollowUpJob.sender_account_id).where(FollowUpJob.status == "sending")
                    )
                )
            )
            for job in [*steps, *candidates]:
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
                        campaign_id = (
                            job.campaign_id
                            if isinstance(job, OutboundMessageJob)
                            else session.get(FollowUpRun, job.run_id).campaign_id
                        )
                        self._notify(session, campaign_id, sender, check)
                    continue
                self.notified.pop(sender, None)
                if isinstance(job, FollowUpJob):
                    picked = self._claim_followup(session, job, now)
                else:
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

    def _notify(self, session, campaign_id: int | None, sender: str, check: SenderCheck) -> None:
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
            "kind": "initial",
            "job_id": job.id,
            "token": token,
            "profile_id": job.sender_account_id,
            "args": {
                "username": lead.username,
                "user_id": lead.platform_user_id or (profile.instagram_id if profile else None),
                # Fixed when queued: a retry sends exactly the same text.
                "text": job.body or recipient.rendered_message,
                "mode": "initial",
                "client_context": client_context(job.idempotency_key),
            },
        }

    def _claim_followup(self, session, job: FollowUpJob, now) -> dict | None:
        run = session.get(FollowUpRun, job.run_id)
        message = session.scalar(
            select(Message).where(Message.idempotency_key == job.idempotency_key)
        )
        if message is not None:
            self.followups.record_sent(session, run, job, sent_at=as_utc(message.sent_at))
            return None
        check = can_send_followup(session, job)
        if not check.allowed:
            if check.final:
                self.followups.stop(session, run, check.reason, check.details, now=now)
            return None
        token = uuid.uuid4().hex
        claimed = session.execute(
            update(FollowUpJob)
            .where(FollowUpJob.id == job.id, FollowUpJob.status == "pending")
            .values(
                status="sending",
                claim_token=token,
                claimed_at=now,
                attempt_count=FollowUpJob.attempt_count + 1,
            )
        ).rowcount
        if claimed != 1:
            return None
        conversation = session.get(Conversation, job.conversation_id)
        lead = session.get(Lead, conversation.lead_id)
        previous = [session.get(Message, run.origin_message_id).body]
        for index in range(job.step_index):
            sent = followup_previous(session, run, index + 1)
            if sent is not None:
                previous.append(sent.body)
        known = session.scalars(
            select(Message.body).where(
                Message.conversation_id == conversation.id, Message.direction == "inbound"
            )
        ).all()
        thread = conversation.platform_thread_id or ""
        return {
            "kind": "followup",
            "job_id": job.id,
            "token": token,
            "profile_id": job.sender_account_id,
            "args": {
                "username": lead.username,
                "user_id": lead.platform_user_id,
                "text": job.body,
                "mode": "followup",
                # The same thread when it is known; otherwise the profile's own button.
                "url": THREAD_URL.format(thread) if thread else PROFILE_URL.format(lead.username),
                "previous": [body for body in previous if body],
                "known_inbound": [body for body in known if body],
                "client_context": client_context(job.idempotency_key),
            },
        }

    # ---------- The last check before Enter ----------

    def guard(self, kind: str, job_id: int, token: str) -> str | None:
        """None when the claimed job may still be sent now; otherwise why not. Asked by
        the browser before opening the thread, while typing and right before Enter."""
        settings = outreach_settings(self.settings())
        with self.sessions() as session:
            if kind == "followup":
                job = session.get(FollowUpJob, int(job_id))
                if job is None or job.status != "sending" or job.claim_token != token:
                    return "CLAIM_LOST"
                if holds_dm(session, job.sender_account_id):
                    return "ACTIVITY_BREAK"
                run = session.get(FollowUpRun, job.run_id)
                if run is None or run.status != "active":
                    return (run.stop_reason if run else None) or reasons.USER_STOPPED
                check = can_send_followup(session, job)
                return None if check.allowed else (check.reason or "NOT_ALLOWED")
            job = session.get(OutboundMessageJob, int(job_id))
            if job is None or job.status != "sending" or job.claim_token != token:
                return "CLAIM_LOST"
            if holds_dm(session, job.sender_account_id):
                return "ACTIVITY_BREAK"
            campaign = session.get(OutreachCampaign, job.campaign_id)
            if campaign is None or campaign.status != "running":
                return reasons.CAMPAIGN_CANCELLED
            recipient = session.get(CampaignRecipient, job.recipient_id)
            check = can_send_initial_outreach(
                session,
                recipient.lead_id,
                job.campaign_id,
                skip_previously_contacted=settings.outreach_skip_previously_contacted,
            )
            return None if check.allowed else (check.reason or "NOT_ALLOWED")

    # ---------- Results ----------

    def commit(self, params: dict) -> dict:
        job_id, token, result = (
            int(params["job_id"]),
            str(params.get("token", "")),
            params.get("result"),
        )
        followup = params.get("kind") == "followup"
        try:
            with self.sessions.begin() as session:
                if followup:
                    return self._commit_followup(session, job_id, token, result)
                return self._commit(session, job_id, token, result)
        except Exception as error:
            # Nothing of the send was recorded. Never resend: it waits for review.
            log.error("outreach_commit_failed", extra={"error_type": type(error).__name__})
            with self.sessions.begin() as session:
                model = FollowUpJob if followup else OutboundMessageJob
                job = session.get(model, job_id)
                if job is not None and job.status == "sending" and job.claim_token == token:
                    details = "Результат отправки не сохранён — проверьте диалог"
                    if followup:
                        self._followup_unconfirmed(session, job, details)
                    else:
                        self._unconfirmed(session, job, details)
            return {"ok": False}

    def _sender_problem(
        self, session, sender: str, reason: str, details: str, campaign_id: int | None
    ) -> None:
        settings = outreach_settings(self.settings())
        until = (
            self.now() + timedelta(minutes=settings.outreach_rate_limit_pause_minutes)
            if reason == reasons.RATE_LIMITED
            else None
        )
        set_status(session, sender, SENDER_STATUS[reason], details, until)
        self.notified[sender] = (reason, details)
        events.emit(
            session,
            campaign_id,
            "sender:unavailable",
            sender_id=sender,
            reason=reason,
            details=details,
        )

    def _commit(self, session, job_id: int, token: str, result) -> dict:
        job = session.get(OutboundMessageJob, job_id)
        if job is None or job.status != "sending" or not token or job.claim_token != token:
            # A late or repeated result of a job that was already settled.
            return {"ok": True, "ignored": True}
        recipient = session.get(CampaignRecipient, job.recipient_id)
        campaign = session.get(OutreachCampaign, job.campaign_id)
        kind, reason, details = classify(result)
        if kind in {"reply", "mismatch"}:
            # Follow-up outcomes; an initial send never asks for them.
            kind = "retry"
        now = self.now()
        job.claim_token = None
        # The account's health is kept apart from this message's result.
        problem = account_outcome(result)
        if problem and kind != "sender":
            self._sender_problem(session, job.sender_account_id, *problem, campaign.id)
        if kind == "sent":
            self._record_sent(session, job, recipient, campaign, result, now)
            return {"ok": True, "outcome": "sent"}
        job.last_error = details
        if kind == "unconfirmed":
            self._unconfirmed(session, job, details)
            return {"ok": True, "outcome": "unconfirmed"}
        if campaign.status == "cancelled" and kind in {"retry", "sender", "hold"}:
            job.status, recipient.status = "cancelled", "cancelled"
            recipient.failure_reason = reasons.CAMPAIGN_CANCELLED
            refresh_counts(session, campaign)
            return {"ok": True, "outcome": "cancelled"}
        settings = outreach_settings(self.settings())
        if kind == "hold":
            # Nothing was sent: the job is checked again when it is next due.
            job.status, recipient.status = "ready", "queued"
            job.attempt_count = max(job.attempt_count - 1, 0)
            refresh_counts(session, campaign)
            return {"ok": True, "outcome": "held"}
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
            self._sender_problem(session, job.sender_account_id, reason, details, campaign.id)
            # Not sent and not the recipient's fault: the job waits for this sender.
            job.status, recipient.status = "ready", "queued"
            refresh_counts(session, campaign)
            return {"ok": True, "outcome": "sender"}
        if kind == "skip":
            job.status = "cancelled"
            skip_recipient(session, recipient, reason, details)
            # Already talking: later outreach skips them like anyone written to.
            lead = session.get(Lead, recipient.lead_id)
            if lead is not None and lead.status != "rejected":
                lead.status = "contacted"
            refresh_counts(session, campaign)
            return {"ok": True, "outcome": "skipped"}
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

    def _commit_followup(self, session, job_id: int, token: str, result) -> dict:
        job = session.get(FollowUpJob, job_id)
        if job is None or job.status != "sending" or not token or job.claim_token != token:
            return {"ok": True, "ignored": True}
        run = session.get(FollowUpRun, job.run_id)
        kind, reason, details = classify(result)
        if kind == "skip":
            kind, reason = "failed", reasons.SEND_ERROR
        now = self.now()
        job.claim_token = None
        problem = account_outcome(result)
        if problem and kind != "sender":
            self._sender_problem(session, job.sender_account_id, *problem, run.campaign_id)
        if kind == "sent":
            self.followups.record_sent(session, run, job, sent_at=now, result=result)
            note_confirmed_dm(session, job.sender_account_id, run.campaign_id)
            return {"ok": True, "outcome": "sent"}
        job.last_error = details
        if kind == "unconfirmed":
            self.followups.mark_review(session, run, job, details)
            return {"ok": True, "outcome": "unconfirmed"}
        if kind == "reply":
            # Seen before typing: this step was not sent and never will be.
            conversation = session.get(Conversation, job.conversation_id)
            replies = result.get("replies") if isinstance(result, dict) else None
            bodies = [str(text)[:4000] for text in replies or [] if str(text).strip()]
            job.status, job.cancel_reason = "cancelled", reasons.REPLY_RECEIVED
            record_reply(session, conversation, bodies, now, self.followups)
            return {"ok": True, "outcome": "replied"}
        if kind in {"hold", "mismatch", "sender"}:
            # Nothing was sent; the step waits (no attempt used up).
            job.status = "pending"
            job.attempt_count = max(job.attempt_count - 1, 0)
            if kind == "mismatch" and run.status == "active":
                run.status, run.stop_reason, run.reason_details = "paused", reason, details
            elif kind == "sender":
                self._sender_problem(
                    session, job.sender_account_id, reason, details, run.campaign_id
                )
            else:
                check = can_send_followup(session, job)
                if not check.allowed and check.final:
                    self.followups.stop(session, run, check.reason, check.details, now=now)
            return {"ok": True, "outcome": "held" if kind == "hold" else kind}
        settings = outreach_settings(self.settings())
        if kind == "retry" and job.attempt_count < settings.outreach_max_attempts:
            delay = RETRY_BACKOFF_MINUTES[min(job.attempt_count, len(RETRY_BACKOFF_MINUTES)) - 1]
            job.status, job.scheduled_at = "pending", now + timedelta(minutes=delay)
            return {"ok": True, "outcome": "retry"}
        job.status = "failed"
        self.followups.stop(session, run, reason, details, status="failed", now=now)
        return {"ok": True, "outcome": "failed"}

    def _record_sent(self, session, job, recipient, campaign, result: dict, now) -> None:
        """One transaction: recipient sent + message + conversation + lead + campaign
        metrics + the follow-up run. Any failure rolls all of it back."""
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
        last = as_utc(conversation.last_outbound_at)
        if last is None or last < now:
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
            body=job.body or recipient.rendered_message or "",
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
        sender = sender_row(session, job.sender_account_id)
        if sender.last_sent_at is None or as_utc(sender.last_sent_at) < now:
            sender.last_sent_at = now
        if conversation.status == "waiting_reply":
            self.followups.start(
                session, conversation=conversation, origin=message, campaign=campaign, sent_at=now
            )
        note_confirmed_dm(session, job.sender_account_id, campaign.id)
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
        """The user checked the Instagram thread: `sent` records the message as sent at
        the time it was attempted (no platform ids), and a follow-up chain starts from
        that time; otherwise the recipient stays failed and the lead is unblocked."""
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
            at = as_utc(job.claimed_at) or self.now()
            self._record_sent(session, job, recipient, campaign, {}, at)
            return {"ok": True}

    def resolve_followup(self, params: dict) -> dict:
        """The user checked an unconfirmed follow-up step: `sent` records it at the time it
        was attempted and the chain goes on from there; otherwise `retry` sends the same
        step again (once more through the queue), or the chain stops."""
        sent, retry = bool(params.get("sent")), bool(params.get("retry"))
        now = self.now()
        with self.sessions.begin() as session:
            job = session.get(FollowUpJob, int(params["job_id"]))
            if job is None or job.status != "unconfirmed":
                raise UserError("Нет шага, ожидающего проверки.")
            run = session.get(FollowUpRun, job.run_id)
            if sent:
                self.followups.record_sent(session, run, job, sent_at=as_utc(job.claimed_at) or now)
                note_confirmed_dm(session, job.sender_account_id, run.campaign_id)
                return {"ok": True}
            if retry:
                if run.status not in {"review", "active", "paused"}:
                    raise UserError("Цепочка уже остановлена — повторить шаг нельзя.")
                job.status, job.scheduled_at, job.last_error = "pending", now, None
                job.attempt_count = 0
                if run.status == "review":
                    run.status, run.stop_reason, run.reason_details = "active", None, None
                return {"ok": True}
            job.status, job.cancel_reason = "cancelled", reasons.USER_STOPPED
            self.followups.stop(session, run, reasons.USER_STOPPED, now=now)
            return {"ok": True}
