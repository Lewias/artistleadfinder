"""resolve_instagram_profile: one profile model from the profile page snapshot.

There is no official Instagram API provider in this application (the Meta adapter is
unavailable), so the browser DOM snapshot read by capture.js is the source. Selectors
stay in capture.js; parsing of counts and bio stays in browser_capture.parse_snapshot.
"""

import re
from dataclasses import asdict, dataclass, field

from ..browser_capture import parse_snapshot
from ..providers import Candidate
from .classifier import ProfileText
from .contacts import extract_emails, extract_phones

# Instagram business/creator categories shown under the profile name.
KNOWN_CATEGORIES = [
    "musician/band",
    "musician",
    "artist",
    "band",
    "dj",
    "music producer",
    "producer",
    "record label",
    "music production studio",
    "recording studio",
    "media/news company",
    "news & media website",
    "radio station",
    "podcast",
    "blogger",
    "personal blog",
    "public figure",
    "digital creator",
    "video creator",
    "photographer",
    "videographer",
    "entertainment website",
    "musician/band",
]
POSTS = re.compile(r"(\d[\d   .,]*\s*(?:k|m|тыс\.?|млн\.?)?)\s*(?:posts?|публикаци\w*)\b", re.I)


@dataclass
class ResolvedProfile:
    id: str | None
    username: str
    full_name: str
    biography: str
    external_url: str
    followers_count: int | None
    following_count: int | None
    posts_count: int | None
    category_name: str
    is_business: bool
    is_private: bool
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    bio_links: list[str] = field(default_factory=list)
    recent_captions: list[str] = field(default_factory=list)

    def text(self) -> ProfileText:
        return ProfileText(
            username=self.username,
            full_name=self.full_name,
            biography=self.biography,
            category_name=self.category_name,
            external_url=self.external_url,
            bio_links=self.bio_links,
            recent_captions=self.recent_captions,
        )

    def as_dict(self) -> dict:
        return asdict(self)


def _count(text: str) -> int | None:
    match = POSTS.search(text)
    if not match:
        return None
    value = re.sub(r"[\s  ]", "", match.group(1)).lower()
    multiplier = 1
    suffix = re.search(r"(k|m|тыс\.?|млн\.?)$", value)
    if suffix:
        multiplier = 1000 if suffix.group(1).startswith(("k", "тыс")) else 1_000_000
        value = value[: suffix.start()].replace(",", ".")
    else:
        value = value.replace(",", "").replace(".", "")
    try:
        return int(float(value) * multiplier)
    except ValueError:
        return None


def _category(header: str) -> str:
    known = set(KNOWN_CATEGORIES)
    for line in header.splitlines():
        cleaned = line.strip()
        if cleaned.lower() in known:
            return cleaned
    return ""


def resolve_instagram_profile(
    snapshot: dict, expected_url: str
) -> tuple[ResolvedProfile, Candidate, dict]:
    """Profile model plus the CRM candidate and capture evidence used to save the lead.

    Raises ValueError when the page is not a loaded profile.
    """
    candidate, evidence = parse_snapshot(snapshot, expected_url)
    header = str(snapshot.get("header", ""))[:12000]
    description = str(snapshot.get("description", ""))[:12000]
    unknown = set(evidence["unknown_fields"])
    raw_id = str(snapshot.get("user_id") or "")
    links = [str(link)[:2048] for link in snapshot.get("links", []) if isinstance(link, str)][:10]
    captions = [str(text)[:500] for text in snapshot.get("captions", []) if isinstance(text, str)][
        :8
    ]
    external = candidate.external_url or (links[0] if links else "")
    category = _category(header)
    contact_text = "\n".join([candidate.bio, header, description])
    profile = ResolvedProfile(
        id=raw_id if raw_id.isdigit() else None,
        username=candidate.username,
        full_name=candidate.display_name,
        biography=candidate.bio,
        external_url=external,
        followers_count=None if "followers" in unknown else candidate.followers,
        following_count=None if "following" in unknown else candidate.following,
        posts_count=_count(header + "\n" + description),
        category_name=category,
        is_business=bool(category),
        is_private=candidate.is_private,
        emails=extract_emails(contact_text),
        phones=extract_phones(contact_text),
        bio_links=[link for link in links if link != external],
        recent_captions=captions,
    )
    if profile.id:
        candidate = candidate.model_copy(update={"platform_user_id": profile.id})
    return profile, candidate, evidence
