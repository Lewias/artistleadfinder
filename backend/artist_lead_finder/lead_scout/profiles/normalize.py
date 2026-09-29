"""The one place that knows Instagram field aliases, count formats and link wrappers.

Providers hand raw values here; nothing else in the application reads alias names.
"""

import re
from urllib.parse import parse_qs, urlsplit

from ..candidates import normalize_instagram_username
from .model import PartialProfile

__all__ = [
    "normalize_instagram_username",
    "parse_instagram_count",
    "normalize_external_url",
    "dedupe_links",
    "profile_from_fields",
]

# Alias paths per field; dots walk nested objects ("edge_followed_by.count").
ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "pk", "pk_id", "user_id", "userId"),
    "username": ("username",),
    "full_name": ("full_name", "fullName"),
    "biography": ("biography", "bio"),
    "external_url": ("external_url", "externalUrl"),
    "followers_count": (
        "followers_count",
        "follower_count",
        "followersCount",
        "followers",
        "edge_followed_by.count",
    ),
    "following_count": (
        "following_count",
        "followingCount",
        "following",
        "edge_follow.count",
    ),
    "posts_count": (
        "media_count",
        "posts_count",
        "postsCount",
        "edge_owner_to_timeline_media.count",
    ),
    "category_name": (
        "category_name",
        "category",
        "categoryName",
        "business_category_name",
        "overall_category_name",
    ),
    "is_business": ("is_business_account", "is_business", "isBusiness"),
    "is_private": ("is_private", "isPrivate"),
    "phone_country_code": ("public_phone_country_code", "business_phone_country_code"),
}
EMAIL_KEYS = ("public_email", "business_email", "emails", "email")
PHONE_KEYS = (
    "public_phone_number",
    "business_phone_number",
    "contact_phone_number",
    "phones",
)
LINK_KEYS = ("bio_links", "bioLinks")
CAPTION_PATHS = ("edge_owner_to_timeline_media.edges",)
INTERNAL_HOSTS = re.compile(r"(^|\.)(instagram\.com|facebook\.com|meta\.com|threads\.net)$")
MISSING = object()

_SUFFIXES = {
    "k": 1_000,
    "тыс": 1_000,
    "m": 1_000_000,
    "млн": 1_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "млрд": 1_000_000_000,
}
_COUNT = re.compile(
    r"(\d[\d\s  .,']*)\s*(k|m|bn|b|тыс|млн|млрд)?\.?(?![a-zа-я])",
    re.I,
)


def parse_instagram_count(value) -> int | None:
    """Instagram counters as shown or served: 1,234 · 1.2K · 12K · 1.5M · 2,1 тыс. · 1 234.

    Returns None for anything that is not a count; never guesses.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, dict):
        return parse_instagram_count(value.get("count"))
    text = str(value).strip().lower()
    match = _COUNT.search(text)
    if not match:
        return None
    number = re.sub(r"[\s  ']", "", match.group(1)).rstrip(".,")
    suffix = (match.group(2) or "").lower()
    if not number:
        return None
    if suffix:
        # "1.2K" and "2,1 тыс." use the separator as a decimal point.
        number = number.replace(",", ".")
        if number.count(".") > 1:
            return None
        try:
            return int(round(float(number) * _SUFFIXES[suffix]))
        except ValueError:
            return None
    # Without a suffix separators group thousands: "1,234", "1.234", "12 345".
    groups = re.split(r"[.,]", number)
    if len(groups) > 1 and not all(len(group) == 3 for group in groups[1:]):
        return None
    try:
        return int("".join(groups))
    except ValueError:
        return None


def normalize_external_url(value) -> str | None:
    """Destination of an external link; unwraps l.instagram.com redirects without opening them."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    elif not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        if host == "l.instagram.com":
            target = parse_qs(parts.query).get("u", [""])[0]
            return normalize_external_url(target) if target else None
        if parts.scheme not in {"http", "https"} or not host or "." not in host:
            return None
        if parts.username or parts.password:
            return None
    except ValueError:
        return None
    return raw[:2048]


def dedupe_links(links, exclude=()) -> list[str]:
    """External links without duplicates (ignoring scheme, www and a trailing slash)."""

    def key(url: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        return host + parts.path.rstrip("/") + (f"?{parts.query}" if parts.query else "")

    seen = {key(url) for url in exclude if url}
    result = []
    for value in links:
        url = normalize_external_url(value)
        if not url or INTERNAL_HOSTS.search((urlsplit(url).hostname or "").lower()):
            continue
        if key(url) not in seen:
            seen.add(key(url))
            result.append(url)
    return result[:20]


def _lookup(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def _first(data: dict, name: str):
    """First alias present in the data; a present null still means "the source said none"."""
    for path in ALIASES[name]:
        value = _lookup(data, path)
        if value is not MISSING:
            return value
    return MISSING


def _text(value, limit: int) -> str | None:
    if value is MISSING:
        return None
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int)) and str(item).strip()]
    return []


def _links(value) -> list[str]:
    links = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            links.append(item)
        elif isinstance(item, dict):
            # Bio link objects: {"url": ..., "lynx_url": "https://l.instagram.com/?u=..."}.
            url = item.get("url") or item.get("lynx_url") or item.get("href")
            if isinstance(url, str):
                links.append(url)
    return links


def _captions(data: dict, limit: int) -> list[str]:
    captions = []
    for path in CAPTION_PATHS:
        edges = _lookup(data, path)
        for edge in edges if isinstance(edges, list) else []:
            node = edge.get("node", {}) if isinstance(edge, dict) else {}
            texts = _lookup(node, "edge_media_to_caption.edges")
            if isinstance(texts, list) and texts:
                text = _lookup(texts[0], "node.text") if isinstance(texts[0], dict) else MISSING
                if isinstance(text, str) and text.strip():
                    captions.append(text.strip()[:500])
            if len(captions) >= limit:
                return captions
    return captions


def profile_from_fields(
    data: dict, source: str, max_captions: int = 3, strategy: str = "api"
) -> PartialProfile:
    """Central alias layer: any Instagram user-shaped dict to a PartialProfile."""
    raw_id = _first(data, "id")
    username = _first(data, "username")
    counts = {
        name: parse_instagram_count(value) if (value := _first(data, name)) is not MISSING else None
        for name in ("followers_count", "following_count", "posts_count")
    }
    flags = {}
    for name in ("is_business", "is_private"):
        value = _first(data, name)
        flags[name] = bool(value) if value is not MISSING and value is not None else None
    emails = [email for key in EMAIL_KEYS for email in _strings(data.get(key))]
    phones = [phone for key in PHONE_KEYS for phone in _strings(data.get(key))]
    links = [link for key in LINK_KEYS for link in _links(data.get(key))]
    external = _first(data, "external_url")
    country = _first(data, "phone_country_code")
    profile = PartialProfile(
        source=source,
        username=normalize_instagram_username(username) if isinstance(username, str) else None,
        id=str(raw_id) if isinstance(raw_id, (str, int)) and str(raw_id).isdigit() else None,
        full_name=_text(_first(data, "full_name"), 200),
        biography=_text(_first(data, "biography"), 10000),
        external_url=(normalize_external_url(external) or "") if external is not MISSING else None,
        category_name=_text(_first(data, "category_name"), 120),
        emails=emails[:10],
        phones=phones[:10],
        phone_country_code=str(country) if country not in (MISSING, None, "") else None,
        recent_captions=_captions(data, max_captions),
        **counts,
        **flags,
    )
    profile.bio_links = dedupe_links(links, exclude=[profile.external_url or ""])
    if profile.external_url == "" and profile.bio_links:
        # Newer accounts carry only bio_links; the first one is the main external link.
        profile.external_url = profile.bio_links.pop(0)
    profile.strategies = {
        name: strategy
        for name in ("followers_count", "biography", "external_url", "category_name")
        if getattr(profile, name) is not None
    }
    return profile
