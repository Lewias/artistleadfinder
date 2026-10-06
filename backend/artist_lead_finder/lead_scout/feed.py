"""Publication facts from data Instagram already sent to an open grid page.

While a profile or tagged grid loads and scrolls, the web app receives its posts as JSON
(preloaded in the HTML and fetched page by page). Each media object names its code, its
owner and its co-authors. Reading that data lets discovery take
authors and collaborators from the grid itself instead of opening every publication: no
extra requests are made, only fewer pages are opened.

The walk does not depend on one response shape: any object with a shortcode, a media
marker and an owner username counts. A publication without such data is opened as before.
"""

import json
import re
from dataclasses import dataclass, field

from .candidates import normalize_instagram_username

SHORTCODE = re.compile(r"[A-Za-z0-9_-]{1,80}")
# Keys only media objects carry; a bare {"code": ...} elsewhere is not a publication.
MEDIA_MARKERS = ("media_type", "taken_at", "taken_at_timestamp", "product_type", "is_video")
MAX_DEPTH = 64
MAX_ITEMS = 1000
MAX_COLLABORATORS = 10


@dataclass
class MediaFacts:
    author: str
    collaborators: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"author": self.author, "collaborators": self.collaborators}


def _username(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("username")
    return normalize_instagram_username(value) if isinstance(value, str) else None


def _media(node: dict) -> tuple[str, MediaFacts] | None:
    code = node.get("code") or node.get("shortcode")
    if not isinstance(code, str) or not SHORTCODE.fullmatch(code):
        return None
    if not any(key in node for key in MEDIA_MARKERS):
        return None
    author = _username(node.get("owner")) or _username(node.get("user"))
    if not author:
        return None
    collaborators = []
    for item in node.get("coauthor_producers") or []:
        name = _username(item)
        if name and name != author and name not in collaborators:
            collaborators.append(name)
    return code, MediaFacts(author, collaborators[:MAX_COLLABORATORS])


def collect(data: object, found: dict[str, MediaFacts] | None = None) -> dict[str, MediaFacts]:
    """Media objects anywhere in a decoded JSON document, by shortcode."""
    found = {} if found is None else found
    stack: list[tuple[object, int]] = [(data, 0)]
    while stack and len(found) < MAX_ITEMS:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        if isinstance(node, dict):
            media = _media(node)
            if media:
                code, facts = media
                known = found.get(code)
                if known is None:
                    found[code] = facts
                else:
                    for name in facts.collaborators:
                        if name not in known.collaborators:
                            known.collaborators.append(name)
            stack.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            stack.extend((value, depth + 1) for value in node)
    return found


def collect_texts(texts: list[str], found: dict[str, MediaFacts] | None = None):
    """Same as `collect` over raw JSON texts; text that is not JSON is skipped."""
    found = {} if found is None else found
    for text in texts:
        # Instagram prefixes some API answers with a guard against JSON hijacking.
        text = text.removeprefix("for (;;);").strip()
        if not text.startswith(("{", "[")):
            continue
        try:
            collect(json.loads(text), found)
        except (ValueError, RecursionError):
            continue
    return found


def from_snapshot(snapshot: dict) -> dict[str, MediaFacts]:
    """The `feed` a grid snapshot carries, checked again on the core side."""
    raw = snapshot.get("feed")
    if not isinstance(raw, dict):
        return {}
    result = {}
    for code, item in list(raw.items())[:MAX_ITEMS]:
        if not isinstance(code, str) or not SHORTCODE.fullmatch(code) or not isinstance(item, dict):
            continue
        author = _username(item.get("author"))
        if not author:
            continue
        collaborators = [
            name
            for name in (_username(value) for value in item.get("collaborators") or [])
            if name and name != author
        ][:MAX_COLLABORATORS]
        result[code] = MediaFacts(author, collaborators)
    return result
