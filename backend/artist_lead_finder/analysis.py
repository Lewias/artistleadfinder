"""Deterministic extraction; classifiers consume structured signals."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from .providers import Candidate

GENRE_PATTERNS = {
    "Hip-Hop": r"hip[ -]?hop|\brapper\b|\brap\b",
    "Trap": r"\btrap\b",
    "R&B": r"r&b|rnb|rhythm and blues",
    "Pop": r"\bpop\b",
    "Indie": r"\bindie\b",
    "Electronic": r"electronic|\bedm\b",
    "Rock": r"\brock\b",
    "Soul": r"\bsoul\b",
}


@dataclass(frozen=True)
class ProfileSignals:
    likely_artist: bool
    likely_producer: bool
    music_related: bool
    has_spotify: bool
    has_apple_music: bool
    has_youtube: bool
    has_soundcloud: bool
    has_linktree: bool
    recent_release_signal: bool
    activity_level: str
    activity_days: int | None
    possible_genres: list[str]
    reasons: list[str]


class ProfileAnalyzer:
    def analyze(self, candidate: Candidate, now: datetime | None = None) -> ProfileSignals:
        now = now or datetime.now(timezone.utc)
        bio = candidate.bio.casefold()
        text = f"{bio} {' '.join(candidate.recent_content).casefold()}"
        producer = bool(re.search(r"\b(producer|beatmaker|mixing|beats)\b", bio))
        artist = bool(re.search(r"\b(artist|rapper|singer|songwriter|band|musician)\b", bio))
        excluded = bool(re.search(r"\b(fan|fanpage|photographer|brand|coach)\b", bio))
        artist = artist and not excluded
        hosts = {urlparse(candidate.external_url).hostname or ""}
        for url in re.findall(r"https?://[^\s<>]+", candidate.bio):
            hosts.add(urlparse(url).hostname or "")

        def has_domain(domain: str) -> bool:
            return any(host == domain or host.endswith(f".{domain}") for host in hosts)

        spotify = has_domain("spotify.com")
        apple = has_domain("music.apple.com")
        youtube = has_domain("youtube.com") or has_domain("youtu.be")
        soundcloud = has_domain("soundcloud.com")
        linktree = has_domain("linktr.ee")
        release = bool(re.search(r"new (single|ep|album)|out now|release", text))
        music = bool(re.search(r"\b(music|artist|rapper|singer|songwriter|album|ep|beats)\b", bio))
        activity_days = None
        if candidate.last_activity_at:
            activity = candidate.last_activity_at
            if activity.tzinfo is None:
                activity = activity.replace(tzinfo=timezone.utc)
            activity_days = max(0, (now - activity).days)
        level = (
            "strong"
            if activity_days is not None and activity_days <= 7
            else "recent"
            if activity_days is not None and activity_days <= 30
            else "inactive"
        )
        genres = [genre for genre, pattern in GENRE_PATTERNS.items() if re.search(pattern, text)]
        reasons = []
        for match, reason in [
            (artist, "Указание на артиста в биографии"),
            (producer, "Указание на музыкального продюсера"),
            (spotify, "Ссылка Spotify"),
            (apple, "Ссылка Apple Music"),
            (soundcloud, "Ссылка SoundCloud"),
            (release, "Упоминание нового релиза"),
        ]:
            if match:
                reasons.append(reason)
        return ProfileSignals(
            artist,
            producer,
            music,
            spotify,
            apple,
            youtube,
            soundcloud,
            linktree,
            release,
            level,
            activity_days,
            genres,
            reasons,
        )
