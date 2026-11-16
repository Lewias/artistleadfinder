"""Follow-up chains: from a confirmed initial message to the last step.

A campaign started with a sequence keeps a copy of it (`followup_snapshot`). When an
initial message is confirmed, `FollowUpScheduler.start` begins a run for that
conversation: the step texts are rendered for the lead and fixed in the run. Only the
next step exists as a job (`FollowUpJob`), due `delay_days` after the confirmed send of
the message before it; the step after it is planned when this one is confirmed. So a
chain never sends two steps in a row after the app was closed for days.

The steps go out through the outreach queue (`OutreachWorker`), from the same sender and
into the same thread, under the same limits, pauses and confirmation as initial messages.
An unconfirmed step puts the run in review: nothing moves until the user says whether it
went out. A reply, a stopped conversation or do-not-contact stops the run; a step that
was already handed to the browser is still recorded with its real result.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, text, update

from ..errors import UserError
from ..models import (
    CampaignRecipient,
    Conversation,
    FollowUpJob,
    FollowUpRun,
    FollowUpSequence,
    Lead,
    LeadScoutProfile,
    Message,
    OutreachCampaign,
    OutreachTemplate,
    utcnow,
)
from . import events, reasons
from .renderer import MAX_MESSAGE_LENGTH, MessageTemplateRenderer, lead_variables, template_errors
from .senders import as_utc, sender_row

log = logging.getLogger("artist_lead_finder.outreach")

MAX_STEPS = 5
MAX_DELAY_DAYS = 60
OPEN = ("active", "paused", "review")
FINISHED = ("completed", "stopped", "failed")


def step_key(run_id: int, index: int) -> str:
    return f"followup:{run_id}:{index}"


def flat(value: str) -> str:
    return " ".join(str(value or "").split())


def validate_steps(session, steps: object) -> list[dict]:
    """[{delay_days, text}] from the interface; a step may still name a saved template
    (`template_id`), whose text is copied in."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise UserError(f"В цепочке должно быть от 1 до {MAX_STEPS} шагов.")
    result = []
    for number, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise UserError("Некорректный шаг цепочки.")
        delay = step.get("delay_days")
        if isinstance(delay, bool) or not isinstance(delay, int) or not 1 <= delay <= 60:
            raise UserError(f"Шаг {number}: задержка — от 1 до {MAX_DELAY_DAYS} дней.")
        body = step.get("text")
        if body is None and step.get("template_id") is not None:
            template = (
                session.get(OutreachTemplate, step["template_id"])
                if isinstance(step["template_id"], int)
                else None
            )
            if template is None:
                raise UserError(f"Шаг {number}: шаблон не найден.")
            body = template.body
        body = str(body or "").strip()
        errors = template_errors(body)
        if errors:
            raise UserError(f"Шаг {number}: {' '.join(errors)}")
        if len(body) > MAX_MESSAGE_LENGTH:
            raise UserError(f"Шаг {number}: текст длиннее {MAX_MESSAGE_LENGTH} символов.")
        result.append({"delay_days": delay, "text": body})
    return result


def sequence_snapshot(sequence: FollowUpSequence) -> dict:
    return {
        "sequence_id": sequence.id,
        "version": sequence.version or 1,
        "name": sequence.name,
        "steps": [{"delay_days": s["delay_days"], "text": s["text"]} for s in sequence.steps],
    }


def new_replies(messages: list[dict], ours: list[str], known: list[str]) -> list[str] | None:
    """The person's messages after our first message of the chain, in thread order.
    Our own messages, notices and replies already recorded are not answers. None when
    none of our messages is in the thread: nothing can be said about it."""
    mine = {flat(item) for item in ours if flat(item)}
    seen = {flat(item) for item in known if flat(item)}
    texts = [(flat(item.get("text")), item.get("side")) for item in messages]
    start = next((i for i, (value, _) in enumerate(texts) if value in mine), None)
    if start is None:
        return None
    return [
        value
        for value, side in texts[start + 1 :]
        if side == "in" and value and value not in mine and value not in seen
    ]


class FollowUpScheduler:
    def __init__(self, renderer: MessageTemplateRenderer | None = None):
        self.renderer = renderer or MessageTemplateRenderer()

    # ---------- start ----------

    def start(
        self,
        session,
        *,
        conversation: Conversation,
        origin: Message,
        campaign: OutreachCampaign,
        sent_at: datetime,
    ) -> FollowUpRun | None:
        """A run from a confirmed initial message, inside the send transaction. One run
        per initial message; a second call returns the first run."""
        snapshot = campaign.followup_snapshot or {}
        legacy = False
        if not snapshot.get("steps"):
            if not campaign.followup_sequence_id:
                return None
            # A campaign started before schema 28 chose a sequence: the run waits for the
            # user instead of sending something planned under the old rules.
            sequence = session.get(FollowUpSequence, campaign.followup_sequence_id)
            if sequence is None or not sequence.steps:
                return None
            snapshot, legacy = sequence_snapshot(sequence), True
        existing = session.scalar(
            select(FollowUpRun).where(FollowUpRun.origin_message_id == origin.id)
        )
        if existing is not None:
            return existing
        lead = session.get(Lead, conversation.lead_id)
        steps, errors = self.render(snapshot["steps"], lead, session.get(LeadScoutProfile, lead.id))
        run = FollowUpRun(
            conversation_id=conversation.id,
            origin_message_id=origin.id,
            sender_account_id=conversation.sender_account_id,
            campaign_id=campaign.id,
            sequence_id=snapshot.get("sequence_id"),
            sequence_version=int(snapshot.get("version") or 1),
            sequence_name=str(snapshot.get("name") or "")[:120],
            steps=steps,
            status="active",
            last_sent_at=sent_at,
            created_at=sent_at,
            updated_at=sent_at,
        )
        if errors:
            run.status, run.stop_reason = "failed", reasons.MESSAGE_REJECTED
            run.reason_details, run.finished_at = errors[0][:300], sent_at
        elif legacy:
            run.status, run.stop_reason = "paused", reasons.LEGACY
            run.reason_details = "Цепочка выбрана до обновления — проверьте и запустите"
        session.add(run)
        session.flush()
        if run.status == "active":
            self.schedule(session, run)
        return run

    def render(self, steps: list[dict], lead: Lead, profile) -> tuple[list[dict], list[str]]:
        variables = lead_variables(lead, profile)
        rendered, errors = [], []
        for number, step in enumerate(steps, start=1):
            result = self.renderer.render(step["text"], variables)
            if not result.valid:
                errors.append(f"Шаг {number}: {' '.join(result.errors)}")
            rendered.append({"delay_days": int(step["delay_days"]), "text": result.text})
        return rendered, errors

    # ---------- the next step ----------

    def schedule(
        self, session, run: FollowUpRun, not_before: datetime | None = None
    ) -> FollowUpJob | None:
        """The job of the run's next step, due `delay_days` after the last confirmed
        message (or at `not_before`, whichever is later). Completes a run with no steps
        left. A step that already has a job keeps it."""
        if run.next_step >= len(run.steps):
            if run.status in OPEN:
                run.status, run.finished_at = "completed", run.last_sent_at
            return None
        step = run.steps[run.next_step]
        due = as_utc(run.last_sent_at) + timedelta(days=int(step["delay_days"]))
        if not_before is not None and due < not_before:
            due = not_before
        key = step_key(run.id, run.next_step)
        job = session.scalar(select(FollowUpJob).where(FollowUpJob.idempotency_key == key))
        if job is None:
            job = FollowUpJob(
                run_id=run.id,
                conversation_id=run.conversation_id,
                step_index=run.next_step,
                idempotency_key=key,
                sender_account_id=run.sender_account_id,
                body=step["text"],
                scheduled_at=due,
                status="pending",
            )
            session.add(job)
            session.flush()
        elif job.status == "pending":
            job.scheduled_at = due
        return job

    def record_sent(
        self,
        session,
        run: FollowUpRun,
        job: FollowUpJob,
        *,
        sent_at: datetime,
        result: dict | None = None,
    ) -> Message:
        """A confirmed step (by the interface or by the user): the message, the thread,
        the sender's pace, then the next step from this send's time."""
        result = result or {}
        message = session.scalar(
            select(Message).where(Message.idempotency_key == job.idempotency_key)
        )
        conversation = session.get(Conversation, job.conversation_id)
        if message is None:
            message = Message(
                conversation_id=conversation.id,
                direction="outbound",
                type="followup",
                body=job.body,
                sender_account_id=job.sender_account_id,
                sent_at=sent_at,
                platform_message_id=str(result.get("message_id") or "")[:80] or None,
                campaign_id=run.campaign_id,
                idempotency_key=job.idempotency_key,
            )
            session.add(message)
            session.flush()
            last = as_utc(conversation.last_outbound_at)
            if last is None or last < sent_at:
                conversation.last_outbound_at = sent_at
            thread = str(result.get("thread_id") or "")[:80]
            if thread and not conversation.platform_thread_id:
                conversation.platform_thread_id = thread
            lead = session.get(Lead, conversation.lead_id)
            if lead is not None:
                contacted = as_utc(lead.last_contacted_at)
                if contacted is None or contacted < sent_at:
                    lead.last_contacted_at = sent_at
            sender = sender_row(session, job.sender_account_id)
            if sender.last_sent_at is None or as_utc(sender.last_sent_at) < sent_at:
                sender.last_sent_at = sent_at
        job.status, job.sent_at, job.message_id = "sent", message.sent_at, message.id
        job.claim_token, job.last_error = None, None
        if run.next_step == job.step_index:
            run.next_step += 1
            run.last_sent_at = message.sent_at
        events.emit(
            session,
            run.campaign_id,
            "followup:sent",
            run_id=run.id,
            username=self._username(session, run),
            step=job.step_index + 1,
        )
        if run.status == "review" and not self._unresolved(session, run):
            run.status = "active"
        if run.status == "active":
            self.schedule(session, run)
        return message

    @staticmethod
    def _unresolved(session, run: FollowUpRun) -> bool:
        return (
            session.scalar(
                select(FollowUpJob.id).where(
                    FollowUpJob.run_id == run.id, FollowUpJob.status == "unconfirmed"
                )
            )
            is not None
        )

    @staticmethod
    def _username(session, run: FollowUpRun) -> str:
        conversation = session.get(Conversation, run.conversation_id)
        lead = session.get(Lead, conversation.lead_id) if conversation else None
        return lead.username if lead else ""

    def mark_review(self, session, run: FollowUpRun, job: FollowUpJob, details: str) -> None:
        """The step may have gone out: never resent, the chain waits for the user."""
        job.status, job.last_error, job.claim_token = "unconfirmed", details[:300], None
        if run.status in OPEN:
            run.status, run.stop_reason, run.reason_details = "review", reasons.UNCONFIRMED, details
        events.emit(
            session,
            run.campaign_id,
            "followup:review",
            run_id=run.id,
            username=self._username(session, run),
            step=job.step_index + 1,
            details=details,
        )

    # ---------- stop, pause, resume ----------

    def stop(
        self,
        session,
        run: FollowUpRun,
        reason: str,
        details: str = "",
        *,
        status: str = "stopped",
        now: datetime | None = None,
    ) -> int:
        """Ends the run; its unsent steps are cancelled. Returns how many steps will not
        go out. A step already handed to the browser is recorded by its result."""
        if run.status in FINISHED:
            return 0
        now = now or utcnow()
        left = max(len(run.steps) - run.next_step, 0)
        run.status, run.stop_reason = status, reason
        run.reason_details, run.finished_at = details[:300] or None, now
        session.execute(
            update(FollowUpJob)
            .where(FollowUpJob.run_id == run.id, FollowUpJob.status == "pending")
            .values(status="cancelled", cancel_reason=reason)
        )
        events.emit(
            session,
            run.campaign_id,
            "followup:stopped",
            run_id=run.id,
            username=self._username(session, run),
            reason=reason,
            details=details,
        )
        return left

    def cancel(self, session, conversation_id: int, reason: str = reasons.REPLY_RECEIVED) -> int:
        """Stops every open run of the conversation; the number of steps that will not go
        out."""
        runs = session.scalars(
            select(FollowUpRun).where(
                FollowUpRun.conversation_id == conversation_id, FollowUpRun.status.in_(OPEN)
            )
        ).all()
        return sum(self.stop(session, run, reason) for run in runs)

    def pause(self, session, run: FollowUpRun) -> None:
        if run.status != "active":
            raise UserError("Приостановить можно только активную цепочку.")
        run.status, run.stop_reason, run.reason_details = "paused", reasons.USER_PAUSED, None

    def resume(self, session, run: FollowUpRun, now: datetime) -> None:
        """A paused chain goes on; a step that became due while paused waits for the
        queue's pace, it is not sent at once with the others."""
        if run.status != "paused":
            raise UserError("Цепочка не на паузе.")
        if self._unresolved(session, run):
            run.status = "review"
            return
        run.status, run.stop_reason, run.reason_details = "active", None, None
        self.schedule(session, run, not_before=now)


def migrate_schema_28(session) -> None:
    """Sequences get their own texts; follow-ups planned by schema 10 (never sent) become
    paused runs, so nothing goes out until the user starts them."""
    for sequence in session.scalars(select(FollowUpSequence)):
        steps = []
        for step in sequence.steps or []:
            if not isinstance(step, dict):
                continue
            body = step.get("text")
            if not body and step.get("template_id"):
                template = session.get(OutreachTemplate, step["template_id"])
                body = template.body if template else ""
            if body:
                delay = step.get("delay_days")
                delay = delay if isinstance(delay, int) and 1 <= delay <= MAX_DELAY_DAYS else 1
                steps.append({"delay_days": delay, "text": str(body)})
        sequence.steps = steps[:MAX_STEPS]
        sequence.version = sequence.version or 1
        if not steps:
            sequence.enabled = False
    legacy = session.execute(
        text(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'table' AND name = 'outreach_followup_jobs'"
        )
    ).first()
    if legacy is None:
        return
    scheduler = FollowUpScheduler()
    rows = session.execute(
        text(
            "SELECT conversation_id, sequence_id, MIN(step_index) FROM outreach_followup_jobs"
            " WHERE status = 'pending' GROUP BY conversation_id, sequence_id"
        )
    ).all()
    converted = 0
    for conversation_id, sequence_id, first_step in rows:
        conversation = session.get(Conversation, conversation_id)
        sequence = session.get(FollowUpSequence, sequence_id)
        if conversation is None or sequence is None or not sequence.steps:
            continue
        origin = session.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.direction == "outbound",
                Message.type == "initial",
            )
            .order_by(Message.sent_at, Message.id)
        )
        if origin is None:
            continue
        if session.scalar(select(FollowUpRun.id).where(FollowUpRun.origin_message_id == origin.id)):
            continue
        last = session.scalar(
            select(Message.sent_at)
            .where(Message.conversation_id == conversation.id, Message.direction == "outbound")
            .order_by(Message.sent_at.desc())
        )
        lead = session.get(Lead, conversation.lead_id)
        steps, errors = scheduler.render(
            sequence.steps, lead, session.get(LeadScoutProfile, lead.id)
        )
        campaign_id = session.scalar(
            select(CampaignRecipient.campaign_id).where(CampaignRecipient.message_id == origin.id)
        )
        run = FollowUpRun(
            conversation_id=conversation.id,
            origin_message_id=origin.id,
            sender_account_id=conversation.sender_account_id,
            campaign_id=campaign_id,
            sequence_id=sequence.id,
            sequence_version=sequence.version or 1,
            sequence_name=sequence.name,
            steps=steps,
            next_step=min(int(first_step or 0), len(steps)),
            last_sent_at=last or origin.sent_at,
            status="failed" if errors else "paused",
            stop_reason=reasons.MESSAGE_REJECTED if errors else reasons.LEGACY,
            reason_details=(
                errors[0][:300]
                if errors
                else "Цепочка запланирована до обновления — проверьте и запустите"
            ),
        )
        if conversation.status != "waiting_reply":
            run.status = "stopped"
            run.stop_reason = (
                reasons.REPLY_RECEIVED
                if conversation.status == "replied"
                else reasons.CONVERSATION_STOPPED
            )
        session.add(run)
        converted += 1
    session.execute(text("DROP TABLE outreach_followup_jobs"))
    log.info("followups_migrated", extra={"detail": f"{converted} paused runs"})
