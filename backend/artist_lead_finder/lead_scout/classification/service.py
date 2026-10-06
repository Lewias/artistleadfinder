"""ClassificationService: local rules first, AI only when the mode and result call for it.

profile -> LocalMusicClassifier -> AI mode? (off / uncertain / always) -> cache ->
AI (validated, timeout, bounded concurrency) -> merge -> FinalClassificationResult.

AI failures never stop the Scout: the local result stands, and transient failures are
kept as pending jobs that the caller retries between its own steps.
"""

import logging
import threading
from dataclasses import dataclass

from .cache import profile_hash, profile_text
from .local import LocalMusicClassifier
from .model import (
    MUSIC_CATEGORIES,
    AIClassificationError,
    AIClassificationResult,
    AIMode,
    AIProfileClassifier,
    FinalClassificationResult,
    LocalClassificationResult,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClassificationSettings:
    mode: AIMode = "uncertain"
    model: str = "anthropic/claude-haiku-4.5"
    timeout_seconds: float = 15
    concurrency: int = 2
    # "uncertain" mode also asks AI when local confidence is below this.
    min_confidence: int = 55

    @classmethod
    def from_scout(cls, scout) -> "ClassificationSettings":
        return cls(
            mode=scout.scout_ai_mode,
            model=scout.scout_ai_model,
            timeout_seconds=scout.scout_ai_timeout_seconds,
            concurrency=scout.scout_ai_concurrency,
            min_confidence=scout.scout_ai_min_confidence,
        )


@dataclass
class RetriedClassification:
    """A pending AI job that has now been answered."""

    context: dict
    ai: AIClassificationResult


class ClassificationService:
    def __init__(
        self,
        ai: AIProfileClassifier | None = None,
        cache=None,
        pending=None,
        local: LocalMusicClassifier | None = None,
    ):
        self.ai = ai
        self.cache = cache
        self.pending = pending
        self.local_classifier = local or LocalMusicClassifier()
        self._slots: tuple[int, threading.BoundedSemaphore] | None = None
        self._lock = threading.Lock()

    # ---------- Local ----------

    def local(self, profile) -> LocalClassificationResult:
        return self.local_classifier.classify(profile)

    # ---------- Decision ----------

    def classify(self, profile, settings: ClassificationSettings, context=None):
        return self.decide(profile, self.local(profile), settings, context)

    def local_only(self, profile, local: LocalClassificationResult, note: str):
        """Final result without AI (e.g. the caller filtered the profile out already)."""
        final = FinalClassificationResult(
            local.category, local.confidence, local, "local", ai_note=note
        )
        return self._logged(profile, final)

    def ai_reason(self, local: LocalClassificationResult, settings) -> str | None:
        """Why AI is needed, or None when the local result stands."""
        if settings.mode == "off":
            return None
        if settings.mode == "always":
            return "mode always"
        config = self.local_classifier.config
        if (
            local.confidence >= config.override_confidence
            and local.strong_signals >= config.override_strong_signals
        ):
            return None  # strong local override
        if local.uncertain:
            return "local result uncertain"
        if local.confidence < settings.min_confidence:
            return f"local confidence {local.confidence} < {settings.min_confidence}"
        return None

    def decide(
        self,
        profile,
        local: LocalClassificationResult,
        settings: ClassificationSettings,
        context: dict | None = None,
    ) -> FinalClassificationResult:
        """`context` is stored with a pending AI job (the caller's task), never read here."""
        final = FinalClassificationResult(local.category, local.confidence, local, "local")
        reason = self.ai_reason(local, settings)
        if reason is None:
            final.ai_note = "off" if settings.mode == "off" else "not required"
            return self._logged(profile, final)
        key = profile_hash(profile)
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            return self._logged(profile, self._merge(final, cached, settings, "cached"))
        if self.ai is None:
            final.ai_note = "not configured"
            return self._logged(profile, final)
        text = profile_text(profile)
        try:
            with self._slot(settings):
                result = self.ai.classify(
                    text, local, model=settings.model, timeout=settings.timeout_seconds
                )
        except AIClassificationError as error:
            log.warning(
                "scout_ai_failed", extra={"error_type": type(error).__name__, "detail": str(error)}
            )
            final.ai_note = f"failed: {error}"
            if error.transient and self.pending is not None:
                self.pending.add(key, text, context or {}, str(error))
                final.ai_pending = True
                final.ai_note += " (retry scheduled)"
            return self._logged(profile, final)
        if self.cache is not None:
            self.cache.put(key, text.username, result)
        return self._logged(profile, self._merge(final, result, settings, reason))

    @staticmethod
    def _merge(final, ai: AIClassificationResult, settings, note) -> FinalClassificationResult:
        """AI answered: its category and confidence decide, the local result is kept.

        In "uncertain" mode AI arbitrates the local result ("local+ai"); in "always" mode
        AI decides alone unless both agree.
        """
        agree = ai.category == final.local.category
        final.category, final.confidence, final.ai = ai.category, ai.confidence, ai
        final.decided_by = "ai" if settings.mode == "always" and not agree else "local+ai"
        final.ai_note = note
        return final

    def _slot(self, settings) -> threading.BoundedSemaphore:
        """At most `concurrency` AI requests at once, however many queues run."""
        with self._lock:
            size = max(1, settings.concurrency)
            if self._slots is None or self._slots[0] != size:
                self._slots = (size, threading.BoundedSemaphore(size))
            return self._slots[1]

    # ---------- Pending AI jobs ----------

    def retry_pending(self, settings: ClassificationSettings, limit: int = 1):
        """Retry a few due pending jobs; returns the ones AI has now answered."""
        if settings.mode == "off" or self.ai is None or self.pending is None:
            return []
        answered = []
        for key, text, context, _attempts in self.pending.due(limit):
            try:
                with self._slot(settings):
                    result = self.ai.classify(
                        text, None, model=settings.model, timeout=settings.timeout_seconds
                    )
            except AIClassificationError as error:
                if error.transient:
                    self.pending.failed(key, str(error))
                else:
                    self.pending.done(key)
                continue
            if self.cache is not None:
                self.cache.put(key, text.username, result)
            self.pending.done(key)
            answered.append(RetriedClassification(context, result))
        return answered

    # ---------- Log ----------

    def _logged(self, profile, final: FinalClassificationResult) -> FinalClassificationResult:
        final.log = classification_log(profile_text(profile).username, final)
        return final


def classification_log(username: str, final: FinalClassificationResult) -> list[str]:
    local = final.local
    lines = [f"[Classifier][@{username}]"]
    for category in (*MUSIC_CATEGORIES, "other"):
        results = [r for r in local.rule_results if r.category == category]
        lines += ["", f"{category.upper()}:"]
        lines += [f"+{r.score} {r.evidence}" for r in results] or ["0"]
    ranked = sorted(local.scores.items(), key=lambda item: -item[1])
    lines += ["", "Local:"]
    if local.uncertain and ranked[0][1] > 0:
        lines += [f"{name} {score}" for name, score in ranked[:2] if score > 0]
    else:
        lines.append(local.category)
    lines.append(f"confidence: {local.confidence}")
    if local.uncertain:
        lines.append("uncertain=true")
    lines += ["", "AI:"]
    if final.ai is not None:
        cached = " (cached)" if final.ai.cached else ""
        lines.append(f"{final.ai.category} {final.ai.confidence} · {final.ai.model}{cached}")
    else:
        lines.append(final.ai_note or "not required")
    lines += ["", "Final:", final.category, f"decidedBy={final.decided_by}"]
    return lines
