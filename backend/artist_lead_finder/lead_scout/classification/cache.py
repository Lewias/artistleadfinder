"""Persistent AI classification cache and pending AI jobs (transient AI failures).

Cache key: sha256 of the normalised fields the classifier reads, so an edited bio or
new link asks AI again while an unchanged profile never does. Rows of another
CLASSIFIER_VERSION are ignored.
"""

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import select

from ...models import ScoutAIClassification, ScoutPendingAIJob, utcnow
from ..memory import aware
from .config import CLASSIFIER_VERSION
from .model import AIClassificationResult, ProfileText
from .text import normalize_text

# Pending jobs: 1, 2, 4 ... minutes, at most one hour apart, dropped after this many tries.
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600
MAX_ATTEMPTS = 5


def profile_text(profile) -> ProfileText:
    return profile if isinstance(profile, ProfileText) else profile.text()


def profile_hash(profile, version: str = CLASSIFIER_VERSION) -> str:
    """sha256 of the normalised classifier input and the classifier version."""
    text = profile_text(profile)
    fields = {
        "version": version,
        "username": text.username.lower().lstrip("@"),
        "biography": normalize_text(text.biography),
        "categoryName": normalize_text(text.category_name),
        "externalUrl": (text.external_url or "").strip().lower(),
        "bioLinks": sorted({link.strip().lower() for link in text.bio_links if link}),
        "recentCaptions": [normalize_text(caption) for caption in text.recent_captions],
    }
    blob = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def retry_delay(attempts: int) -> timedelta:
    return timedelta(seconds=min(RETRY_BASE_SECONDS * 2 ** max(attempts - 1, 0), RETRY_MAX_SECONDS))


class SqlAIClassificationCache:
    def __init__(self, sessions, version: str = CLASSIFIER_VERSION):
        self.sessions, self.version = sessions, version

    def get(self, key: str) -> AIClassificationResult | None:
        with self.sessions() as session:
            row = session.get(ScoutAIClassification, key)
            if row is None or row.classifier_version != self.version:
                return None
            return AIClassificationResult(row.category, row.confidence, row.model, cached=True)

    def put(self, key: str, username: str, result: AIClassificationResult) -> None:
        with self.sessions.begin() as session:
            row = session.get(ScoutAIClassification, key) or ScoutAIClassification(profile_hash=key)
            row.username = username[:40]
            row.category, row.confidence, row.model = (
                result.category,
                result.confidence,
                result.model,
            )
            row.classifier_version, row.created_at = self.version, utcnow()
            session.add(row)


class SqlPendingAIJobs:
    """Profiles whose AI step failed transiently. Retrying never blocks the Scout: the
    caller asks for a few due jobs between its own steps."""

    def __init__(self, sessions, now=utcnow):
        self.sessions, self.now = sessions, now

    def add(self, key: str, profile, context: dict, error: str) -> None:
        with self.sessions.begin() as session:
            row = session.get(ScoutPendingAIJob, key)
            if row is None:
                text = profile_text(profile)
                row = ScoutPendingAIJob(
                    profile_hash=key,
                    username=text.username[:40],
                    profile=asdict(text),
                    context=context,
                    created_at=self.now(),
                )
            row.attempt_count = (row.attempt_count or 0) + 1
            row.last_error = error[:200]
            row.retry_at = self.now() + retry_delay(row.attempt_count)
            session.add(row)

    def due(self, limit: int = 1) -> list[tuple[str, ProfileText, dict, int]]:
        now = self.now()
        with self.sessions() as session:
            rows = session.scalars(
                select(ScoutPendingAIJob).order_by(ScoutPendingAIJob.retry_at).limit(limit * 4)
            )
            ready = [row for row in rows if aware(row.retry_at) <= now][:limit]
            return [
                (row.profile_hash, ProfileText(**row.profile), dict(row.context), row.attempt_count)
                for row in ready
            ]

    def failed(self, key: str, error: str) -> bool:
        """Another transient failure; returns False when the job was dropped."""
        with self.sessions.begin() as session:
            row = session.get(ScoutPendingAIJob, key)
            if row is None:
                return False
            row.attempt_count += 1
            if row.attempt_count >= MAX_ATTEMPTS:
                session.delete(row)
                return False
            row.last_error = error[:200]
            row.retry_at = self.now() + retry_delay(row.attempt_count)
            return True

    def done(self, key: str) -> None:
        with self.sessions.begin() as session:
            row = session.get(ScoutPendingAIJob, key)
            if row is not None:
                session.delete(row)

    def count(self) -> int:
        with self.sessions() as session:
            return len(list(session.scalars(select(ScoutPendingAIJob.profile_hash))))
