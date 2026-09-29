"""Classification types: rule results, local / AI / final results.

The classifier only labels a profile (artist / producer / media / other) and explains
why; it never saves leads, applies follower filters, touches the CRM or sends messages.
"""

from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

ProfileCategory = Literal["artist", "producer", "media", "other"]
CATEGORIES: tuple[ProfileCategory, ...] = ("artist", "producer", "media", "other")
MUSIC_CATEGORIES: tuple[ProfileCategory, ...] = ("artist", "producer", "media")
AIMode = Literal["off", "uncertain", "always"]
DecidedBy = Literal["local", "ai", "local+ai"]


@dataclass
class ProfileText:
    """The profile fields the classifier reads (NormalizedInstagramProfile.text())."""

    username: str
    full_name: str = ""
    biography: str = ""
    category_name: str = ""
    external_url: str = ""
    bio_links: list[str] = field(default_factory=list)
    recent_captions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClassificationRuleResult:
    rule_id: str
    category: ProfileCategory
    score: int
    evidence: str
    # Strong signals raise confidence; negative ones (category "other") lower it.
    strong: bool = False
    negative: bool = False


@dataclass
class LocalClassificationResult:
    category: ProfileCategory
    score: int
    confidence: int
    reasons: list[str]
    uncertain: bool
    rule_results: list[ClassificationRuleResult]
    scores: dict[str, int]
    strong_signals: int = 0

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "score": self.score,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "uncertain": self.uncertain,
            "scores": self.scores,
        }


@dataclass
class AIClassificationResult:
    category: ProfileCategory
    confidence: int
    model: str
    cached: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FinalClassificationResult:
    category: ProfileCategory
    confidence: int
    local: LocalClassificationResult
    decided_by: DecidedBy
    ai: AIClassificationResult | None = None
    # Why AI was not asked or did not answer ("not required", "failed: ...").
    ai_note: str = ""
    # AI was temporarily unavailable; a pending job retries it later.
    ai_pending: bool = False
    log: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "decided_by": self.decided_by,
            "local": self.local.as_dict(),
            "ai": self.ai.as_dict() if self.ai else None,
            "ai_note": self.ai_note,
        }


class AIClassificationError(Exception):
    """AI could not classify; the local result stays in force."""

    # Transient failures (network, timeout, 429, 5xx) are retried later by a pending job.
    transient = False


class AITransientError(AIClassificationError):
    transient = True


class AIInvalidOutput(AIClassificationError):
    """The model answered, but not with valid JSON of the expected schema."""


class AIProfileClassifier(Protocol):
    def classify(
        self,
        profile: ProfileText,
        local: LocalClassificationResult | None = None,
        *,
        model: str,
        timeout: float,
    ) -> AIClassificationResult: ...
