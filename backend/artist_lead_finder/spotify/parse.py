"""Facts from the Spotify web player's own data answers (pathfinder); no network here.

Every reader takes the decoded JSON of one answer and returns only what it found: a shape
Spotify changed returns nothing instead of raising.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

from ..browser_capture import RESERVED
from ..outreach.workspace import normalize_username

SPOTIFY_ID = r"[A-Za-z0-9]{22}"
URI = re.compile(rf"^spotify:(artist|playlist):({SPOTIFY_ID})$")
# open.spotify.com/artist/<id>, also with a locale prefix (/intl-de/) and query string.
LINK = re.compile(rf"^/(?:intl-[a-z-]+/)?(artist|playlist)/({SPOTIFY_ID})/?$", re.I)


def _get(value, *path):
    for key in path:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and isinstance(key, int) and -len(value) <= key < len(value):
            value = value[key]
        else:
            return None
    return value


def _items(value, *path) -> list:
    found = _get(value, *path)
    return found if isinstance(found, list) else []


def spotify_id(uri: object, kind: str) -> str | None:
    match = URI.match(str(uri or ""))
    return match.group(2) if match and match.group(1) == kind else None


def parse_link(text: str) -> tuple[str, str] | None:
    """("artist" | "playlist", id) from a link or a spotify: URI the user pasted."""
    text = text.strip()
    match = URI.match(text)
    if match:
        return match.group(1), match.group(2)
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if (parts.hostname or "").lower() != "open.spotify.com":
        return None
    match = LINK.match(parts.path)
    return (match.group(1).lower(), match.group(2)) if match else None


def instagram_username(links: list) -> str | None:
    """The artist's own Instagram from the links on their Spotify profile."""
    for link in links:
        if str(_get(link, "name") or "").upper() != "INSTAGRAM":
            continue
        url = str(_get(link, "url") or "").strip()
        try:
            parts = urlsplit(url if "://" in url else "https://" + url)
        except ValueError:
            continue
        host = (parts.hostname or "").lower()
        if host not in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
            continue
        path = [part for part in parts.path.split("/") if part]
        # instagram.com/_u/name opens the app; the name is what matters.
        if len(path) == 2 and path[0] == "_u":
            path = path[1:]
        if len(path) != 1 or path[0].lower() in RESERVED:
            continue
        username = normalize_username(path[0])
        if username:
            return username
    return None


def _release_date(value) -> date | None:
    year, month, day = (_get(value, key) for key in ("year", "month", "day"))
    if not isinstance(year, int):
        iso = str(_get(value, "isoString") or "")[:10]
        try:
            return date.fromisoformat(iso)
        except ValueError:
            return None
    try:
        return date(
            year, month if isinstance(month, int) else 1, day if isinstance(day, int) else 1
        )
    except ValueError:
        return None


@dataclass
class ArtistFacts:
    id: str
    name: str
    listeners: int
    followers: int
    instagram: str | None
    bio: str = ""
    release_name: str = ""
    release_date: date | None = None
    city: str = ""
    country: str = ""
    related: list[str] = field(default_factory=list)


def artist_facts(payload: dict) -> ArtistFacts | None:
    """queryArtistOverview → the artist, or None when Spotify has no such artist."""
    artist = _get(payload, "data", "artistUnion")
    artist_id = spotify_id(_get(artist, "uri"), "artist")
    if not artist_id:
        return None
    name = str(_get(artist, "profile", "name") or "").strip()
    listeners = _get(artist, "stats", "monthlyListeners")
    followers = _get(artist, "stats", "followers")
    bio = html.unescape(str(_get(artist, "profile", "biography", "text") or ""))
    # The newest of the latest release and the first single and album (each list is newest
    # first).
    releases = [_get(artist, "discography", "latest")]
    for kind in ("singles", "albums"):
        releases += [
            _get(item, "releases", "items", 0)
            for item in _items(artist, "discography", kind, "items")[:1]
        ]
    dated = [
        (when, str(_get(release, "name") or ""))
        for release in releases
        if isinstance(release, dict) and (when := _release_date(_get(release, "date")))
    ]
    newest = max(dated, default=(None, ""))
    city = _items(artist, "stats", "topCities", "items")[:1]
    related = [
        found
        for item in _items(artist, "relatedContent", "relatedArtists", "items")
        if (found := spotify_id(_get(item, "uri"), "artist"))
    ]
    return ArtistFacts(
        id=artist_id,
        name=name[:240] or artist_id,
        listeners=listeners if isinstance(listeners, int) and listeners >= 0 else 0,
        followers=followers if isinstance(followers, int) and followers >= 0 else 0,
        instagram=instagram_username(_items(artist, "profile", "externalLinks", "items")),
        bio=bio.strip()[:2000],
        release_name=newest[1][:240],
        release_date=newest[0],
        city=str(_get(city, 0, "city") or "")[:120],
        country=str(_get(city, 0, "country") or "")[:2],
        related=related,
    )


@dataclass
class PlaylistPage:
    name: str
    total: int
    artists: list[str]


def playlist_page(payload: dict) -> PlaylistPage | None:
    """fetchPlaylist → the artists of one page of tracks, in playlist order."""
    playlist = _get(payload, "data", "playlistV2")
    content = _get(playlist, "content")
    if not isinstance(content, dict):
        return None
    artists: list[str] = []
    for item in _items(content, "items"):
        for artist in _items(item, "itemV2", "data", "artists", "items"):
            found = spotify_id(_get(artist, "uri"), "artist")
            if found and found not in artists:
                artists.append(found)
    total = _get(content, "totalCount")
    return PlaylistPage(
        name=str(_get(playlist, "name") or "")[:240],
        total=total if isinstance(total, int) else 0,
        artists=artists,
    )


@dataclass
class FoundPlaylist:
    id: str
    name: str
    owner: str


def search_playlists(payload: dict) -> list[FoundPlaylist]:
    found = []
    for item in _items(payload, "data", "searchV2", "playlists", "items"):
        data = _get(item, "data")
        playlist_id = spotify_id(_get(data, "uri"), "playlist")
        if playlist_id:
            found.append(
                FoundPlaylist(
                    id=playlist_id,
                    name=str(_get(data, "name") or "")[:240],
                    owner=str(_get(data, "ownerV2", "data", "name") or "")[:160],
                )
            )
    return found


def search_artists(payload: dict) -> list[str]:
    return [
        found
        for item in _items(payload, "data", "searchV2", "artists", "items")
        if (found := spotify_id(_get(item, "data", "uri"), "artist"))
    ]
