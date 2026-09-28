"""Lead Scout settings; stored through the application's settings system."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DISCOVERY_METHODS = ("posts", "comments", "tagged", "stories", "followers", "following")


class ScoutSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scout_methods: list[
        Literal["posts", "comments", "tagged", "stories", "followers", "following"]
    ] = Field(default_factory=lambda: ["posts", "comments", "tagged"])
    scout_profile_type: Literal["artists", "artists_producers", "everyone"] = "artists"
    scout_min_followers: int = Field(default=0, ge=0, le=1_000_000_000)
    scout_max_followers: int = Field(default=1_000_000, ge=0, le=1_000_000_000)
    scout_only_contacts: bool = False
    scout_skip_processed: bool = True
    scout_skip_recent_sources: bool = True
    scout_source_cooldown_hours: int = Field(default=24, ge=0, le=24 * 90)
    # How many enabled sources one run scans; the rotation cursor continues next run.
    scout_sources_per_run: int = Field(default=5, ge=1, le=500)
    scout_follow_page_size: int = Field(default=12, ge=5, le=50)
    scout_follow_delay_seconds: int = Field(default=2, ge=1, le=10)
    scout_follow_max: int = Field(default=100, ge=1, le=1000)
    scout_ai_mode: Literal["off", "uncertain", "always"] = "uncertain"
    scout_ai_model: str = Field(default="anthropic/claude-haiku-4.5", min_length=3, max_length=120)

    @model_validator(mode="after")
    def ordered_followers(self):
        if self.scout_max_followers < self.scout_min_followers:
            raise ValueError("Максимум подписчиков меньше минимума.")
        return self
