"""Primary outreach list: usernames and message variants kept between sessions.

"Рассылка" turns the list into an ordinary campaign (leads found or created by username,
variants given in turn), so eligibility, the queue, pacing and history stay the same.
Usernames already messaged, queued or waiting for review are never put in a new campaign.
"""

import re
from datetime import datetime

from sqlalchemy import select

from ..models import (
    CampaignRecipient,
    Lead,
    OutreachCampaign,
    OutreachTemplate,
    OutreachWorkspace,
    utcnow,
)
from . import events
from .campaigns import MAX_RECIPIENTS, UNFINISHED, CampaignService, refresh_counts, row_dict
from .renderer import MAX_MESSAGE_LENGTH, template_errors

MAX_MESSAGES = 50
USERNAME = re.compile(r"^[a-z0-9_.]{1,30}$")
PROFILE_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]{1,30})/?(?:[?#].*)?$", re.I
)
# Not sent again from the list: delivered, in the queue, or possibly delivered.
DONE = {"sent", "review", "contacted"}
BUSY = {"pending", "queued", "sending"}


def normalize_username(value: object) -> str | None:
    text = str(value).strip()
    match = PROFILE_URL.match(text)
    username = (match.group(1) if match else text.removeprefix("@")).lower()
    return username if USERNAME.match(username) else None


def usernames_from(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Некорректный список аккаунтов.")
    result = []
    for item in value:
        username = normalize_username(item)
        if username is None:
            raise ValueError(f"Некорректный username: {str(item)[:40]}")
        result.append(username)
    result = list(dict.fromkeys(result))
    if len(result) > MAX_RECIPIENTS:
        raise ValueError(f"В списке не больше {MAX_RECIPIENTS} аккаунтов.")
    return result


def messages_from(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Некорректный список сообщений.")
    result = [str(item).strip() for item in value if str(item).strip()]
    if len(result) > MAX_MESSAGES:
        raise ValueError(f"Не больше {MAX_MESSAGES} сообщений.")
    for index, text in enumerate(result, start=1):
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Сообщение №{index} длиннее {MAX_MESSAGE_LENGTH} символов.")
        errors = template_errors(text)
        if errors:
            raise ValueError(f"Сообщение №{index}: {' '.join(errors)}")
    return list(dict.fromkeys(result))


class WorkspaceService:
    def __init__(self, sessions, campaigns: CampaignService):
        self.sessions = sessions
        self.campaigns = campaigns

    @staticmethod
    def _row(session) -> OutreachWorkspace:
        row = session.get(OutreachWorkspace, 1)
        if row is None:
            row = OutreachWorkspace(id=1, usernames=[], messages=[], sender_ids=[])
            session.add(row)
            session.flush()
        return row

    @staticmethod
    def _statuses(session, usernames: list[str]) -> dict[str, dict]:
        """Latest outreach state of each username across all campaigns."""
        result: dict[str, dict] = {}
        for start in range(0, len(usernames), 500):
            chunk = usernames[start : start + 500]
            for recipient in session.scalars(
                select(CampaignRecipient)
                .where(CampaignRecipient.username.in_(chunk))
                .order_by(CampaignRecipient.id)
            ):
                status = "review" if recipient.needs_review else recipient.status
                result[recipient.username] = {
                    "status": status,
                    "reason": recipient.failure_reason or recipient.skip_reason,
                    "details": recipient.reason_details,
                }
            for lead in session.scalars(
                select(Lead).where(Lead.platform == "instagram", Lead.username.in_(chunk))
            ):
                if lead.do_not_contact:
                    result[lead.username] = {
                        "status": "skipped",
                        "reason": "DO_NOT_CONTACT",
                        "details": "Отмечен «Не связываться»",
                    }
                elif lead.last_contacted_at:
                    result.setdefault(
                        lead.username, {"status": "contacted", "reason": None, "details": None}
                    )
        return result

    def state(self, params: dict | None = None) -> dict:
        with self.sessions.begin() as session:
            row = self._row(session)
            statuses = self._statuses(session, row.usernames)
            campaign = session.get(OutreachCampaign, row.campaign_id) if row.campaign_id else None
            return {
                "usernames": [
                    {
                        "username": name,
                        **statuses.get(name, {"status": "new", "reason": None, "details": None}),
                    }
                    for name in row.usernames
                ],
                "messages": row.messages,
                "sender_ids": row.sender_ids,
                "campaign": row_dict(campaign) if campaign else None,
                "running": bool(campaign and campaign.status in UNFINISHED),
            }

    def update(self, params: dict) -> dict:
        usernames = usernames_from(params["usernames"]) if "usernames" in params else None
        messages = messages_from(params["messages"]) if "messages" in params else None
        senders = None
        if "sender_ids" in params:
            senders = (
                self.campaigns._sender_ids(params["sender_ids"]) if params["sender_ids"] else []
            )
        with self.sessions.begin() as session:
            row = self._row(session)
            if usernames is not None:
                row.usernames = usernames
            if messages is not None:
                row.messages = messages
            if senders is not None:
                row.sender_ids = senders
        return self.state()

    def add_usernames(self, names: list[str]) -> int:
        with self.sessions.begin() as session:
            return add_to_list(session, names)

    def add_leads(self, params: dict) -> dict:
        """Leads chosen in the contact base, or every lead with the given CRM statuses."""
        query = select(Lead.username).where(
            Lead.platform == "instagram", Lead.do_not_contact.is_(False)
        )
        if params.get("lead_ids"):
            query = query.where(Lead.id.in_(self.campaigns._lead_ids(params["lead_ids"])))
        elif params.get("statuses"):
            query = query.where(Lead.status.in_([str(s) for s in params["statuses"]]))
        else:
            raise ValueError("Выберите лидов или статусы CRM.")
        with self.sessions() as session:
            names = [n for n in session.scalars(query.order_by(Lead.id)) if USERNAME.match(n)]
        return {"added": self.add_usernames(names)}

    def start(self, params: dict | None = None) -> dict:
        now = utcnow()
        with self.sessions.begin() as session:
            row = self._row(session)
            current = session.get(OutreachCampaign, row.campaign_id) if row.campaign_id else None
            if current and current.status in UNFINISHED:
                raise ValueError("Рассылка уже идёт.")
            if not row.messages:
                raise ValueError("Добавьте хотя бы одно сообщение.")
            if not row.sender_ids:
                raise ValueError("Выберите аккаунт-отправитель в «Настройках».")
            statuses = self._statuses(session, row.usernames)
            targets = [
                name
                for name in row.usernames
                if statuses.get(name, {}).get("status") not in DONE | BUSY
            ]
            if not targets:
                raise ValueError("В списке нет аккаунтов, которым ещё не писали.")
            leads = {
                lead.username: lead
                for lead in session.scalars(
                    select(Lead).where(Lead.platform == "instagram", Lead.username.in_(targets))
                )
            }
            for name in targets:
                if name not in leads:
                    leads[name] = Lead(
                        platform="instagram",
                        username=name,
                        profile_url=f"https://www.instagram.com/{name}/",
                    )
                    session.add(leads[name])
            template = session.scalar(
                select(OutreachTemplate).where(OutreachTemplate.hidden.is_(True)).limit(1)
            ) or OutreachTemplate(name="Первичная рассылка", hidden=True)
            template.body, template.enabled = row.messages[0], True
            session.add(template)
            session.flush()
            campaign = OutreachCampaign(
                name=f"Первичная рассылка {datetime.now().strftime('%d.%m %H:%M')}",
                template_id=template.id,
                sender_strategy="single" if len(row.sender_ids) == 1 else "round_robin",
                sender_ids=list(row.sender_ids),
                message_variants=list(row.messages),
                created_at=now,
            )
            session.add(campaign)
            session.flush()
            for name in targets:
                session.add(
                    CampaignRecipient(
                        campaign_id=campaign.id, lead_id=leads[name].id, username=name
                    )
                )
            refresh_counts(session, campaign)
            events.emit(
                session,
                campaign.id,
                "campaign:created",
                name=campaign.name,
                total=campaign.total_recipients,
            )
            row.campaign_id = campaign.id
            campaign_id = campaign.id
        try:
            self.campaigns.start({"id": campaign_id})
        except Exception:
            # Nothing was queued (no active sender, …): drop the empty draft.
            with self.sessions.begin() as session:
                session.delete(session.get(OutreachCampaign, campaign_id))
            raise
        return self.state()

    def stop(self, params: dict | None = None) -> dict:
        """Unsent messages are cancelled; the list stays for the next run."""
        with self.sessions.begin() as session:
            row = self._row(session)
            campaign_id = row.campaign_id
            status = session.get(OutreachCampaign, campaign_id).status if campaign_id else None
        if status in UNFINISHED:
            self.campaigns.control({"id": campaign_id, "action": "cancel"})
        return self.state()


def add_to_list(session, names: list[str]) -> int:
    """Appends usernames to the list inside the caller's transaction; returns how many
    were new. Nothing is sent: only a started «Рассылка» writes to the list."""
    row = WorkspaceService._row(session)
    names = [name for name in map(normalize_username, names) if name]
    merged = list(dict.fromkeys([*row.usernames, *names]))[:MAX_RECIPIENTS]
    added = len(merged) - len(row.usernames)
    row.usernames = merged
    return added
