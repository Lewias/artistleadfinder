"""Optional classifier contract; default requires no key or network."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from .analysis import ProfileSignals


class ClassificationResult(BaseModel):
    is_artist: bool
    confidence: float = Field(ge=0, le=1)
    primary_genre: str | None
    genres: list[str]
    signals: list[str]


class AIClassifier(ABC):
    @abstractmethod
    def classify(self, signals: ProfileSignals) -> ClassificationResult: ...


class GenreClassifier:
    def classify(self, signals: ProfileSignals) -> tuple[str | None, list[str]]:
        genres = list(dict.fromkeys(signals.possible_genres))
        return (genres[0] if genres else None), genres


class RuleBasedClassifier(AIClassifier):
    def classify(self, signals: ProfileSignals) -> ClassificationResult:
        primary, genres = GenreClassifier().classify(signals)
        # A platform link alone is insufficient: fans and producers also have music links.
        artist = signals.likely_artist
        music_link = signals.has_spotify or signals.has_apple_music or signals.has_soundcloud
        confidence = (
            0.7 + 0.15 * music_link + 0.1 * signals.recent_release_signal
            if artist
            else 0.2
            if signals.likely_producer
            else 0.05
        )
        return ClassificationResult(
            is_artist=artist,
            confidence=round(confidence, 2),
            primary_genre=primary,
            genres=genres,
            signals=signals.reasons,
        )
