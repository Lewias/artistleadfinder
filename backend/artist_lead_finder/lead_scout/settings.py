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
    # A profile whose followers count could not be read passes the followers filter.
    scout_allow_unknown_followers: bool = True
    # Final classification confidence below this skips the profile (0 turns it off).
    scout_min_confidence: int = Field(default=0, ge=0, le=100)
    # CRM status of a new Scout lead; existing leads keep their status.
    scout_lead_status: Literal["new", "reviewed", "qualified"] = "new"
    scout_skip_processed: bool = True
    scout_skip_recent_sources: bool = True
    scout_source_cooldown_hours: int = Field(default=24, ge=0, le=24 * 90)
    # How many enabled sources one run scans; the rotation cursor continues next run.
    scout_sources_per_run: int = Field(default=5, ge=1, le=500)
    scout_follow_page_size: int = Field(default=12, ge=5, le=50)
    scout_follow_delay_seconds: int = Field(default=2, ge=1, le=10)
    scout_follow_max: int = Field(default=100, ge=1, le=1000)
    # Posts / tagged grids: lazy scrolling until enough unprocessed publications are found.
    scout_max_posts_per_source: int = Field(default=120, ge=1, le=200)
    scout_max_scroll_rounds: int = Field(default=15, ge=0, le=40)
    scout_scroll_delay_ms: int = Field(default=1200, ge=300, le=5000)
    scout_max_no_progress_rounds: int = Field(default=2, ge=1, le=10)
    # Stories: frames per source, pause between frames, confidence threshold, reuse window.
    scout_max_stories_per_source: int = Field(default=20, ge=1, le=100)
    scout_story_delay_ms: int = Field(default=1500, ge=500, le=5000)
    scout_story_confidence: float = Field(default=0.8, ge=0.5, le=1.0)
    scout_story_ttl_hours: int = Field(default=48, ge=1, le=24 * 30)
    # Transient navigation failures are retried; a publication failing this often is skipped.
    scout_max_retries: int = Field(default=2, ge=0, le=5)
    scout_max_item_failures: int = Field(default=3, ge=1, le=10)
    scout_debug: bool = False
    # Profile resolver: in-tab web API first (browser page only as a fallback),
    # persistent cache of resolved profiles, recent captions taken from the API answer.
    scout_profile_api: bool = True
    scout_profile_cache_hours: int = Field(default=12, ge=0, le=24 * 7)
    scout_recent_captions: int = Field(default=3, ge=0, le=12)
    scout_ignore_usernames: list[str] = Field(default_factory=list, max_length=500)
    scout_ai_mode: Literal["off", "uncertain", "always"] = "uncertain"
    scout_ai_model: str = Field(default="anthropic/claude-haiku-4.5", min_length=3, max_length=120)
    # AI requests: timeout, parallel requests, and "uncertain" mode also asks AI when the
    # local confidence is below scout_ai_min_confidence.
    scout_ai_timeout_seconds: int = Field(default=15, ge=5, le=60)
    scout_ai_concurrency: int = Field(default=2, ge=1, le=5)
    scout_ai_min_confidence: int = Field(default=55, ge=0, le=100)

    @model_validator(mode="after")
    def ordered_followers(self):
        if self.scout_max_followers < self.scout_min_followers:
            raise ValueError("Максимум подписчиков меньше минимума.")
        return self
