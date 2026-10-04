"""Outreach campaigns: audience, templates, preview, lifecycle, replies and CRM view.

Campaign lifecycle: draft → (Start) → scheduled → running ⇄ paused → completed | failed,
or cancelled from any unfinished state. Creating a campaign never sends anything.
"""

from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update

from ..errors import UserError
from ..models import (
    CampaignRecipient,
    Conversation,
    FollowUpJob,
    FollowUpSequence,
    Lead,
    LeadScoutProfile,
    Message,
    OutboundMessageJob,
    OutreachCampaign,
    OutreachTemplate,
    ScoutLeadSource,
    utcnow,
)
from . import events, reasons
from .eligibility import can_send_initial_outreach, initial_key
from .followups import FollowUpScheduler, validate_steps
from .renderer import LeadVariables, MessageTemplateRenderer, template_errors
from .senders import SenderAllocator, as_utc, current_status, sent_counts
from .settings import outreach_settings

MAX_RECIPIENTS = 5000
MAX_SENDERS = 20
UNFINISHED = ("draft", "scheduled", "running", "paused")
OPEN_RECIPIENT = ("pending", "queued", "sending")
CATEGORY = Literal["artist", "producer", "media", "other"]
CRM_STATUS = Literal["new", "reviewed", "qualified", "rejected", "contacted"]


def iso(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.isoformat() if value else None


def row_dict(row) -> dict:
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = iso(value) if isinstance(value, datetime) else value
    return result


def parse_time(value: object) -> datetime | None:
    """ISO time from the interface (with an offset) → aware UTC."""
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise UserError("Время без часового пояса.")
    return parsed.astimezone(timezone.utc)


class AudienceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = Field(default="", max_length=240)
    profile_types: list[CATEGORY] = Field(default_factory=list)
    min_followers: int = Field(default=0, ge=0)
    max_followers: int | None = Field(default=None, ge=0)
    has_email: bool = False
    has_phone: bool = False
    statuses: list[CRM_STATUS] = Field(default_factory=list)
    source_username: str = Field(default="", max_length=40)
    discovery_method: str = Field(default="", max_length=20)
    min_confidence: int = Field(default=0, ge=0, le=100)
    created_from: date | None = None
    created_to: date | None = None
    include_dnc: bool = False
    include_contacted: bool = False
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=50, ge=1, le=200)


def audience_statement(query: AudienceQuery):
    stmt = (
        select(Lead, LeadScoutProfile)
        .outerjoin(LeadScoutProfile, LeadScoutProfile.lead_id == Lead.id)
        .where(Lead.platform == "instagram")
    )
    if query.search:
        term = f"%{query.search.replace('/', '//').replace('%', '/%').replace('_', '/_')}%"
        stmt = stmt.where(
            or_(Lead.username.ilike(term, escape="/"), Lead.display_name.ilike(term, escape="/"))
        )
    if query.profile_types:
        stmt = stmt.where(LeadScoutProfile.profile_type.in_(query.profile_types))
    stmt = stmt.where(Lead.followers >= query.min_followers)
    if query.max_followers is not None:
        stmt = stmt.where(Lead.followers <= query.max_followers)
    if query.has_email:
        stmt = stmt.where(func.json_array_length(LeadScoutProfile.emails) > 0)
    if query.has_phone:
        stmt = stmt.where(func.json_array_length(LeadScoutProfile.phones) > 0)
    if query.statuses:
        stmt = stmt.where(Lead.status.in_(query.statuses))
    if query.source_username:
        name = query.source_username.strip().lstrip("@").lower()
        stmt = stmt.where(
            or_(
                LeadScoutProfile.source_username == name,
                Lead.id.in_(
                    select(ScoutLeadSource.lead_id).where(ScoutLeadSource.source_username == name)
                ),
            )
        )
    if query.discovery_method:
        method = query.discovery_method
        stmt = stmt.where(
            or_(
                LeadScoutProfile.discovery_method == method,
                Lead.id.in_(
                    select(ScoutLeadSource.lead_id).where(
                        ScoutLeadSource.discovery_method == method
                    )
                ),
            )
        )
    if query.min_confidence:
        stmt = stmt.where(LeadScoutProfile.profile_confidence >= query.min_confidence)
    if query.created_from:
        start = datetime.combine(query.created_from, time.min, tzinfo=timezone.utc)
        stmt = stmt.where(Lead.created_at >= start)
    if query.created_to:
        end = datetime.combine(query.created_to, time.min, tzinfo=timezone.utc)
        stmt = stmt.where(Lead.created_at < end + timedelta(days=1))
    if not query.include_dnc:
        stmt = stmt.where(Lead.do_not_contact.is_(False))
    if not query.include_contacted:
        stmt = stmt.where(Lead.last_contacted_at.is_(None), Lead.status != "contacted")
    return stmt.order_by(Lead.created_at.desc(), Lead.id.desc())


def lead_variables(lead: Lead, profile: LeadScoutProfile | None) -> LeadVariables:
    return LeadVariables(
        username=lead.username,
        display_name=lead.display_name,
        followers=lead.followers or None,
        source_username=profile.source_username if profile else None,
        profile_type=profile.profile_type if profile else None,
    )


def refresh_counts(session, campaign: OutreachCampaign) -> None:
    """Campaign metrics from the recipient rows, in the caller's transaction."""
    session.flush()
    rows = dict(
        session.execute(
            select(CampaignRecipient.status, func.count())
            .where(CampaignRecipient.campaign_id == campaign.id)
            .group_by(CampaignRecipient.status)
        ).all()
    )
    campaign.total_recipients = sum(rows.values())
    campaign.queued_count = rows.get("queued", 0) + rows.get("sending", 0)
    campaign.sent_count = rows.get("sent", 0)
    campaign.skipped_count = rows.get("skipped", 0)
    campaign.failed_count = rows.get("failed", 0)
    campaign.replied_count = session.scalar(
        select(func.count())
        .select_from(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.replied_at.is_not(None),
        )
    )
    if campaign.status == "running" and not any(rows.get(state) for state in OPEN_RECIPIENT):
        failed = campaign.sent_count == 0 and campaign.failed_count > 0
        campaign.status = "failed" if failed else "completed"
        campaign.finished_at = utcnow()
        events.emit(
            session,
            campaign.id,
            "campaign:completed",
            sent=campaign.sent_count,
            skipped=campaign.skipped_count,
            failed=campaign.failed_count,
        )


def skip_recipient(session, recipient: CampaignRecipient, reason: str, details: str) -> None:
    recipient.status, recipient.skip_reason, recipient.reason_details = "skipped", reason, details
    events.emit(
        session,
        recipient.campaign_id,
        "recipient:skipped",
        recipient_id=recipient.id,
        username=recipient.username,
        reason=reason,
        details=details,
    )


class CampaignService:
    def __init__(
        self,
        sessions,
        settings: Callable[[], dict],
        sender_names: Callable[[], dict[str, str]],
        followups: FollowUpScheduler | None = None,
    ):
        self.sessions = sessions
        self.settings = settings
        self.sender_names = sender_names
        self.followups = followups or FollowUpScheduler()
        self.renderer = MessageTemplateRenderer()

    # ---------- Templates and follow-up sequences ----------

    def templates(self, params: dict) -> list[dict]:
        with self.sessions() as session:
            rows = session.scalars(
                select(OutreachTemplate)
                .where(OutreachTemplate.hidden.is_(False))
                .order_by(OutreachTemplate.id.desc())
            )
            return [row_dict(row) for row in rows]

    def save_template(self, params: dict) -> dict:
        name = str(params.get("name", "")).strip()
        body = str(params.get("body", ""))
        if not name or len(name) > 120:
            raise UserError("Укажите название шаблона (до 120 символов).")
        errors = template_errors(body)
        if errors:
            raise ValueError(" ".join(errors))
        with self.sessions.begin() as session:
            if params.get("id"):
                template = session.get(OutreachTemplate, int(params["id"]))
                if template is None:
                    raise UserError("Шаблон не найден.")
            else:
                template = OutreachTemplate()
                session.add(template)
            template.name, template.body = name, body
            template.enabled = bool(params.get("enabled", True))
            session.flush()
            return row_dict(template)

    def render_template(self, params: dict) -> dict:
        """Preview of a template body for one lead (or a sample lead)."""
        body = str(params.get("body", ""))
        with self.sessions() as session:
            if params.get("lead_id"):
                lead = session.get(Lead, int(params["lead_id"]))
                if lead is None:
                    raise UserError("Лид не найден.")
                variables = lead_variables(lead, session.get(LeadScoutProfile, lead.id))
            else:
                variables = LeadVariables("jaycarter", "Jay Carter", 12500, "rapdaily", "artist")
        result = self.renderer.render(body, variables)
        return {
            "text": result.text,
            "valid": result.valid,
            "errors": result.errors,
            "fallbacks": result.fallbacks,
            "length": result.length,
        }

    def sequences(self, params: dict) -> list[dict]:
        with self.sessions() as session:
            rows = session.scalars(select(FollowUpSequence).order_by(FollowUpSequence.id.desc()))
            return [row_dict(row) for row in rows]

    def save_sequence(self, params: dict) -> dict:
        name = str(params.get("name", "")).strip()
        if not name or len(name) > 120:
            raise UserError("Укажите название цепочки (до 120 символов).")
        with self.sessions.begin() as session:
            steps = validate_steps(session, params.get("steps"))
            if params.get("id"):
                sequence = session.get(FollowUpSequence, int(params["id"]))
                if sequence is None:
                    raise UserError("Цепочка не найдена.")
            else:
                sequence = FollowUpSequence()
                session.add(sequence)
            sequence.name, sequence.steps = name, steps
            sequence.enabled = bool(params.get("enabled", True))
            session.flush()
            return row_dict(sequence)

    # ---------- Audience and preview ----------

    def audience(self, params: dict) -> dict:
        query = AudienceQuery.model_validate(params)
        with self.sessions() as session:
            stmt = audience_statement(query)
            total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
            ids = list(
                session.scalars(
                    select(Lead.id)
                    .where(Lead.id.in_(stmt.with_only_columns(Lead.id).order_by(None)))
                    .limit(MAX_RECIPIENTS)
                )
            )
            rows = session.execute(
                stmt.offset((query.page - 1) * query.page_size).limit(query.page_size)
            ).all()
            return {
                "total": total,
                "ids": ids,
                "items": [self._audience_item(lead, profile) for lead, profile in rows],
            }

    @staticmethod
    def _audience_item(lead: Lead, profile: LeadScoutProfile | None) -> dict:
        return {
            "id": lead.id,
            "username": lead.username,
            "display_name": lead.display_name,
            "followers": lead.followers,
            "status": lead.status,
            "do_not_contact": lead.do_not_contact,
            "contacted": lead.last_contacted_at is not None or lead.status == "contacted",
            "created_at": iso(lead.created_at),
            "profile_type": profile.profile_type if profile else None,
            "confidence": profile.profile_confidence if profile else None,
            "email": (profile.emails or [None])[0] if profile else None,
            "phone": (profile.phones or [None])[0] if profile else None,
            "source_username": profile.source_username if profile else None,
            "discovery_method": profile.discovery_method if profile else None,
        }

    def preview(self, params: dict) -> dict:
        """What Start would do: eligibility, sender allocation and rendered examples. Takes
        leads, senders and template, or a draft `campaign_id`."""
        if params.get("campaign_id"):
            with self.sessions() as session:
                campaign = self._campaign(session, {"id": params["campaign_id"]})
                lead_ids = list(
                    session.scalars(
                        select(CampaignRecipient.lead_id)
                        .where(
                            CampaignRecipient.campaign_id == campaign.id,
                            CampaignRecipient.status == "pending",
                        )
                        .order_by(CampaignRecipient.id)
                    )
                )
                if not lead_ids:
                    raise UserError("В кампании нет получателей, ожидающих проверки.")
                params = {
                    "lead_ids": lead_ids,
                    "sender_ids": campaign.sender_ids,
                    "template_id": campaign.template_id,
                    "limit": params.get("limit", 5),
                }
        lead_ids = self._lead_ids(params.get("lead_ids"))
        sender_ids = self._sender_ids(params.get("sender_ids"))
        limit = min(int(params.get("limit", 5)), 20)
        settings = outreach_settings(self.settings())
        names = self.sender_names()
        with self.sessions() as session:
            body = self._template_body(session, params)
            active = [s for s in sender_ids if current_status(session, s).status == "active"]
            allocator = SenderAllocator(active) if active else None
            skipped: Counter = Counter()
            examples, eligible, invalid = [], 0, 0
            for lead_id in lead_ids:
                check = can_send_initial_outreach(
                    session,
                    lead_id,
                    0,
                    skip_previously_contacted=settings.outreach_skip_previously_contacted,
                )
                if not check.allowed:
                    skipped[check.reason] += 1
                    continue
                lead = session.get(Lead, lead_id)
                rendered = self.renderer.render(
                    body, lead_variables(lead, session.get(LeadScoutProfile, lead_id))
                )
                if not rendered.valid:
                    invalid += 1
                    skipped[reasons.MESSAGE_REJECTED] += 1
                    continue
                eligible += 1
                sender = allocator.allocate(session, lead_id, sender_ids) if allocator else None
                if len(examples) < limit:
                    examples.append(
                        {
                            "lead_id": lead_id,
                            "username": lead.username,
                            "display_name": lead.display_name,
                            "sender_id": sender,
                            "sender_name": names.get(sender or "", "—"),
                            "message": rendered.text,
                            "fallbacks": rendered.fallbacks,
                        }
                    )
            session.rollback()
        return {
            "total": len(lead_ids),
            "eligible": eligible,
            "skipped": dict(skipped),
            "invalid_messages": invalid,
            "accounts": len(sender_ids),
            "active_accounts": len(active),
            "estimated_queued": eligible if active else 0,
            "examples": examples,
        }

    # ---------- Lifecycle ----------

    def create(self, params: dict) -> dict:
        name = str(params.get("name", "")).strip()
        if not name or len(name) > 160:
            raise UserError("Укажите название кампании (до 160 символов).")
        lead_ids = self._lead_ids(params.get("lead_ids"))
        sender_ids = self._sender_ids(params.get("sender_ids"))
        scheduled_at = parse_time(params.get("scheduled_at"))
        with self.sessions.begin() as session:
            template = session.get(OutreachTemplate, int(params.get("template_id") or 0))
            if template is None or not template.enabled:
                raise UserError("Выберите включённый шаблон сообщения.")
            sequence_id = params.get("followup_sequence_id")
            if sequence_id:
                sequence = session.get(FollowUpSequence, int(sequence_id))
                if sequence is None or not sequence.enabled:
                    raise UserError("Цепочка follow-up не найдена.")
            leads = {
                lead.id: lead for lead in session.scalars(select(Lead).where(Lead.id.in_(lead_ids)))
            }
            if not leads:
                raise UserError("Выбранные лиды не найдены.")
            campaign = OutreachCampaign(
                name=name,
                template_id=template.id,
                followup_sequence_id=int(sequence_id) if sequence_id else None,
                sender_strategy="single" if len(sender_ids) == 1 else "round_robin",
                sender_ids=sender_ids,
                scheduled_at=scheduled_at,
            )
            session.add(campaign)
            session.flush()
            for lead_id in lead_ids:
                if lead_id in leads:
                    session.add(
                        CampaignRecipient(
                            campaign_id=campaign.id,
                            lead_id=lead_id,
                            username=leads[lead_id].username,
                        )
                    )
            refresh_counts(session, campaign)
            events.emit(
                session, campaign.id, "campaign:created", name=name, total=campaign.total_recipients
            )
            return row_dict(campaign)

    def start(self, params: dict) -> dict:
        """Eligibility check, sender allocation and queueing; the worker sends later."""
        settings = outreach_settings(self.settings())
        now = utcnow()
        with self.sessions.begin() as session:
            campaign = self._campaign(session, params)
            if campaign.status != "draft":
                raise UserError("Запустить можно только черновик кампании.")
            template = session.get(OutreachTemplate, campaign.template_id)
            if template is None or not template.enabled:
                raise UserError("Шаблон кампании выключен или удалён.")
            active = [
                sender
                for sender in campaign.sender_ids
                if current_status(session, sender, now).status == "active"
            ]
            if not active:
                raise UserError(
                    "Нет активных аккаунтов-отправителей. Проверьте их состояние в «Рассылках»."
                )
            later = campaign.scheduled_at is not None and as_utc(campaign.scheduled_at) > now
            start_at = as_utc(campaign.scheduled_at) if later else now
            allocator = SenderAllocator(active)
            # Variants go to queued recipients in turn; otherwise every lead gets the template.
            bodies = campaign.message_variants or [template.body]
            queued = skipped = 0
            recipients = session.scalars(
                select(CampaignRecipient)
                .where(
                    CampaignRecipient.campaign_id == campaign.id,
                    CampaignRecipient.status == "pending",
                )
                .order_by(CampaignRecipient.id)
            ).all()
            for recipient in recipients:
                check = can_send_initial_outreach(
                    session,
                    recipient.lead_id,
                    campaign.id,
                    skip_previously_contacted=settings.outreach_skip_previously_contacted,
                )
                if not check.allowed:
                    skip_recipient(session, recipient, check.reason, check.details)
                    skipped += 1
                    continue
                lead = session.get(Lead, recipient.lead_id)
                rendered = self.renderer.render(
                    bodies[queued % len(bodies)],
                    lead_variables(lead, session.get(LeadScoutProfile, lead.id)),
                )
                if not rendered.valid:
                    skip_recipient(
                        session, recipient, reasons.MESSAGE_REJECTED, " ".join(rendered.errors)
                    )
                    skipped += 1
                    continue
                sender = allocator.allocate(session, lead.id, campaign.sender_ids)
                recipient.status, recipient.queued_at = "queued", now
                recipient.sender_account_id = sender
                recipient.rendered_message = rendered.text
                session.add(
                    OutboundMessageJob(
                        campaign_id=campaign.id,
                        recipient_id=recipient.id,
                        idempotency_key=initial_key(campaign.id, lead.id),
                        sender_account_id=sender,
                        scheduled_at=start_at,
                        status="pending" if later else "ready",
                    )
                )
                events.emit(
                    session,
                    campaign.id,
                    "recipient:queued",
                    recipient_id=recipient.id,
                    username=recipient.username,
                    sender_id=sender,
                )
                queued += 1
            campaign.status = "scheduled" if later else "running"
            if not later:
                campaign.started_at = now
                events.emit(
                    session, campaign.id, "campaign:started", queued=queued, skipped=skipped
                )
            refresh_counts(session, campaign)
            return row_dict(campaign)

    def control(self, params: dict) -> dict:
        action = params.get("action")
        with self.sessions.begin() as session:
            campaign = self._campaign(session, params)
            if action == "pause":
                if campaign.status not in {"running", "scheduled"}:
                    raise UserError("Приостановить можно только запущенную кампанию.")
                campaign.status = "paused"
                events.emit(session, campaign.id, "campaign:paused")
            elif action == "resume":
                if campaign.status != "paused":
                    raise UserError("Кампания не на паузе.")
                waiting = (
                    campaign.started_at is None
                    and campaign.scheduled_at is not None
                    and as_utc(campaign.scheduled_at) > utcnow()
                )
                campaign.status = "scheduled" if waiting else "running"
                if campaign.started_at is None and not waiting:
                    campaign.started_at = utcnow()
                events.emit(session, campaign.id, "campaign:resumed")
                refresh_counts(session, campaign)
            elif action == "cancel":
                if campaign.status not in UNFINISHED:
                    raise UserError("Кампания уже завершена.")
                self._cancel(session, campaign)
            else:
                raise UserError("Неизвестное действие.")
            return row_dict(campaign)

    def _cancel(self, session, campaign: OutreachCampaign) -> None:
        """Unsent jobs are cancelled; sent messages and history stay. A job already handed
        to the browser finishes and is recorded by its result."""
        session.execute(
            update(OutboundMessageJob)
            .where(
                OutboundMessageJob.campaign_id == campaign.id,
                OutboundMessageJob.status.in_(["pending", "ready"]),
            )
            .values(status="cancelled")
        )
        cancelled = session.execute(
            update(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status.in_(["pending", "queued"]),
            )
            .values(status="cancelled", failure_reason=reasons.CAMPAIGN_CANCELLED)
        ).rowcount
        campaign.status, campaign.finished_at = "cancelled", utcnow()
        refresh_counts(session, campaign)
        events.emit(
            session,
            campaign.id,
            "campaign:cancelled",
            sent=campaign.sent_count,
            cancelled=cancelled,
        )

    # ---------- Views ----------

    def campaigns(self, params: dict) -> list[dict]:
        with self.sessions() as session:
            rows = session.scalars(
                select(OutreachCampaign).order_by(OutreachCampaign.id.desc()).limit(200)
            )
            return [row_dict(row) for row in rows]

    def campaign(self, params: dict) -> dict:
        names = self.sender_names()
        settings = outreach_settings(self.settings())
        with self.sessions.begin() as session:
            campaign = self._campaign(session, params)
            template = session.get(OutreachTemplate, campaign.template_id)
            sequence = (
                session.get(FollowUpSequence, campaign.followup_sequence_id)
                if campaign.followup_sequence_id
                else None
            )
            counts = sent_counts(session, campaign.sender_ids)
            senders = []
            for sender in campaign.sender_ids:
                row = current_status(session, sender)
                senders.append(
                    {
                        "id": sender,
                        "name": names.get(sender, "Удалённый профиль"),
                        "status": row.status,
                        "reason": row.reason,
                        "until": iso(row.until),
                        "sent_24h": counts.get(sender, 0),
                        "daily_limit": settings.outreach_daily_limit_per_sender,
                    }
                )
            return {
                **row_dict(campaign),
                "template": row_dict(template) if template else None,
                "sequence": row_dict(sequence) if sequence else None,
                "senders": senders,
                "reasons": dict(
                    session.execute(
                        select(
                            func.coalesce(
                                CampaignRecipient.failure_reason, CampaignRecipient.skip_reason
                            ),
                            func.count(),
                        )
                        .where(
                            CampaignRecipient.campaign_id == campaign.id,
                            CampaignRecipient.status.in_(["skipped", "failed"]),
                        )
                        .group_by(
                            func.coalesce(
                                CampaignRecipient.failure_reason, CampaignRecipient.skip_reason
                            )
                        )
                    ).all()
                ),
            }

    def recipients(self, params: dict) -> dict:
        names = self.sender_names()
        page = max(int(params.get("page", 1)), 1)
        size = min(max(int(params.get("page_size", 50)), 1), 200)
        status = params.get("status") or ""
        with self.sessions() as session:
            campaign = self._campaign(session, params)
            stmt = (
                select(CampaignRecipient, Lead, LeadScoutProfile)
                .join(Lead, Lead.id == CampaignRecipient.lead_id)
                .outerjoin(LeadScoutProfile, LeadScoutProfile.lead_id == Lead.id)
                .where(CampaignRecipient.campaign_id == campaign.id)
            )
            if status == "replied":
                stmt = stmt.where(CampaignRecipient.replied_at.is_not(None))
            elif status:
                stmt = stmt.where(CampaignRecipient.status == status)
            total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
            rows = session.execute(
                stmt.order_by(CampaignRecipient.id).offset((page - 1) * size).limit(size)
            ).all()
            return {
                "total": total,
                "items": [
                    {
                        **row_dict(recipient),
                        "display_name": lead.display_name,
                        "followers": lead.followers,
                        "profile_type": profile.profile_type if profile else None,
                        "sender_name": names.get(recipient.sender_account_id or "", None),
                    }
                    for recipient, lead, profile in rows
                ],
            }

    def event_list(self, params: dict) -> list[dict]:
        with self.sessions() as session:
            return events.listing(
                session,
                int(params["campaign_id"]) if params.get("campaign_id") else None,
                int(params.get("after", 0)),
                int(params.get("limit", 100)),
            )

    # ---------- CRM: replies, stop, do-not-contact ----------

    def mark_replied(self, params: dict) -> dict:
        """A reply received on a lead's conversation (marked by hand until inbox reading
        exists): conversation → replied, inbound message saved, campaign reply counted,
        pending follow-ups cancelled."""
        lead_id = int(params["lead_id"])
        body = str(params.get("body", ""))[:4000]
        now = utcnow()
        with self.sessions.begin() as session:
            conversations = session.scalars(
                select(Conversation)
                .where(Conversation.lead_id == lead_id)
                .order_by(Conversation.last_outbound_at.desc())
            ).all()
            if not conversations:
                raise UserError("Этому лиду ещё не писали.")
            conversation = conversations[0]
            conversation.status, conversation.last_inbound_at = "replied", now
            session.add(
                Message(
                    conversation_id=conversation.id,
                    direction="inbound",
                    type="reply",
                    body=body,
                    sent_at=now,
                )
            )
            cancelled = sum(self.followups.cancel(session, row.id) for row in conversations)
            recipients = session.scalars(
                select(CampaignRecipient).where(
                    CampaignRecipient.lead_id == lead_id,
                    CampaignRecipient.status == "sent",
                    CampaignRecipient.replied_at.is_(None),
                )
            ).all()
            for recipient in recipients:
                recipient.replied_at = now
                events.emit(
                    session,
                    recipient.campaign_id,
                    "recipient:replied",
                    recipient_id=recipient.id,
                    username=recipient.username,
                )
                refresh_counts(session, session.get(OutreachCampaign, recipient.campaign_id))
            return {"ok": True, "followups_cancelled": cancelled}

    def stop_conversation(self, params: dict) -> dict:
        """The user ended the conversation: never message this lead again."""
        lead_id = int(params["lead_id"])
        with self.sessions.begin() as session:
            rows = session.scalars(
                select(Conversation).where(Conversation.lead_id == lead_id)
            ).all()
            if not rows:
                raise UserError("С этим лидом нет диалога.")
            for row in rows:
                row.status = "stopped"
                self.followups.cancel(session, row.id, "CONVERSATION_STOPPED")
        return {"ok": True}

    def set_do_not_contact(self, params: dict) -> dict:
        lead_id, value = int(params["id"]), bool(params.get("value", True))
        with self.sessions.begin() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                raise UserError("Профиль не найден.")
            lead.do_not_contact = value
            if value:
                for row in session.scalars(
                    select(Conversation).where(Conversation.lead_id == lead_id)
                ):
                    self.followups.cancel(session, row.id, reasons.DO_NOT_CONTACT)
        return {"ok": True, "do_not_contact": value}

    def lead_outreach(self, session, lead: Lead) -> dict:
        """Outreach block of the CRM lead card."""
        names = self.sender_names()
        conversation = session.scalars(
            select(Conversation)
            .where(Conversation.lead_id == lead.id)
            .order_by(Conversation.last_outbound_at.desc())
            .limit(1)
        ).first()
        recipient = session.execute(
            select(CampaignRecipient, OutreachCampaign.name)
            .join(OutreachCampaign, OutreachCampaign.id == CampaignRecipient.campaign_id)
            .where(CampaignRecipient.lead_id == lead.id)
            .order_by(CampaignRecipient.sent_at.desc().nulls_last(), CampaignRecipient.id.desc())
            .limit(1)
        ).first()
        history = []
        if conversation:
            history = [
                {
                    "direction": row.direction,
                    "type": row.type,
                    "body": row.body,
                    "sent_at": iso(row.sent_at),
                }
                for row in session.scalars(
                    select(Message)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(Conversation.lead_id == lead.id)
                    .order_by(Message.sent_at.desc())
                    .limit(20)
                )
            ]
        followups = session.scalar(
            select(func.count())
            .select_from(FollowUpJob)
            .join(Conversation, Conversation.id == FollowUpJob.conversation_id)
            .where(Conversation.lead_id == lead.id, FollowUpJob.status == "pending")
        )
        return {
            "do_not_contact": lead.do_not_contact,
            "contacted": lead.last_contacted_at is not None or lead.status == "contacted",
            "last_contacted_at": iso(lead.last_contacted_at),
            "sender_name": names.get(conversation.sender_account_id) if conversation else None,
            "conversation_status": conversation.status if conversation else None,
            "campaign": (
                {
                    "id": recipient[0].campaign_id,
                    "recipient_id": recipient[0].id,
                    "name": recipient[1],
                    "status": recipient[0].status,
                    "reason": recipient[0].failure_reason or recipient[0].skip_reason,
                    "needs_review": recipient[0].needs_review,
                }
                if recipient
                else None
            ),
            "pending_followups": followups,
            "messages": history,
        }

    # ---------- Helpers ----------

    @staticmethod
    def _campaign(session, params: dict) -> OutreachCampaign:
        campaign = session.get(OutreachCampaign, int(params.get("id") or 0))
        if campaign is None:
            raise UserError("Кампания не найдена.")
        return campaign

    @staticmethod
    def _lead_ids(value: object) -> list[int]:
        if not isinstance(value, list) or not value:
            raise UserError("Выберите хотя бы одного лида.")
        if len(value) > MAX_RECIPIENTS:
            raise UserError(f"В кампании не больше {MAX_RECIPIENTS} получателей.")
        if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value):
            raise UserError("Некорректный список лидов.")
        return list(dict.fromkeys(value))

    def _sender_ids(self, value: object) -> list[str]:
        if not isinstance(value, list) or not value:
            raise UserError("Выберите аккаунт-отправитель.")
        if len(value) > MAX_SENDERS:
            raise UserError(f"Не больше {MAX_SENDERS} аккаунтов.")
        known = self.sender_names()
        ids = list(dict.fromkeys(str(item) for item in value))
        if any(item not in known for item in ids):
            raise UserError("Аккаунт-отправитель не найден.")
        return ids

    @staticmethod
    def _template_body(session, params: dict) -> str:
        if params.get("template_id"):
            template = session.get(OutreachTemplate, int(params["template_id"]))
            if template is None:
                raise UserError("Шаблон не найден.")
            return template.body
        return str(params.get("body", ""))
