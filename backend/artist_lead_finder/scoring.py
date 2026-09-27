"""Explainable, validated weights; no source or database dependencies."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .analysis import ProfileSignals
from .classification import ClassificationResult
from .providers import Candidate
from .schemas import SearchConfiguration


class ScoringWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artist: int = Field(default=30, ge=0, le=100)
    music_bio: int = Field(default=15, ge=0, le=100)
    music_link: int = Field(default=15, ge=0, le=100)
    recent_activity: int = Field(default=15, ge=0, le=100)
    followers: int = Field(default=10, ge=0, le=100)
    genre: int = Field(default=10, ge=0, le=100)
    strong_activity: int = Field(default=5, ge=0, le=100)

    @model_validator(mode="after")
    def total(self):
        if sum(self.model_dump().values()) != 100:
            raise ValueError("Сумма весов должна равняться 100.")
        return self


class ScoreItem(BaseModel):
    rule: str
    points: int
    reason: str


class ScoreResult(BaseModel):
    score: int
    breakdown: list[ScoreItem]
    qualified: bool


class LeadScorer:
    def __init__(self, weights: ScoringWeights | None = None) -> None:
        self.weights = weights or ScoringWeights()

    def score(
        self,
        candidate: Candidate,
        signals: ProfileSignals,
        classification: ClassificationResult,
        config: SearchConfiguration,
    ) -> ScoreResult:
        follower_match = config.min_followers <= candidate.followers <= config.max_followers
        genre_match = not config.genres or bool(
            {g.casefold() for g in config.genres} & {g.casefold() for g in classification.genres}
        )
        active = signals.activity_days is not None and signals.activity_days <= config.activity_days
        rules = [
            ("artist", classification.is_artist, "Профиль вероятного артиста"),
            ("music_bio", signals.music_related, "Музыкальная биография"),
            (
                "music_link",
                signals.has_spotify
                or signals.has_apple_music
                or signals.has_soundcloud
                or signals.has_youtube,
                "Ссылка музыкальной платформы",
            ),
            (
                "recent_activity",
                active and signals.recent_release_signal,
                "Недавняя музыка / релиз",
            ),
            ("followers", follower_match, "Аудитория в заданном диапазоне"),
            ("genre", genre_match, "Соответствие жанровым условиям"),
            (
                "strong_activity",
                signals.activity_level == "strong",
                "Активность за последние 7 дней",
            ),
        ]
        items = [
            ScoreItem(rule=key, points=getattr(self.weights, key) if match else 0, reason=reason)
            for key, match, reason in rules
        ]
        score = sum(item.points for item in items)
        qualified = (
            classification.is_artist
            and not candidate.is_private
            and follower_match
            and genre_match
            and active
            and score >= config.minimum_score
        )
        return ScoreResult(score=score, breakdown=items, qualified=qualified)
