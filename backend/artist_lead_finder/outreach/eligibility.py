"""canSendInitialOutreach: may this lead get an initial message in this campaign?

Checked when a recipient is queued and again by the worker right before sending.
"Previously contacted" uses the global message history and the CRM, not only the
current campaign.
"""

import re
from dataclasses import dataclass

from sqlalchemy import exists, select

from ..models import CampaignRecipient, Conversation, Lead, Message
from . import reasons

USERNAME = re.compile(r"[a-z0-9._]{1,30}")


@dataclass
class Eligibility:
    allowed: bool
    reason: str | None = None
    details: str = ""

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
