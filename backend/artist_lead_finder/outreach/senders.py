"""Sender account health and allocation.

A sender is a browser profile (existing sessions; no new login flow). Its health lives
in `outreach_senders`: active, paused, auth_required, checkpoint, rate_limited, disabled.
Only an active sender whose browser window is open and idle sends, and only at the
configured pace. Problems stop the sender; jobs wait for it and are never moved to
another account automatically.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..errors import UserError
from ..models import Conversation, Message, OutreachSender, utcnow
from . import reasons
from .settings import OutreachSettings

SENDER_STATUSES = ("active", "paused", "auth_required", "checkpoint", "rate_limited", "disabled")
REASON_OF_STATUS = {
    "paused": reasons.SENDER_UNAVAILABLE,
    "disabled": reasons.SENDER_UNAVAILABLE,
    "auth_required": reasons.AUTH_REQUIRED,
    "checkpoint": reasons.CHECKPOINT,
    "rate_limited": reasons.RATE_LIMITED,
}


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every stored time is UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def sender_row(session, profile_id: str) -> OutreachSender:
    row = session.get(OutreachSender, profile_id)
    if row is None:
        row = OutreachSender(profile_id=profile_id, status="active")
        session.add(row)
        session.flush()
    return row


def set_status(
    session, profile_id: str, status: str, reason: str | None = None, until=None
) -> OutreachSender:
    if status not in SENDER_STATUSES:
        raise ValueError("Unknown sender status")
    row = sender_row(session, profile_id)
    row.status, row.reason, row.until = status, reason, until
    return row


def current_status(session, profile_id: str, now: datetime | None = None) -> OutreachSender:
    """Health row; a rate-limit break that has ended returns the sender to active."""
    now = now or utcnow()
    row = sender_row(session, profile_id)
    if row.status == "rate_limited" and row.until and as_utc(row.until) <= now:
        row.status, row.reason, row.until = "active", None, None
    return row


def sent_last_day(session, profile_id: str, now: datetime) -> list[datetime]:
    rows = session.scalars(
        select(Message.sent_at)
        .where(
            Message.sender_account_id == profile_id,
            Message.direction == "outbound",
            Message.sent_at > now - timedelta(hours=24),
        )
        .order_by(Message.sent_at)
    )
    return [as_utc(value) for value in rows]


@dataclass
class SenderCheck:
    available: bool
    # Structured reason when the sender cannot send at all (not just "not yet").
    reason: str | None = None
    details: str = ""
    # When pacing, not a problem, holds the sender back.
    wait_until: datetime | None = None


def availability(
    session,
    profile_id: str,
    settings: OutreachSettings,
    *,
    window_open: bool,
    busy: bool,
    now: datetime | None = None,
) -> SenderCheck:
    now = now or utcnow()
    row = current_status(session, profile_id, now)
    if row.status != "active":
        return SenderCheck(False, REASON_OF_STATUS[row.status], row.reason or row.status)
    if not window_open:
        return SenderCheck(False, reasons.SENDER_UNAVAILABLE, "Окно браузера аккаунта закрыто")
    if busy:
        return SenderCheck(False, reasons.SENDER_UNAVAILABLE, "В окне аккаунта идёт парсинг")
    if row.last_sent_at:
        ready_at = as_utc(row.last_sent_at) + timedelta(
            seconds=settings.outreach_send_interval_seconds
        )
        if ready_at > now:
            return SenderCheck(False, None, "Пауза между сообщениями", ready_at)
    recent = sent_last_day(session, profile_id, now)
    limit = settings.outreach_daily_limit_per_sender
    if len(recent) >= limit:
        return SenderCheck(
            False,
            None,
            f"Лимит {limit} сообщений за 24 часа",
            recent[-limit] + timedelta(hours=24),
        )
    return SenderCheck(True)


def sent_counts(session, profile_ids: list[str], now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    rows = session.execute(
        select(Message.sender_account_id, func.count())
        .where(
            Message.sender_account_id.in_(profile_ids),
            Message.direction == "outbound",
            Message.sent_at > now - timedelta(hours=24),
        )
        .group_by(Message.sender_account_id)
    )
    return {profile_id: count for profile_id, count in rows}


class SenderAllocator:
    """Round robin over the campaign's available senders, predictable by recipient order.

    A lead that already has a conversation with one of the campaign's senders keeps that
    sender, so one conversation never switches accounts.
    """

    def __init__(self, sender_ids: list[str]):
        if not sender_ids:
            raise UserError("Нет доступных аккаунтов-отправителей.")
        self.sender_ids = list(sender_ids)
        self.position = 0

    def allocate(self, session, lead_id: int, campaign_senders: list[str]) -> str:
        existing = session.scalars(
            select(Conversation.sender_account_id)
            .where(Conversation.lead_id == lead_id, Conversation.platform == "instagram")
            .order_by(Conversation.id)
        ).all()
        for sender in existing:
            if sender in campaign_senders:
                return sender
        sender = self.sender_ids[self.position % len(self.sender_ids)]
        self.position += 1
        return sender
