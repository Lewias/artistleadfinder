"""The single candidate shape every discovery method produces."""

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

METHODS = ("post", "reel", "tagged", "story", "followers", "following", "comment")

# Instagram paths that look like usernames but are product pages.
SYSTEM_NAMES = {
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
    "instagram",
}
USERNAME = re.compile(r"[a-zA-Z0-9_.]{1,30}")


def clean_username(value: str) -> str | None:
    """Username from `@name`, `name` or a profile URL; None for system or foreign links."""
    value = (value or "").strip()
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if parsed.hostname not in {"instagram.com", "www.instagram.com"} or parsed.username:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1:
            return None
        value = parts[0]
    value = value.lstrip("@").rstrip(".").lower()
    if not USERNAME.fullmatch(value) or value in SYSTEM_NAMES:
        return None
    return value


def profile_link(username: str) -> str:
    return f"https://www.instagram.com/{username}/"


@dataclass(frozen=True)
class ScoutCandidate:
    username: str
    source_username: str
    method: str
    origin_id: str | None = None
    origin_url: str | None = None

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
        }

    def as_dict(self) -> dict:
        return asdict(self)
