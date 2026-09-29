"""Outreach events (live progress and recent activity) and the audit log.

Events are stored in the caller's transaction, so they commit with the change they
describe. The audit log line carries ids and reasons only: never message text, cookies,
tokens or headers.
"""

import logging
from datetime import timedelta

from sqlalchemy import delete, select

from ..models import OutreachEvent, utcnow

log = logging.getLogger("artist_lead_finder.outreach")

EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "campaign:created": ("name", "total"),
    "campaign:started": ("queued", "skipped"),
    "campaign:paused": (),
    "campaign:resumed": (),
    "campaign:cancelled": ("sent", "cancelled"),
    "campaign:completed": ("sent", "skipped", "failed"),
    "recipient:queued": ("recipient_id", "username", "sender_id"),
    "recipient:sending": ("recipient_id", "username", "sender_id"),
    "recipient:sent": ("recipient_id", "username", "sender_id"),
    "recipient:skipped": ("recipient_id", "username", "reason"),
    "recipient:failed": ("recipient_id", "username", "reason"),
    # Not part of the spec list: a reply marked on a campaign recipient's conversation.
    "recipient:replied": ("recipient_id", "username"),
    "sender:unavailable": ("sender_id", "reason"),
}
RETENTION_DAYS = 90
# Recipient-level events are audit-logged too; campaign events always are.
AUDITED = {name for name in EVENT_FIELDS if name != "recipient:sending"}


def emit(session, campaign_id: int | None, event_type: str, **payload) -> None:
    if event_type not in EVENT_FIELDS:
        raise ValueError(f"Unknown outreach event: {event_type}")
    missing = [key for key in EVENT_FIELDS[event_type] if key not in payload]
    if missing:
        raise ValueError(f"{event_type}: missing {', '.join(missing)}")
    session.add(OutreachEvent(campaign_id=campaign_id, type=event_type, payload=payload))
    if event_type in AUDITED:
        log.info(
            "outreach_" + event_type.replace(":", "_"),
            extra={
                "campaign_id": campaign_id,
                "recipient_id": payload.get("recipient_id"),
                "reason": payload.get("reason"),
            },
        )


def listing(session, campaign_id: int | None, after: int = 0, limit: int = 100) -> list[dict]:
    query = select(OutreachEvent).where(OutreachEvent.id > after)
    if campaign_id:
        query = query.where(OutreachEvent.campaign_id == campaign_id)
    rows = session.scalars(query.order_by(OutreachEvent.id.desc()).limit(min(limit, 500)))
    return [
        {
            "id": row.id,
            "campaign_id": row.campaign_id,
            "type": row.type,
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in reversed(list(rows))
    ]


def prune(session) -> None:
    since = utcnow() - timedelta(days=RETENTION_DAYS)
    session.execute(delete(OutreachEvent).where(OutreachEvent.created_at < since))
