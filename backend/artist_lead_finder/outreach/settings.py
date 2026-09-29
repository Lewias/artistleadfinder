"""Outreach settings, stored with the other application settings."""

from pydantic import BaseModel, ConfigDict, Field


class OutreachSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # Leads with any earlier initial message (any campaign) or a manual "contacted" mark.
    outreach_skip_previously_contacted: bool = True
    # Queue pacing per sender account: a load limit, not a way around platform limits.
    outreach_send_interval_seconds: int = Field(default=180, ge=30, le=3600)
    outreach_daily_limit_per_sender: int = Field(default=20, ge=1, le=200)
    # Temporary failures (network) only; everything else is never retried automatically.
    outreach_max_attempts: int = Field(default=3, ge=1, le=5)
    # A rate-limited sender sends nothing until this break ends (or the user resumes it).
    outreach_rate_limit_pause_minutes: int = Field(default=360, ge=60, le=10080)


def outreach_settings(settings: dict) -> OutreachSettings:
    return OutreachSettings.model_validate(settings)
