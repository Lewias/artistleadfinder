"""Browsing pace for scout runs: page delays, hourly profile cap and rate-limit breaks.

State lives in memory; restarting the core starts a fresh pace.
"""

import random
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import utcnow


class PacingSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page_delay_min: int = Field(default=8, ge=3, le=300)
    page_delay_max: int = Field(default=20, ge=3, le=600)
    # 0 disables a limit.
    profiles_per_hour: int = Field(default=60, ge=0, le=1000)
    profiles_per_run: int = Field(default=0, ge=0, le=100000)
    rate_limit_pause_minutes: int = Field(default=10, ge=5, le=1440)

    @model_validator(mode="after")
    def ordered_delay(self):
        if self.page_delay_max < self.page_delay_min:
            raise ValueError("Максимальная пауза меньше минимальной.")
        return self


class Pacer:
    def __init__(
        self,
        now: Callable[[], datetime] = utcnow,
        jitter: Callable[[float, float], float] = random.uniform,
    ):
        self.now, self.jitter = now, jitter
        self.next_page_at: datetime | None = None
        self.cooldown_until: datetime | None = None
        self.profile_visits: deque[datetime] = deque()

    def page_done(self, settings: PacingSettings, profile: bool) -> None:
        now = self.now()
        delay = self.jitter(settings.page_delay_min, settings.page_delay_max)
        self.next_page_at = now + timedelta(seconds=delay)
        if profile:
            self.profile_visits.append(now)

    def rate_limited(self, settings: PacingSettings) -> None:
        self.cooldown_until = self.now() + timedelta(minutes=settings.rate_limit_pause_minutes)

    def wait(self, settings: PacingSettings, next_kind: str) -> tuple[float, str | None]:
        """Seconds until the next page may be opened, with the reason shown to the user."""
        now = self.now()
        hour_ago = now - timedelta(hours=1)
        while self.profile_visits and self.profile_visits[0] <= hour_ago:
            self.profile_visits.popleft()
        waits = []
        if self.cooldown_until and self.cooldown_until > now:
            waits.append(
                (
                    self.cooldown_until,
                    "Instagram ограничил запросы. Перерыв до "
                    f"{self.cooldown_until.astimezone():%H:%M}.",
                )
            )
        if self.next_page_at and self.next_page_at > now:
            waits.append((self.next_page_at, "Пауза между страницами."))
        limit = settings.profiles_per_hour
        if next_kind == "profile" and limit and len(self.profile_visits) >= limit:
            resume = self.profile_visits[-limit] + timedelta(hours=1)
            waits.append(
                (
                    resume,
                    f"Лимит {limit} профилей в час. Продолжение в {resume.astimezone():%H:%M}.",
                )
            )
        if not waits:
            return 0.0, None
        until, reason = max(waits, key=lambda item: item[0])
        return (until - now).total_seconds(), reason
