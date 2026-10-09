"""Settings of reading the outreach threads, stored with the other application settings."""

from pydantic import BaseModel, ConfigDict, Field


class InboxSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # Country of numbers written without a country code («(312) 555-0199»).
    inbox_region: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    # Threads read in one scan, newest outreach first.
    inbox_max_threads: int = Field(default=100, ge=1, le=1000)
    # Pause between two threads, seconds: a load limit like the parser's page pauses.
    inbox_delay_min_seconds: int = Field(default=6, ge=3, le=300)
    inbox_delay_max_seconds: int = Field(default=15, ge=3, le=600)


def inbox_settings(settings: dict) -> InboxSettings:
    return InboxSettings.model_validate(settings)
