"""Forget what the parser, the Instagram outreach and iMessage remember about past work.

Leads, their details and where they were found stay, and so do templates, the outreach
and iMessage lists, sender accounts and the CRM. Nothing runs while it is reset: an
active run or campaign has to be stopped first.
"""

from sqlalchemy import delete, select, update

from .errors import UserError
from .models import (
    BrowserQueue,
    CampaignRecipient,
    Conversation,
    FollowUpJob,
    IMessageCampaign,
    IMessageEvent,
    IMessageJob,
    Lead,
    LeadSource,
    Message,
    OutboundMessageJob,
    OutreachCampaign,
    OutreachEvent,
    OutreachSender,
    OutreachWorkspace,
    ScoutAccount,
    ScoutAIClassification,
    ScoutDecision,
    ScoutEvent,
    ScoutPendingAIJob,
    ScoutPost,
    ScoutProcessedPost,
    ScoutProcessedProfile,
    ScoutProfileCache,
    ScoutRun,
    ScoutSource,
    ScoutState,
    SearchJob,
    utcnow,
)
from .outreach.campaigns import UNFINISHED

IMESSAGE_ACTIVE = ("running", "paused")


def reset_scout(session) -> dict:
    """Processed profiles and publications, caches, run history, source cooldowns and the
    rotation cursor. Searches that found a lead stay in the database (the lead points to
    them) but leave the history."""
    if session.scalar(select(SearchJob.id).where(SearchJob.status.in_(("queued", "running")))):
        raise UserError("Остановите парсинг на всех аккаунтах, затем сбросьте память.")
    now = utcnow()
    # Paused and interrupted runs are dropped with the rest of the history.
    session.execute(
        update(SearchJob)
        .where(SearchJob.status == "paused")
        .values(status="cancelled", completed_at=now)
    )
    for model in (
        ScoutEvent,
        ScoutDecision,
        ScoutRun,
        BrowserQueue,
        ScoutProcessedProfile,
        ScoutProcessedPost,
        ScoutProfileCache,
        ScoutAIClassification,
        ScoutPendingAIJob,
        ScoutPost,
        ScoutState,
    ):
        session.execute(delete(model))
    with_leads = select(LeadSource.search_job_id)
    removed = session.execute(delete(SearchJob).where(SearchJob.id.not_in(with_leads))).rowcount
    session.execute(update(SearchJob).values(hidden=True))
    session.execute(
        update(ScoutSource).values(
            last_scanned_at=None,
            status="new",
            leads_found=0,
            candidates_found=0,
            profiles_resolved=0,
            profiles_skipped=0,
            errors_count=0,
        )
    )
    session.execute(update(ScoutAccount).values(found=0))
    return {"searches_removed": removed}


def reset_outreach(session) -> dict:
    """Campaigns, queues, sent messages, follow-ups and sender health, and the marks that
    a lead was already written to: the next outreach may write to the same people."""
    if session.scalar(select(OutreachCampaign.id).where(OutreachCampaign.status.in_(UNFINISHED))):
        raise UserError("Остановите рассылку, затем сбросьте память.")
    session.execute(update(OutreachWorkspace).values(campaign_id=None))
    for model in (
        FollowUpJob,
        OutboundMessageJob,
        CampaignRecipient,
        OutreachEvent,
        Message,
        Conversation,
        OutreachCampaign,
        OutreachSender,
    ):
        session.execute(delete(model))
    contacted = session.execute(
        update(Lead).where(Lead.status == "contacted").values(status="new")
    ).rowcount
    session.execute(update(Lead).values(last_contacted_at=None))
    return {"leads_reopened": contacted}


def reset_imessage(session) -> dict:
    """Campaigns, their jobs and the log: numbers already sent to may be sent to again."""
    if session.scalar(
        select(IMessageCampaign.id).where(IMessageCampaign.status.in_(IMESSAGE_ACTIVE))
    ):
        raise UserError("Остановите рассылку iMessage, затем сбросьте память.")
    for model in (IMessageEvent, IMessageJob, IMessageCampaign):
        session.execute(delete(model))
    return {"ok": True}
