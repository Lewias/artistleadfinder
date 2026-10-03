"""Candidate shape, username normalisation, post URL parsing and per-run candidate gate."""

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

METHODS = ("post", "reel", "tagged", "story", "followers", "following", "comment", "profile")

# Instagram paths that look like usernames but are product pages.
SYSTEM_PATHS = {
    "about",
    "accounts",
    "api",
    "challenge",
    "developer",
    "direct",
    "explore",
    "legal",
    "p",
    "privacy",
    "reel",
    "reels",
    "stories",
    "terms",
    "tv",
    "web",
}
# Official Instagram/Meta accounts that are never music leads.
SYSTEM_ACCOUNTS = {
    "instagram",
    "creators",
    "meta",
    "facebook",
    "threads",
    "instagramforbusiness",
    "design",
    "music",
    "instagramcomedy",
}
SYSTEM_NAMES = SYSTEM_PATHS | SYSTEM_ACCOUNTS
USERNAME = re.compile(r"[a-z0-9_.]{1,30}")
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
POST_PATH = re.compile(r"/(?:([A-Za-z0-9_.]{1,30})/)?(p|reel|reels|tv)/([A-Za-z0-9_-]{1,80})/?")


def normalize_instagram_username(value: str | None) -> str | None:
    """Lowercase username from `@name`, `name` or instagram.com/<name>; None if invalid.

    Rejects Instagram system paths, publication/story URLs and foreign hosts.
    """
    value = (value or "").strip()
    if not value:
        return None
    if "/" in value or value.lower().startswith(("http:", "https:", "instagram.com", "www.")):
        if not value.lower().startswith(("http://", "https://")):
            value = "https://" + value
        parsed = urlsplit(value)
        if (parsed.hostname or "").lower() not in INSTAGRAM_HOSTS or parsed.username:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1:
            return None
        value = parts[0]
    value = value.lstrip("@").strip().rstrip(".").lower()
    if not USERNAME.fullmatch(value) or value in SYSTEM_NAMES or ".." in value:
        return None
    return value


# Backwards-compatible name used by existing discovery code.
clean_username = normalize_instagram_username


@dataclass(frozen=True)
class ParsedPost:
    type: str  # "post" or "reel"
    shortcode: str
    canonical_url: str


def parse_instagram_post_url(value: str) -> ParsedPost | None:
    """Publication type, shortcode and canonical URL for /p/, /reel/, /reels/ and /tv/."""
    parsed = urlsplit((value or "").strip())
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in INSTAGRAM_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        return None
    match = POST_PATH.fullmatch(parsed.path)
    if not match:
        return None
    kind = "post" if match[2] == "p" else "reel"
    path = "reel" if match[2] == "reels" else match[2]
    author = f"{match[1].lower()}/" if match[1] else ""
    return ParsedPost(kind, match[3], f"https://www.instagram.com/{author}{path}/{match[3]}/")


def profile_link(username: str) -> str:
    return f"https://www.instagram.com/{username}/"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ScoutCandidate:
    """DiscoveredCandidate: what every provider yields into the Scout pipeline."""

    username: str
    source_username: str
    method: str
    origin_id: str | None = None
    origin_url: str | None = None
    discovered_at: datetime = field(default_factory=now_utc, compare=False)
    evidence_type: str | None = field(default=None, compare=False)
    confidence: float | None = field(default=None, compare=False)

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError(f"Unknown discovery method: {self.method}")

    def task(self) -> dict:
        """Queue entry for the profile page; keeps where the candidate came from."""
        return {
            "kind": "profile",
            "url": profile_link(self.username),
            "source": profile_link(self.source_username),
            "method": self.method,
            "origin_id": self.origin_id,
            "origin_url": self.origin_url,
            "discovered_at": self.discovered_at.isoformat(),
            "evidence_type": self.evidence_type,
        }

    def as_dict(self) -> dict:
        data = asdict(self)
        data["discovered_at"] = self.discovered_at.isoformat()
        return data


DiscoveredCandidate = ScoutCandidate


class CandidateGate:
    """Global validation before a candidate is yielded (in memory, no DB per element).

    Rejects invalid names, the source itself, ignore-list and system accounts, and
    anything already yielded in this scan run.
    """

    def __init__(
        self, source_username: str, ignore: set[str] | None = None, seen: set[str] | None = None
    ):
        self.source = normalize_instagram_username(source_username)
        self.ignore = {
            name for name in (normalize_instagram_username(i) for i in ignore or ()) if name
        }
        self.seen = seen if seen is not None else set()
        self.duplicates = 0
        self.rejected = 0

    def valid(self, value: str | None) -> str | None:
        """Validation without recording (comment authors may repeat to add evidence)."""
        name = normalize_instagram_username(value)
        if not name or name == self.source or name in self.ignore or name in SYSTEM_ACCOUNTS:
            return None
        return name

    def admit(self, value: str | None) -> str | None:
        name = normalize_instagram_username(value)
        if not name or name == self.source or name in self.ignore or name in SYSTEM_ACCOUNTS:
            self.rejected += 1
            return None
        if name in self.seen:
            self.duplicates += 1
            return None
        self.seen.add(name)
        return name
