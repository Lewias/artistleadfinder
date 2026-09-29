"""Persistent profile cache in the application SQLite database (key: username)."""

from datetime import timedelta

from ...models import ScoutProfileCache, utcnow
from ..memory import aware
from .model import NormalizedInstagramProfile


class SqlProfileCache:
    def __init__(self, sessions):
        self.sessions = sessions

    def get(self, username: str, ttl_hours: int) -> NormalizedInstagramProfile | None:
        with self.sessions() as session:
            row = session.get(ScoutProfileCache, username)
            if row is None or aware(row.resolved_at) < utcnow() - timedelta(hours=ttl_hours):
                return None
            try:
                return NormalizedInstagramProfile.from_dict(row.profile)
            except (KeyError, TypeError, ValueError):
                return None  # An entry from an older format is simply resolved again.

    def put(self, profile: NormalizedInstagramProfile) -> None:
        with self.sessions.begin() as session:
            row = session.get(ScoutProfileCache, profile.username) or ScoutProfileCache(
                username=profile.username
            )
            row.profile, row.source, row.resolved_at = (
                profile.as_dict(),
                profile.source,
                aware(profile.resolved_at),
            )
            session.add(row)
