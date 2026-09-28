"""Scout memory in the application database: processed items, cooldowns, cursor, AI cache."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..models import (
    BrowserQueue,
    ScoutAICache,
    ScoutProcessedPost,
    ScoutProcessedProfile,
    ScoutProcessedStory,
    ScoutRun,
    ScoutSource,
    ScoutState,
    SearchJob,
    utcnow,
)


def aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def processed_profile(session, username: str) -> ScoutProcessedProfile | None:
    return session.get(ScoutProcessedProfile, username)


def mark_profile(
    session, username: str, source: str, method: str, result: str, reason=None
) -> None:
    row = session.get(ScoutProcessedProfile, username) or ScoutProcessedProfile(username=username)
    row.source_username, row.method, row.result, row.reason = source, method, result, reason
    row.processed_at = utcnow()
    session.add(row)


def queued_elsewhere(session, job_id: int) -> set[str]:
    """Profile URLs queued by other runs that are still in progress."""
    queued = set()
    for run, queue, job in session.execute(
        select(ScoutRun, BrowserQueue, SearchJob)
        .join(BrowserQueue, BrowserQueue.job_id == ScoutRun.job_id)
        .join(SearchJob, SearchJob.id == ScoutRun.job_id)
        .where(ScoutRun.job_id != job_id, SearchJob.status.in_(["running", "paused"]))
        .where(SearchJob.stage != "interrupted")
    ):
        queued.update(t["url"] for t in run.tasks[queue.cursor :] if t["kind"] == "profile")
    return queued


def processed_post(session, post_id: str) -> bool:
    return session.get(ScoutProcessedPost, post_id) is not None


def mark_post(session, post_id: str, source: str, kind: str) -> None:
    if session.get(ScoutProcessedPost, post_id) is None:
        session.add(ScoutProcessedPost(post_id=post_id, source_username=source, kind=kind))


def processed_story(session, story_id: str) -> bool:
    return session.get(ScoutProcessedStory, story_id) is not None


def mark_story(session, story_id: str, source: str) -> None:
    if session.get(ScoutProcessedStory, story_id) is None:
        session.add(ScoutProcessedStory(story_id=story_id, source_username=source))


def in_cooldown(source: ScoutSource, hours: int, now: datetime | None = None) -> bool:
    last = aware(source.last_scanned_at)
    if not hours or last is None:
        return False
    return (now or utcnow()) - last < timedelta(hours=hours)


def state_get(session, key: str, default=None):
    row = session.get(ScoutState, key)
    return row.value if row else default


def state_set(session, key: str, value) -> None:
    row = session.get(ScoutState, key) or ScoutState(key=key)
    row.value = value
    session.add(row)


def pick_sources(
    session, batch: int, cooldown_hours: int, skip_recent: bool
) -> tuple[list[str], list[str]]:
    """Next `batch` enabled sources after the stored cursor, skipping ones in cooldown.

    Returns (picked urls, skipped-in-cooldown urls). The cursor advances past the
    last picked source, so consecutive runs cycle A B, C D, E F, A B...
    """
    sources = list(
        session.scalars(
            select(ScoutSource)
            .where(ScoutSource.enabled)
            .order_by(ScoutSource.added_at, ScoutSource.url)
        )
    )
    if not sources:
        return [], []
    cursor = int(state_get(session, "source_cursor", 0) or 0) % len(sources)
    picked, cooling, last_index = [], [], None
    for step in range(len(sources)):
        index = (cursor + step) % len(sources)
        source = sources[index]
        if skip_recent and in_cooldown(source, cooldown_hours):
            cooling.append(source.url)
            continue
        picked.append(source.url)
        last_index = index
        if len(picked) >= batch:
            break
    if last_index is not None:
        state_set(session, "source_cursor", (last_index + 1) % len(sources))
    return picked, cooling


def ai_cached(session, username: str, model: str) -> ScoutAICache | None:
    row = session.get(ScoutAICache, username)
    return row if row and row.model == model else None


def ai_store(session, username: str, category: str, confidence: int, model: str) -> None:
    row = session.get(ScoutAICache, username) or ScoutAICache(username=username)
    row.category, row.confidence, row.model, row.created_at = category, confidence, model, utcnow()
    session.add(row)
