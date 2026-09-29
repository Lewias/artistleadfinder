"""Profile classification: NormalizedInstagramProfile -> local rules -> optional AI ->
FinalClassificationResult (artist / producer / media / other, with reasons)."""

from .cache import SqlAIClassificationCache, SqlPendingAIJobs, profile_hash
from .config import CLASSIFIER_VERSION, DEFAULT_CONFIG, ClassifierConfig, ClassifierWeights
from .local import LocalMusicClassifier, calculate_confidence
from .model import (
    CATEGORIES,
    AIClassificationError,
    AIClassificationResult,
    AIInvalidOutput,
    AITransientError,
    ClassificationRuleResult,
    FinalClassificationResult,
    LocalClassificationResult,
    ProfileText,
)
from .rules import RULE_GROUPS, ClassificationContext, RuleRegistry
from .service import ClassificationService, ClassificationSettings, classification_log
from .text import build_profile_classification_text

__all__ = [
    "AIClassificationError",
    "AIClassificationResult",
    "AIInvalidOutput",
    "AITransientError",
    "CATEGORIES",
    "CLASSIFIER_VERSION",
    "ClassificationContext",
    "ClassificationRuleResult",
    "ClassificationService",
    "ClassificationSettings",
    "ClassifierConfig",
    "ClassifierWeights",
    "DEFAULT_CONFIG",
    "FinalClassificationResult",
    "LocalClassificationResult",
    "LocalMusicClassifier",
    "ProfileText",
    "RULE_GROUPS",
    "RuleRegistry",
    "SqlAIClassificationCache",
    "SqlPendingAIJobs",
    "build_profile_classification_text",
    "calculate_confidence",
    "classification_log",
    "profile_hash",
]
