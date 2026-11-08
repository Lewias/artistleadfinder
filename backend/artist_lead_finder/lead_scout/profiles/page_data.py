"""Users in the JSON Instagram already sent to an open page.

A profile page receives its owner as JSON (preloaded in the HTML or fetched by the web
app), and lists, grids and publications carry light user objects of everyone they show.
Reading that data costs no request:

- `profile_user`  the page owner's fields, merged from every object that names them;
- `people`        light facts (private, verified, followers) of every user on a page,
                  used to skip candidates before their profile is opened;
- `merge_partials` JSON fields first, the DOM reading fills the gaps.

The walk does not depend on one response shape: any object with a matching `username`
and profile-like keys counts.
"""

import json
from collections.abc import Iterable, Iterator

from ..candidates import normalize_instagram_username
from .model import PartialProfile
from .normalize import parse_instagram_count, profile_from_fields

MAX_DEPTH = 64
MAX_NODES = 400_000
MAX_PEOPLE = 3000
MAX_CAPTIONS = 12
# Keys only a profile-shaped user object carries (an owner stub has just id/username).
PROFILE_KEYS = (
    "biography",
    "follower_count",
    "edge_followed_by",
    "following_count",
    "edge_follow",
    "media_count",
    "edge_owner_to_timeline_media",
    "external_url",
    "bio_links",
    "category",
    "category_name",
    "public_email",
    "business_email",
    "public_phone_number",
    "business_phone_number",
    "contact_phone_number",
    "is_business_account",
    "is_business",
)
# Heavy media fields are never copied out of a response.
DROP_KEYS = frozenset(
    {
        "display_resources",
        "display_url",
        "thumbnail_src",
        "thumbnail_resources",
        "video_url",
        "video_versions",
        "image_versions2",
        "dash_info",
        "profile_pic_url",
        "profile_pic_url_hd",
        "hd_profile_pic_url_info",
        "hd_profile_pic_versions",
        "edge_felix_video_timeline",
        "edge_saved_media",
        "edge_media_collections",
        "edge_mutual_followed_by",
        "edge_related_profiles",
        "carousel_media",
        "clips_metadata",
    }
)


def documents(texts: Iterable[str]) -> Iterator[object]:
    """Decoded JSON documents of raw response texts; anything else is skipped."""
    for text in texts:
        # Instagram prefixes some API answers with a guard against JSON hijacking.
        text = str(text).removeprefix("for (;;);").strip()
        if not text.startswith(("{", "[")):
            continue
        try:
            yield json.loads(text)
        except (ValueError, RecursionError):
            continue


def _walk(data: object) -> Iterator[dict]:
    stack: list[tuple[object, int]] = [(data, 0)]
    seen = 0
    while stack and seen < MAX_NODES:
        node, depth = stack.pop()
        seen += 1
        if depth > MAX_DEPTH:
            continue
        if isinstance(node, dict):
            yield node
            stack.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            stack.extend((value, depth + 1) for value in node)


def _name(node: dict) -> str | None:
    value = node.get("username")
    return normalize_instagram_username(value) if isinstance(value, str) else None


def _richness(node: dict) -> int:
    return sum(1 for key in PROFILE_KEYS if node.get(key) not in (None, "", [], {}))


def _light(value: object, depth: int = 0) -> object:
    """A copy without media blobs, bounded in depth, list length and string size."""
    if depth > 8:
        return None
    if isinstance(value, str):
        return value[:10000]
    if isinstance(value, list):
        return [_light(item, depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        return {key: _light(item, depth + 1) for key, item in value.items() if key not in DROP_KEYS}
    return value


def _caption(node: dict) -> str | None:
    caption = node.get("caption")
    if isinstance(caption, dict) and isinstance(caption.get("text"), str):
        return caption["text"]
    if isinstance(caption, str):
        return caption
    edges = (node.get("edge_media_to_caption") or {}).get("edges")
    if isinstance(edges, list) and edges and isinstance(edges[0], dict):
        text = (edges[0].get("node") or {}).get("text")
        return text if isinstance(text, str) else None
    return None


def _owner(node: dict) -> str | None:
    for key in ("owner", "user"):
        value = node.get(key)
        if isinstance(value, dict):
            name = _name(value)
            if name:
                return name
    return None


def profile_user(texts: Iterable[str], username: str) -> dict | None:
    """The owner of a profile page, from every JSON object that names them.

    The richest object goes first and the others only fill keys it did not have, so a
    stub ({id, username}) never overrides real data. Captions of the owner's own
    publications come along as `_captions`. None when no profile-shaped object is there.
    """
    target = normalize_instagram_username(username)
    if not target:
        return None
    found: list[dict] = []
    captions: list[str] = []
    for document in documents(texts):
        for node in _walk(document):
            if _name(node) == target and _richness(node):
                found.append(node)
            elif len(captions) < MAX_CAPTIONS and _owner(node) == target:
                text = _caption(node)
                if text and text.strip() and text.strip()[:500] not in captions:
                    captions.append(text.strip()[:500])
    if not found:
        return None
    found.sort(key=_richness, reverse=True)
    merged: dict = {}
    for node in found:
        for key, value in node.items():
            if key in DROP_KEYS:
                continue
            # A stated null stays ("the source said none"); a real value fills an empty one.
            if key not in merged or (
                merged[key] in (None, "", [], {}) and value not in (None, "", [], {})
            ):
                merged[key] = _light(value)
    if captions:
        merged["_captions"] = captions
    return merged


def user_id(user: dict | None) -> str | None:
    for key in ("pk", "id", "pk_id"):
        value = (user or {}).get(key)
        if isinstance(value, (str, int)) and str(value).isdigit():
            return str(value)
    return None


def people(texts: Iterable[str]) -> dict[str, dict]:
    """Light facts of every user object on a page: {username: {is_private, ...}}.

    Only facts the data states are kept (a missing flag stays missing), so a hint can
    only ever skip a candidate the profile check would skip as well.
    """
    found: dict[str, dict] = {}
    for document in documents(texts):
        for node in _walk(document):
            name = _name(node)
            if not name:
                continue
            facts = found.get(name, {})
            if "id" not in facts and (uid := user_id(node)):
                # Lets a fast request ask for the profile the way its page does.
                facts["id"] = uid
            for key in ("is_private", "is_verified"):
                if isinstance(node.get(key), bool) and key not in facts:
                    facts[key] = node[key]
            if "followers" not in facts:
                for key in ("follower_count", "edge_followed_by"):
                    count = parse_instagram_count(node.get(key))
                    if count is not None:
                        facts["followers"] = count
                        break
            if facts:
                found[name] = facts
            if len(found) >= MAX_PEOPLE:
                return found
    return found


def clean_hint(value: object) -> dict | None:
    """A hint as it comes back from a stored task: only known keys and types."""
    if not isinstance(value, dict):
        return None
    hint = {
        key: value[key] for key in ("is_private", "is_verified") if isinstance(value.get(key), bool)
    }
    followers = value.get("followers")
    if isinstance(followers, int) and not isinstance(followers, bool) and followers >= 0:
        hint["followers"] = followers
    uid = value.get("id")
    if isinstance(uid, str) and uid.isdigit() and len(uid) <= 30:
        hint["id"] = uid
    return hint or None


# ---------- JSON partial + DOM partial ----------

NUMERIC_FIELDS = ("followers_count", "following_count", "posts_count")
TEXT_FIELDS = ("full_name", "biography", "external_url", "category_name", "phone_country_code")
FLAG_FIELDS = ("is_business", "is_private")
LIST_FIELDS = ("emails", "phones", "bio_links", "recent_captions")


def _dedupe(values) -> list:
    seen, result = set(), []
    for value in values:
        key = value.strip().lower() if isinstance(value, str) else value
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def merge_partials(data: PartialProfile, page: PartialProfile) -> PartialProfile:
    """Page JSON is primary for structured and numeric data; the DOM fills what is missing.

    A value is never replaced by an empty one and lists are unioned without duplicates.
    """
    merged = PartialProfile(
        source="api",
        username=data.username or page.username,
        id=data.id or page.id,
        contact_text=page.contact_text or data.contact_text,
        strategies={**page.strategies, **data.strategies},
    )
    for name in NUMERIC_FIELDS:
        value = getattr(data, name)
        setattr(merged, name, value if value is not None else getattr(page, name))
    for name in TEXT_FIELDS:
        first, second = getattr(data, name), getattr(page, name)
        # Non-empty beats empty; an explicit empty answer beats "unknown".
        setattr(merged, name, first or second or (first if first is not None else second))
        if not first and second:
            merged.strategies[name] = page.strategies.get(name, "browser")
    for name in FLAG_FIELDS:
        first, second = getattr(data, name), getattr(page, name)
        setattr(merged, name, first if first is not None else second)
    for name in LIST_FIELDS:
        setattr(merged, name, _dedupe([*getattr(data, name), *getattr(page, name)]))
    # Captions: the page data's own list when it has one (the DOM reads image alt texts).
    merged.recent_captions = list(data.recent_captions or page.recent_captions)
    external = (merged.external_url or "").rstrip("/")
    merged.bio_links = [link for link in merged.bio_links if link.rstrip("/") != external]
    return merged


def partial_from_user(user: dict, max_captions: int) -> PartialProfile:
    """Page JSON user -> PartialProfile through the central alias layer."""
    partial = profile_from_fields(user, "api", max_captions=max_captions, strategy="page_json")
    if not partial.recent_captions:
        partial.recent_captions = [
            str(text) for text in user.get("_captions") or [] if isinstance(text, str)
        ][:max_captions]
    return partial
