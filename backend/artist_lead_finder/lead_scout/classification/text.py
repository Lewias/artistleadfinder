"""Classifier input: profile fields normalised for matching, originals kept for evidence."""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from .model import ProfileText

# Separators that Instagram bios use between roles ("Rapper | Singer • ATL").
SEPARATORS = re.compile(r"[|•·/\\,;:!?()\[\]{}\"“”«»*_~+=<>–—-]+")
SPACES = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """Lowercase, fold fancy Unicode letters (NFKC: 𝐫𝐚𝐩𝐩𝐞𝐫 -> rapper), turn separators
    and hyphens into spaces ("pre-save" -> "pre save") and collapse whitespace."""
    text = unicodedata.normalize("NFKC", value or "").lower().replace("’", "'")
    return SPACES.sub(" ", SEPARATORS.sub(" ", text)).strip()


def link_host(url: str) -> str:
    try:
        host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


@dataclass
class ClassificationText:
    username: str
    # Normalised fields, matched by the rules.
    name: str
    bio: str
    category: str
    captions: list[str]
    links: list[str]
    hosts: list[str]
    # Original values, shown as evidence.
    original: dict

    @property
    def profile(self) -> str:
        """Self-description: name, bio and Instagram category."""
        return "\n".join(filter(None, [self.name, self.bio, self.category]))

    @property
    def all_text(self) -> str:
        return "\n".join(filter(None, [self.profile, *self.captions]))

    @property
    def combined(self) -> str:
        """Everything in one normalised string (username, fields, links, captions)."""
        return "\n".join(filter(None, [self.username, self.all_text, *self.links]))


def build_profile_classification_text(profile) -> ClassificationText:
    """Accepts ProfileText or NormalizedInstagramProfile (the same field names)."""
    if not isinstance(profile, ProfileText):
        profile = profile.text()
    links = [url for url in dict.fromkeys([profile.external_url, *profile.bio_links]) if url]
    return ClassificationText(
        username=(profile.username or "").lower().lstrip("@"),
        name=normalize_text(profile.full_name),
        bio=normalize_text(profile.biography),
        category=normalize_text(profile.category_name),
        captions=[normalize_text(caption) for caption in profile.recent_captions if caption],
        links=[link.lower() for link in links],
        hosts=[host for host in (link_host(link) for link in links) if host],
        original={
            "username": profile.username,
            "full_name": profile.full_name,
            "biography": profile.biography,
            "category_name": profile.category_name,
            "links": links,
        },
    )
