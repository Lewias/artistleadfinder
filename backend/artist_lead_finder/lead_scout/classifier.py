"""Score-based local classifier: artist / producer / media / other, with evidence per rule."""

import re
from dataclasses import dataclass, field

CATEGORIES = ("artist", "producer", "media", "other")

ARTIST_WORDS = [
    "recording artist",
    "music artist",
    "rap artist",
    "rapper",
    "singer",
    "songwriter",
    "musician",
    "vocalist",
    "performer",
    "dj",
    "band",
    "рэпер",
    "певец",
    "певица",
    "музыкант",
    "исполнитель",
]
RELEASE_PHRASES = [
    "my new single",
    "my album",
    "my ep",
    "new single",
    "new album",
    "out now",
    "streaming now",
    "music video",
    "official video",
    "available now",
    "all platforms",
    "tour dates",
    "новый сингл",
    "новый альбом",
    "уже на всех площадках",
]
MUSIC_LINKS = [
    "spotify",
    "music.apple",
    "soundcloud",
    "bandcamp",
    "audiomack",
    "album.link",
    "song.link",
    "lnk.to",
    "ffm.to",
    "hyperfollow",
    "unitedmasters",
    "distrokid",
    "tunecore",
    "cdbaby",
]
PRODUCER_WORDS = [
    "music producer",
    "beatmaker",
    "beats",
    "prod by me",
    "prod.",
    "type beat",
    "drumkit",
    "drum kit",
    "loopkit",
    "sample pack",
    "midi kit",
    "soundkit",
    "composer",
    "битмейкер",
    "продюсер",
]
PRODUCER_LINKS = ["beatstars", "airbit", "traktrain", "soundee", "bsta.rs"]
PRODUCER_USERNAME = ["prodby", "prod", "producer", "beats", "beatmaker", "typebeats", "composer"]
MEDIA_WORDS = [
    "music news",
    "music blog",
    "music magazine",
    "music press",
    "radio",
    "podcast",
    "repost hub",
    "playlist curator",
    "curator",
    "promo page",
    "record label",
    "playlist",
]
SERVICE_WORDS = [
    "videographer",
    "video editor",
    "video director",
    "cinematographer",
    "filmmaker",
    "photographer",
    "audio engineer",
    "sound engineer",
    "recording engineer",
    "mixing",
    "mastering",
    "studio",
]
NEGATIVE_WORDS = [
    "forex",
    "casino",
    "betting",
    "onlyfans",
    "nails",
    "boutique",
    "realtor",
    "backup account",
    "fan page",
    "fanpage",
    "spam",
    "giveaway",
]
MUSIC_CONTEXT = re.compile(
    r"\b(?:music|hip.?hop|rap|r&b|trap|drill|beats?|artists?|songs?|albums?|singles?|studio)\b"
    r"|музык|рэп|хип.?хоп|трек",
    re.I,
)
# Instagram business categories that map directly to a category.
CATEGORY_NAMES = {
    "artist": ["musician/band", "musician", "artist", "band", "dj", "singer", "rapper"],
    "producer": ["music producer", "producer", "music production studio"],
    "media": [
        "record label",
        "media/news company",
        "media",
        "news & media website",
        "radio station",
    ],
}


@dataclass
class ProfileText:
    username: str
    full_name: str = ""
    biography: str = ""
    category_name: str = ""
    external_url: str = ""
    bio_links: list[str] = field(default_factory=list)
    recent_captions: list[str] = field(default_factory=list)


@dataclass
class Classification:
    category: str
    score: int
    confidence: int
    reasons: list[str]
    uncertain: bool
    scores: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "score": self.score,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "uncertain": self.uncertain,
        }


def _phrase(words: list[str]) -> re.Pattern:
    escaped = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    # Word boundaries that also work for Cyrillic and for phrases ending in punctuation.
    return re.compile(rf"(?<![\w])(?:{escaped})(?![\w])", re.I)


PATTERNS = {
    "artist": _phrase(ARTIST_WORDS),
    "release": _phrase(RELEASE_PHRASES),
    "producer": _phrase(PRODUCER_WORDS),
    "media": _phrase(MEDIA_WORDS),
    "service": _phrase(SERVICE_WORDS),
    "negative": _phrase(NEGATIVE_WORDS),
}
UNCERTAIN_BELOW = 6
MIN_CATEGORY_SCORE = 4


def classify(profile: ProfileText) -> Classification:
    scores = {category: 0 for category in CATEGORIES}
    reasons: list[str] = []

    def add(category: str, points: int, reason: str) -> None:
        scores[category] += points
        if reason not in reasons:
            reasons.append(reason)

    fields = {
        "Bio": profile.biography,
        "Name": profile.full_name,
        "Category": profile.category_name,
    }
    captions = "\n".join(profile.recent_captions)
    links = " ".join([profile.external_url, *profile.bio_links]).lower()
    all_text = "\n".join([*fields.values(), captions])

    for label, text in fields.items():
        for match in {m.lower() for m in PATTERNS["artist"].findall(text)}:
            add("artist", 4, f"{label} contains {match}")
        for match in {m.lower() for m in PATTERNS["producer"].findall(text)}:
            add("producer", 4, f"{label} contains {match}")
    profile_text = "\n".join(fields.values())
    for match in {m.lower() for m in PATTERNS["release"].findall(profile_text)}:
        add("artist", 3, f"Release phrase: {match}")
    # Captions alone are weak: media and service accounts post about other people's releases.
    for match in {m.lower() for m in PATTERNS["release"].findall(captions)} - {
        m.lower() for m in PATTERNS["release"].findall(profile_text)
    }:
        add("artist", 1, f"Recent caption: {match}")
    for match in {m.lower() for m in PATTERNS["artist"].findall(captions)}:
        add("artist", 1, f"Recent caption mentions {match}")
    for link in MUSIC_LINKS:
        if link in links:
            add("artist", 4, f"{link.split('.')[0].capitalize()} link found")
    for link in PRODUCER_LINKS:
        if link in links:
            add("producer", 5, f"{link.split('.')[0].capitalize()} link found")
    username = profile.username.lower()
    for token in PRODUCER_USERNAME:
        if token in username:
            add("producer", 3, f"Username contains {token}")
            break

    category_name = profile.category_name.lower().strip()
    for category, names in CATEGORY_NAMES.items():
        if category_name and category_name in names:
            add(category, 5, f"Instagram category: {profile.category_name}")

    # Media and creative services count only around music.
    music_context = bool(MUSIC_CONTEXT.search(all_text) or scores["artist"] or scores["producer"])
    media_hits = {m.lower() for m in PATTERNS["media"].findall(all_text)}
    service_hits = {m.lower() for m in PATTERNS["service"].findall(all_text)}
    if music_context:
        for match in media_hits:
            add("media", 4, f"Media signal: {match}")
        for match in service_hits:
            add("media", 3, f"Music service signal: {match}")
    elif media_hits or service_hits:
        reasons.append("Media/service words without music context")

    negatives = {m.lower() for m in PATTERNS["negative"].findall(all_text)}
    for match in negatives:
        reasons.append(f"Negative signal: {match}")
    penalty = 5 * len(negatives)
    for category in ("artist", "producer", "media"):
        scores[category] = max(0, scores[category] - penalty)

    ranked = sorted(((scores[c], c) for c in ("artist", "producer", "media")), reverse=True)
    (best, category), (second, _) = ranked[0], ranked[1]
    if best < MIN_CATEGORY_SCORE:
        category = "other"
    confidence = round(100 * best / (best + second + 4)) if best else (80 if negatives else 50)
    uncertain = (
        category == "other"
        and best > 0
        or (category != "other" and (best < UNCERTAIN_BELOW or best - second < 3))
    )
    return Classification(
        category=category,
        score=best,
        confidence=max(1, min(99, confidence)),
        reasons=reasons,
        uncertain=bool(uncertain),
        scores=scores,
    )
