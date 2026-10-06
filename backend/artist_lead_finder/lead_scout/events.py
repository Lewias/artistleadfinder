"""Scout events and per-profile logs, polled by the interface for live progress."""

import json
import logging
from datetime import timedelta

from sqlalchemy import delete, select

from ..models import ScoutDecision, ScoutEvent, utcnow

# Event type -> payload fields it always carries (run_id is added to every event).
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "scout:run-started": ("sources", "methods"),
    "scout:source-started": ("source",),
    "scout:source-completed": ("source", "leads_found"),
    "scout:candidate-found": ("username", "source", "method"),
    "scout:profile-resolving": ("username", "source"),
    "scout:profile-resolved": ("username", "source", "via", "followers"),
    "scout:classification-started": ("username", "source"),
    "scout:classification-completed": ("username", "source", "category", "confidence"),
    "scout:profile-skipped": ("username", "source", "reason"),
    "scout:lead-created": ("lead_id", "username", "source", "category", "confidence"),
    "scout:lead-updated": ("lead_id", "username", "source"),
    # kind: profile | source | fatal; RATE_LIMITED goes to the scheduler, not a skip.
    "scout:error": ("reason", "kind"),
    "scout:paused": (),
    "scout:resumed": (),
    "scout:cancelled": ("discovered", "analyzed", "leads", "skipped"),
    "scout:completed": ("discovered", "analyzed", "leads", "skipped", "errors"),
    # Discovery log of one source page (posts grid, story, followers list).
    "scout:discovery-page": ("source", "method", "url"),
}
EVENT_TYPES = set(EVENT_FIELDS)
# Names stored before schema 9; listing() returns them under the current names.
LEGACY_TYPES = {
    "scout:start": "scout:run-started",
    "source:start": "scout:source-started",
    "source:done": "scout:source-completed",
    "candidate:found": "scout:candidate-found",
    "profile:analyzing": "scout:profile-resolving",
    "profile:skipped": "scout:profile-skipped",
    "lead:found": "scout:lead-created",
    "scout:pause": "scout:paused",
    "scout:stop": "scout:cancelled",
    "scout:done": "scout:completed",
    "discovery:page": "scout:discovery-page",
}
RETENTION_DAYS = 30
# Also written to the application log, so a user's log file tells why a run stopped.
LOGGED = {
    "scout:run-started",
    "scout:source-completed",
    "scout:error",
    "scout:paused",
    "scout:resumed",
    "scout:cancelled",
    "scout:completed",
}
log = logging.getLogger(__name__)


def emit(session, job_id: int, event_type: str, **payload) -> None:
    """Store a typed event in the caller's transaction, so it commits with the change."""
    if event_type not in EVENT_FIELDS:
        raise ValueError(f"Unknown scout event: {event_type}")
    missing = [key for key in EVENT_FIELDS[event_type] if key not in payload]
    if missing:
        raise ValueError(f"{event_type}: missing {', '.join(missing)}")
    session.add(ScoutEvent(job_id=job_id, type=event_type, payload={"run_id": job_id, **payload}))
    if event_type in LOGGED:
        brief = {key: value for key, value in payload.items() if key != "log"}
        log.log(
            logging.WARNING if event_type == "scout:error" else logging.INFO,
            event_type,
            extra={
                "job_id": job_id,
                "reason": payload.get("reason"),
                "detail": json.dumps(brief, ensure_ascii=False, default=str),
            },
        )


def listing(session, job_id: int | None, after: int = 0, limit: int = 200) -> list[dict]:
    query = select(ScoutEvent).where(ScoutEvent.id > after)
    if job_id:
        query = query.where(ScoutEvent.job_id == job_id)
    rows = session.scalars(query.order_by(ScoutEvent.id.desc()).limit(min(limit, 500)))
    return [
        {
            "id": row.id,
            "job_id": row.job_id,
            "type": LEGACY_TYPES.get(row.type, row.type),
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in reversed(list(rows))
    ]


def prune(session) -> None:
    """Events and the decision log are debugging aids: kept for RETENTION_DAYS."""
    since = utcnow() - timedelta(days=RETENTION_DAYS)
    session.execute(delete(ScoutEvent).where(ScoutEvent.created_at < since))
    session.execute(delete(ScoutDecision).where(ScoutDecision.created_at < since))


def profile_log(
    username: str,
    source: str,
    method: str,
    result: str,
    *,
    profile=None,
    classification=None,
    filters: list[str] | None = None,
    reason: str | None = None,
    details: str | None = None,
    resolve_log: list[str] | None = None,
    classification_log: list[str] | None = None,
) -> str:
    """Readable block for the Scout log viewer: resolver and classifier sections, then

    [Scout][@source][@username] with the resolved data, classification, filter checks
    and the result (LEAD CREATED / LEAD UPDATED / SKIPPED with reason and details).
    """
    separator = ["", "—" * 12, ""]
    lines = [*resolve_log, *separator] if resolve_log else []
    lines += [*classification_log, *separator] if classification_log else []
    lines += [f"[Scout][@{source}][@{username}]", "", f"method: {method}", ""]
    if profile is not None:
        followers = profile.followers_count
        lines += [
            "Resolved:",
            f"followers {followers if followers is not None else 'unknown'}",
            f"email {'yes' if profile.emails else 'no'}",
            f"phone {'yes' if profile.phones else 'no'}",
            "",
        ]
    if classification is not None:
        lines += [
            "Classification:",
            f"{classification.category} {classification.confidence}% ({classification.decided_by})",
            "",
        ]
    if filters:
        lines += ["Filters:", *filters, ""]
    lines += ["RESULT:", result]
    if reason:
        lines += ["", "Reason:", reason, *([details] if details else [])]
    return "\n".join(lines)
