"""LocalMusicClassifier: rules -> per-category scores -> category, confidence, uncertainty.

Fast and offline; AI is never consulted here.
"""

import math
from dataclasses import dataclass

from .config import DEFAULT_CONFIG, ClassifierConfig
from .model import (
    CATEGORIES,
    MUSIC_CATEGORIES,
    ClassificationRuleResult,
    LocalClassificationResult,
    ProfileCategory,
)
from .rules import ClassificationContext, RuleRegistry
from .text import build_profile_classification_text


@dataclass(frozen=True)
class ConfidenceResult:
    category: ProfileCategory
    score: int
    confidence: int
    uncertain: bool


def calculate_confidence(
    scores: dict[str, int],
    strong_signals: int = 0,
    negative_signals: int = 0,
    config: ClassifierConfig = DEFAULT_CONFIG,
) -> ConfidenceResult:
    """Category and confidence from category scores.

    Takes into account the absolute top score, the gap to the runner-up (another music
    category or "other"), the number of strong signals, conflicts and negative signals.
    Confidence is 1..99, never the raw score.
    """
    ranked = sorted(((scores.get(c, 0), c) for c in MUSIC_CATEGORIES), reverse=True)
    (top, category), (second, _) = ranked[0], ranked[1]
    other = scores.get("other", 0)

    if top < config.min_category_score or other > top:
        if other > top and top >= config.min_category_score:
            # Music signals outweighed by negative / non-music evidence.
            gap = other - top
            confidence = 55 + 5 * gap
            uncertain = gap < config.uncertainty_margin
        else:
            # No music category: weak music hints make "other" less certain.
            confidence = (
                config.other_base_confidence - config.other_hint_penalty * top + 3 * min(other, 5)
            )
            uncertain = top > 0
        return ConfidenceResult("other", other, _clamp(confidence), uncertain)

    runner_up = max(second, other)
    strength = 1 - math.exp(-top / config.confidence_scale)
    separation = (top - runner_up) / top
    confidence = 100 * strength * (0.4 + 0.6 * separation)
    confidence += config.strong_bonus * min(strong_signals, 3)
    confidence -= config.negative_penalty * negative_signals
    if second >= config.min_category_score:
        confidence -= config.conflict_penalty  # e.g. "rapper / producer"
    uncertain = top < config.min_confident_score or top - runner_up < config.uncertainty_margin
    return ConfidenceResult(category, top, _clamp(confidence), uncertain)


def _clamp(value: float) -> int:
    return max(1, min(99, round(value)))


class LocalMusicClassifier:
    def __init__(
        self, registry: RuleRegistry | None = None, config: ClassifierConfig | None = None
    ):
        self.registry = registry or RuleRegistry.default()
        self.config = config or DEFAULT_CONFIG

    def classify(self, profile) -> LocalClassificationResult:
        text = build_profile_classification_text(profile)
        context = ClassificationContext.build(text, self.config.weights)
        results = self.registry.evaluate(context)
        scores = {category: 0 for category in CATEGORIES}
        for result in results:
            scores[result.category] += result.score
        strong = [r for r in results if r.strong]
        negatives = [r for r in results if r.negative]
        decision = calculate_confidence(
            scores,
            strong_signals=len({r.evidence for r in strong if r.category != "other"}),
            negative_signals=len(negatives),
            config=self.config,
        )
        return LocalClassificationResult(
            category=decision.category,
            score=decision.score,
            confidence=decision.confidence,
            reasons=reasons(results, decision.category),
            uncertain=decision.uncertain,
            rule_results=results,
            scores=scores,
            strong_signals=sum(r.category == decision.category for r in strong),
        )


def reasons(results: list[ClassificationRuleResult], category: str) -> list[str]:
    """Evidence of the chosen category first, then the rest (conflicts, negatives)."""
    ordered = sorted(results, key=lambda r: (r.category != category, -r.score))
    return list(dict.fromkeys(r.evidence for r in ordered))
