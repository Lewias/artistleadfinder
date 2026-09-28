"""Scout events and per-profile logs, polled by the interface for live progress."""

from datetime import timedelta

from sqlalchemy import delete, select

from ..models import ScoutEvent, utcnow

EVENT_TYPES = {
    "scout:start",
    "source:start",
    "candidate:found",
    "profile:analyzing",
    "profile:skipped",
    "lead:found",
    "source:done",
    "scout:pause",
    "scout:stop",
    "scout:error",
    "scout:done",
    "discovery:page",
}
RETENTION_DAYS = 30


def emit(session, job_id: int, event_type: str, **payload) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown scout event: {event_type}")
    session.add(ScoutEvent(job_id=job_id, type=event_type, payload=payload))


def listing(session, job_id: int | None, after: int = 0, limit: int = 200) -> list[dict]:
    query = select(ScoutEvent).where(ScoutEvent.id > after)
    if job_id:
        query = query.where(ScoutEvent.job_id == job_id)
    rows = session.scalars(query.order_by(ScoutEvent.id.desc()).limit(min(limit, 500)))
    return [
        {
            "id": row.id,
            "job_id": row.job_id,
            "type": row.type,
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in reversed(list(rows))
    ]


def prune(session) -> None:
    session.execute(
        delete(ScoutEvent).where(ScoutEvent.created_at < utcnow() - timedelta(days=RETENTION_DAYS))
    )


def profile_log(
    username: str,
    source: str,
    method: str,
    followers: int | None,
    classification: dict | None,
    ai: dict | None,
    result: str,
) -> str:
    """Readable block for the Scout log viewer."""
    lines = [
        f"[@{username}]",
        "",
        f"source: @{source}",
        f"method: {method}",
        f"followers: {followers if followers is not None else 'unknown'}",
    ]
    if classification:
        lines += [
            "",
            "local classification:",
            classification["category"],
            f"confidence: {classification['confidence']}",
            f"score: {classification['score']}"
            + (" (uncertain)" if classification["uncertain"] else ""),
        ]
        if classification["reasons"]:
            lines += ["", "signals:", *[f"+ {reason}" for reason in classification["reasons"][:12]]]
    if ai:
        lines += [
            "",
            f"AI: {ai.get('category', '—')} · confidence {ai.get('confidence', '—')}"
            f" · {ai.get('model', '')}",
        ]
        if ai.get("error"):
            lines.append(f"AI skipped: {ai['error']}")
    lines += ["", "RESULT:", result]
    return "\n".join(lines)
