"""Browser fallback provider: the profile page read by capture.js in the existing tab.

capture.js holds the selectors and strategy chains (SELECTORS/TEXT blocks); this module
turns its bounded snapshot into a PartialProfile. Counters are parsed here, centrally.
"""

import re

from ...browser_capture import count_from, parse_snapshot
from .model import NOT_FOUND, PARSER_ERROR, PartialProfile, ProfileResolveError
from .normalize import dedupe_links, normalize_external_url, parse_instagram_count

# Instagram business/creator categories shown under the profile name (text fallback).
KNOWN_CATEGORIES = {
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
    "музыкант/группа",
    "музыкант",
    "исполнитель",
    "автор",
    "цифровой автор",
    "общественный деятель",
}
POSTS_TEXT = re.compile(
    r"(\d[\d\s  .,]*\s*(?:k|m|тыс\.?|млн\.?)?)\s*(?:posts?|публикаци\w*)\b", re.I
)


def _category_from_header(header: str) -> str | None:
    for line in header.splitlines():
        if line.strip().lower() in KNOWN_CATEGORIES:
            return line.strip()
    return None


def _field(profile: dict, name: str) -> tuple[str | None, str | None]:
    """(value, strategy) of a capture.js field that went through a strategy chain."""
    item = profile.get(name)
    if isinstance(item, dict) and isinstance(item.get("value"), str) and item["value"].strip():
        return item["value"].strip(), str(item.get("strategy") or "dom")[:40]
    return None, None


def _stat(profile: dict, name: str) -> tuple[int | None, str | None]:
    item = (profile.get("stats") or {}).get(name)
    if isinstance(item, dict):
        value = parse_instagram_count(item.get("raw"))
        if value is not None:
            return value, str(item.get("strategy") or "dom")[:40]
    return None, None


class BrowserProfileProvider:
    """Fallback provider: parses the profile page opened in the application's browser."""

    name = "browser"

    def get_profile(
        self, username: str, snapshot: dict, expected_url: str, max_captions: int = 3
    ) -> PartialProfile:
        if snapshot.get("unavailable"):
            raise ProfileResolveError(NOT_FOUND, "profile page is not available")
        try:
            candidate, evidence = parse_snapshot(snapshot, expected_url)
        except ValueError as error:
            raise ProfileResolveError(PARSER_ERROR, str(error)) from error
        header = str(snapshot.get("header", ""))[:12000]
        description = str(snapshot.get("description", ""))[:12000]
        page = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
        unknown = set(evidence["unknown_fields"])
        strategies: dict[str, str] = {}

        counts = {}
        for name, key, text_label in (
            ("followers_count", "followers", "followers"),
            ("following_count", "following", "following"),
            ("posts_count", "posts", None),
        ):
            value, strategy = _stat(page, key)
            if value is None and text_label and text_label not in unknown:
                # Header text first, then the meta description (both read by parse_snapshot).
                in_header = count_from(header, text_label) is not None
                value = getattr(candidate, text_label)
                strategy = "visible_text" if in_header else "meta_description"
            if value is None and key == "posts":
                match = POSTS_TEXT.search(header + "\n" + description)
                value = parse_instagram_count(match.group(1)) if match else None
                strategy = "visible_text" if value is not None else None
            counts[name] = value
            if strategy:
                strategies[name] = strategy

        biography, strategy = _field(page, "bio")
        if biography is None and "bio" not in unknown:
            biography, strategy = candidate.bio, evidence.get("bio_method", "dom")
        if strategy:
            strategies["biography"] = strategy
        full_name, _ = _field(page, "full_name")
        # Category: short header lines near the name first, then any header line.
        lines = (page.get("category") or {}).get("candidates") or []
        category = _category_from_header("\n".join(str(line) for line in lines[:12]))
        strategy = "header_line"
        if category is None:
            category, strategy = _category_from_header(header), "known_category_line"
        if category:
            strategies["category_name"] = strategy
        raw_links = [str(link) for link in snapshot.get("links", []) if isinstance(link, str)]
        external = normalize_external_url(snapshot.get("external_url")) or (
            normalize_external_url(raw_links[0]) if raw_links else None
        )
        if external:
            strategies["external_url"] = "header_link"
        contacts = page.get("contacts") if isinstance(page.get("contacts"), dict) else {}
        raw_id, _ = _field(page, "user_id")
        raw_id = raw_id or str(snapshot.get("user_id") or "")
        captions = [
            str(text).strip()[:500]
            for text in snapshot.get("captions", [])
            if isinstance(text, str) and text.strip()
        ][:max_captions]
        return PartialProfile(
            source="browser",
            username=candidate.username,
            id=raw_id if raw_id.isdigit() else None,
            full_name=full_name or candidate.display_name,
            biography=biography,
            # An empty header without links is a real "no link"; a failed read stays None.
            external_url=external or ("" if header else None),
            category_name=category,
            is_business=True if category else None,
            is_private=bool(snapshot.get("private", False)),
            emails=[str(item) for item in contacts.get("emails", []) if isinstance(item, str)][:10],
            phones=[str(item) for item in contacts.get("phones", []) if isinstance(item, str)][:10],
            bio_links=dedupe_links(raw_links, exclude=[external or ""]),
            recent_captions=captions,
            contact_text=str(contacts.get("text") or "")[:2000],
            strategies=strategies,
            **counts,
        )
