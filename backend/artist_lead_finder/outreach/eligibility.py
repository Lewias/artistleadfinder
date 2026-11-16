"""May this message be sent now? Initial outreach and follow-ups have their own rules.

canSendInitialOutreach: may this lead get an initial message in this campaign? Checked
when a recipient is queued, when the worker claims the job and right before Enter.
"Previously contacted" uses the global message history and the CRM, not only the current
campaign. An initial message never goes into an existing thread (checked in the browser).

canSendFollowUp: may this step of this run go out? A follow-up is written into the
existing thread on purpose, so it has its own checks instead of the initial ones: the
same recipient and sender, a confirmed previous message, the step still due and unsent,
no unresolved send, no stop, no do-not-contact, no reply. Checked when the job is
claimed and right before Enter; the browser also checks the thread itself.
"""

import re
from dataclasses import dataclass

from sqlalchemy import exists, select

from ..models import (
    CampaignRecipient,
    Conversation,
    FollowUpJob,
    FollowUpRun,
    Lead,
    Message,
)
from . import reasons

USERNAME = re.compile(r"[a-z0-9._]{1,30}")


@dataclass
class Eligibility:
    allowed: bool
    reason: str | None = None
    details: str = ""
    # Follow-ups: the run cannot go on at all (stop it), not just "not now".
    final: bool = False

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason, "details": self.details}


def initial_key(campaign_id: int, lead_id: int) -> str:
    return f"initial-outreach:{campaign_id}:{lead_id}"


def previously_contacted(session, lead: Lead, own_key: str | None = None) -> bool:
    """Any outbound message to the lead in any campaign or by hand, or a CRM contact mark."""
    if lead.last_contacted_at is not None or lead.status == "contacted":
        return True
    query = (
        select(Message.id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.lead_id == lead.id, Message.direction == "outbound")
    )
    if own_key:
        query = query.where(
            (Message.idempotency_key.is_(None)) | (Message.idempotency_key != own_key)
        )
    return session.scalar(select(exists(query))) is True


def can_send_initial_outreach(
    session,
    lead_id: int,
    campaign_id: int,
    *,
    skip_previously_contacted: bool = True,
    sender_status: str | None = None,
) -> Eligibility:
    lead = session.get(Lead, lead_id)
    if lead is None:
        return Eligibility(False, reasons.RECIPIENT_UNAVAILABLE, "Лид удалён из базы")
    if lead.platform != "instagram":
        return Eligibility(False, reasons.RECIPIENT_UNAVAILABLE, "Не Instagram-профиль")
    if not USERNAME.fullmatch(lead.username or ""):
        return Eligibility(False, reasons.RECIPIENT_UNAVAILABLE, "Нет корректного username")
    if lead.do_not_contact:
        return Eligibility(False, reasons.DO_NOT_CONTACT, "Отмечен «Не связываться»")
    states = set(
        session.scalars(select(Conversation.status).where(Conversation.lead_id == lead.id))
    )
    if "stopped" in states:
        return Eligibility(False, reasons.DO_NOT_CONTACT, "Общение остановлено вручную")
    if "replied" in states:
        return Eligibility(False, reasons.ALREADY_CONTACTED, "Лид уже ответил")
    key = initial_key(campaign_id, lead_id)
    if session.scalar(select(exists().where(Message.idempotency_key == key))):
        return Eligibility(False, reasons.ALREADY_CONTACTED, "Уже отправлено в этой кампании")
    # A send that may have gone out (no result came back) blocks the lead whatever the
    # settings, until the user checks the thread and resolves it.
    unconfirmed = exists().where(
        CampaignRecipient.lead_id == lead.id, CampaignRecipient.needs_review.is_(True)
    )
    if session.scalar(select(unconfirmed)):
        return Eligibility(
            False, reasons.ALREADY_CONTACTED, "Прошлая отправка не подтверждена — проверьте диалог"
        )
    if skip_previously_contacted and previously_contacted(session, lead, key):
        return Eligibility(False, reasons.ALREADY_CONTACTED, "Лиду уже писали раньше")
    if sender_status is not None and sender_status != "active":
        return Eligibility(False, reasons.SENDER_UNAVAILABLE, f"Аккаунт: {sender_status}")
    return Eligibility(True)


def followup_previous(session, run: FollowUpRun, step_index: int) -> Message | None:
    """The confirmed message a step follows: the initial one, or the step before it."""
    if step_index == 0:
        message = session.get(Message, run.origin_message_id)
    else:
        message = session.scalar(
            select(Message).where(Message.idempotency_key == f"followup:{run.id}:{step_index - 1}")
        )
    if message is None or message.direction != "outbound":
        return None
    return message


def can_send_followup(session, job: FollowUpJob) -> Eligibility:
    run = session.get(FollowUpRun, job.run_id)
    if run is None:
        return Eligibility(False, reasons.ORIGIN_MISSING, "Цепочка удалена", final=True)
    if run.status != "active":
        return Eligibility(False, None, "Цепочка не активна")
    if job.step_index != run.next_step or job.step_index >= len(run.steps):
        return Eligibility(False, reasons.SEND_ERROR, "Шаг уже пройден", final=True)
    if session.scalar(select(exists().where(Message.idempotency_key == job.idempotency_key))):
        return Eligibility(False, reasons.ALREADY_CONTACTED, "Шаг уже отправлен", final=True)
    conversation = session.get(Conversation, job.conversation_id)
    if conversation is None or conversation.id != run.conversation_id:
        return Eligibility(False, reasons.ORIGIN_MISSING, "Диалог удалён", final=True)
    if not (job.sender_account_id == run.sender_account_id == conversation.sender_account_id):
        return Eligibility(
            False, reasons.SENDER_MISMATCH, "Диалог ведёт другой аккаунт", final=True
        )
    lead = session.get(Lead, conversation.lead_id)
    if lead is None:
        return Eligibility(False, reasons.RECIPIENT_UNAVAILABLE, "Лид удалён", final=True)
    if lead.do_not_contact:
        return Eligibility(False, reasons.DO_NOT_CONTACT, "Отмечен «Не связываться»", final=True)
    states = set(
        session.scalars(select(Conversation.status).where(Conversation.lead_id == lead.id))
    )
    if "stopped" in states:
        return Eligibility(
            False, reasons.CONVERSATION_STOPPED, "Общение остановлено вручную", final=True
        )
    origin = session.get(Message, run.origin_message_id)
    threads = select(Conversation.id).where(Conversation.lead_id == lead.id)
    replied = select(Message.id).where(
        Message.conversation_id.in_(threads), Message.direction == "inbound"
    )
    if origin is not None:
        replied = replied.where(Message.sent_at >= origin.sent_at)
    if "replied" in states or session.scalar(select(exists(replied))):
        return Eligibility(False, reasons.REPLY_RECEIVED, "Лид ответил", final=True)
    if followup_previous(session, run, job.step_index) is None:
        return Eligibility(
            False, reasons.ORIGIN_MISSING, "Нет подтверждённого прошлого сообщения", final=True
        )
    unresolved = exists().where(
        FollowUpJob.conversation_id == conversation.id,
        FollowUpJob.status == "unconfirmed",
    )
    review = exists().where(
        CampaignRecipient.lead_id == lead.id, CampaignRecipient.needs_review.is_(True)
    )
    if session.scalar(select(unresolved)) or session.scalar(select(review)):
        return Eligibility(
            False, reasons.UNCONFIRMED, "Прошлая отправка не подтверждена — проверьте диалог"
        )
    return Eligibility(True)
