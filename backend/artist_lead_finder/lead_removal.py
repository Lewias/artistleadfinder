"""Remove artists from the artist base.

A removed artist leaves with everything that belongs to it: sources, analysis, the
scout profile, outreach recipients, conversations and messages. CRM contacts stay and
only lose the link. The parser's processed memory keeps the username, so the next run
does not add the same artist back (until the parser memory is reset).
"""

from sqlalchemy import delete, select, update

from .errors import UserError
from .models import (
    CampaignRecipient,
    Conversation,
    CrmContact,
    FollowUpJob,
    Lead,
    LeadAnalysis,
    LeadScoreBreakdown,
    LeadScoutProfile,
    LeadSource,
    Message,
    OutboundMessageJob,
    OutreachCampaign,
    ScoutAssessment,
    ScoutLeadSource,
)
from .outreach.campaigns import UNFINISHED

MAX_AT_ONCE = 5000


def delete_leads(session, ids: list[int]) -> dict:
    ids = sorted({int(value) for value in ids})
    if not ids:
        raise UserError("Выберите артистов для удаления.")
    if len(ids) > MAX_AT_ONCE:
        raise UserError(f"За один раз можно удалить не больше {MAX_AT_ONCE} артистов.")
    busy = session.scalar(
        select(Lead.username)
        .join(CampaignRecipient, CampaignRecipient.lead_id == Lead.id)
        .join(OutreachCampaign, OutreachCampaign.id == CampaignRecipient.campaign_id)
        .where(Lead.id.in_(ids), OutreachCampaign.status.in_(UNFINISHED))
    )
    if busy:
        raise UserError(f"@{busy} в незавершённой рассылке. Остановите её, затем удалите.")
    recipients = select(CampaignRecipient.id).where(CampaignRecipient.lead_id.in_(ids))
    conversations = select(Conversation.id).where(Conversation.lead_id.in_(ids))
    # Recipients point at their sent message, so they go before the messages.
    session.execute(
        delete(OutboundMessageJob).where(OutboundMessageJob.recipient_id.in_(recipients))
    )
    session.execute(delete(CampaignRecipient).where(CampaignRecipient.lead_id.in_(ids)))
    session.execute(delete(FollowUpJob).where(FollowUpJob.conversation_id.in_(conversations)))
    session.execute(delete(Message).where(Message.conversation_id.in_(conversations)))
    for model in (
        Conversation,
        LeadSource,
        LeadAnalysis,
        LeadScoreBreakdown,
        LeadScoutProfile,
        ScoutLeadSource,
        ScoutAssessment,
    ):
        session.execute(delete(model).where(model.lead_id.in_(ids)))
    session.execute(update(CrmContact).where(CrmContact.lead_id.in_(ids)).values(lead_id=None))
    removed = session.execute(delete(Lead).where(Lead.id.in_(ids))).rowcount
    return {"removed": removed}
