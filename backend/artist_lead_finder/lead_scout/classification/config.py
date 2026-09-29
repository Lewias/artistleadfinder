"""Weights and thresholds of the local classifier; no magic numbers in the rules.

Change CLASSIFIER_VERSION whenever rules, weights or the AI prompt change noticeably:
cached AI answers of another version are not reused.
"""

from dataclasses import dataclass

CLASSIFIER_VERSION = "1"


@dataclass(frozen=True)
class ClassifierWeights:
    strong: int = 5  # "rapper", "music producer", "music blog"
    medium: int = 2  # "dj", "beats", "new single" in the bio
    weak: int = 1  # generic "artist", caption mentions, YouTube, username hints
    strong_negative: int = 4  # forex, casino, fan page ...
    medium_negative: int = 2  # boutique, real estate, makeup artist ...
    music_link: int = 3  # Spotify / Apple Music / SoundCloud ... (artist)
    music_links_max: int = 2  # distinct streaming services that count
    streaming_release_combo: int = 3  # streaming link + release wording
    beat_marketplace: int = 5  # BeatStars / Airbit / Traktrain ... (producer)
    marketplace_beats_combo: int = 3  # marketplace + beats/prod wording
    first_person_release: int = 5  # "my new single"
    username_producer: int = 2
    instagram_category: int = 5  # Instagram business category "Musician/band"
    music_service: int = 6  # "music video director", "mixing & mastering for artists"
    non_music_service: int = 2  # "wedding photographer" -> other


@dataclass(frozen=True)
class ClassifierConfig:
    weights: ClassifierWeights = ClassifierWeights()
    # A music category needs at least this score, otherwise the profile is "other".
    min_category_score: int = 4
    # Below this top score the result is marked uncertain.
    min_confident_score: int = 5
    # topScore - secondScore below this margin: uncertain.
    uncertainty_margin: int = 3
    # Confidence curve: 1 - exp(-top / scale); bonus per strong signal (max 3 counted).
    confidence_scale: float = 6.0
    strong_bonus: int = 4
    negative_penalty: int = 5
    conflict_penalty: int = 8
    # "other" without music signals is fairly safe; each weak music hint lowers it.
    other_base_confidence: int = 75
    other_hint_penalty: int = 12
    # Strong local override: AI is skipped in "uncertain" mode above these.
    override_confidence: int = 95
    override_strong_signals: int = 2


DEFAULT_CONFIG = ClassifierConfig()
