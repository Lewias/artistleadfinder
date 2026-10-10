"""The Spotify web player in our own hidden Chromium, without a Spotify account.

The player gets an anonymous token by itself; we read its data requests (headers and the
persisted query hashes) once and then ask the same data API from inside the page, as the
player does. Everything here runs on the search thread: sync Playwright objects never
leave the thread that created them.
"""

import json
import logging
from urllib.parse import quote

from .. import chromium_runtime  # noqa: F401 - sets where Playwright finds our Chromium

log = logging.getLogger(__name__)

API = "https://api-partner.spotify.com/pathfinder/v2/query"
PLAYER = "https://open.spotify.com"
# Seen in the player on 2026-10-10; a stale one is learned again from the page.
KNOWN_HASHES = {
    "queryArtistOverview": "9f8134ef565e78621f1e1793555bd6633c5ac144ae0f89604ed3ae3f80b3c8e6",
    "fetchPlaylist": "8964e8eafb21aa992a7d951d256d83285c04be2105d209262901de70cb97584a",
    "searchPlaylists": "d520014e748f9ea44f7707d8df1819867ac1205e8b7f3e28f22fe5fc858921b1",
    "searchArtists": "7bf95d754fdbe32c8b161fbbe54d1ae50974900df4dce4c8f1afcbcad153224d",
}
# Request headers of the player's own data requests that our requests repeat.
KEPT_HEADERS = {
    "authorization",
    "client-token",
    "app-platform",
    "spotify-app-version",
    "content-type",
    "accept",
    "accept-language",
}
FETCH = """async ([url, headers, body]) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, {
      method: 'POST', headers, body: JSON.stringify(body), signal: controller.signal,
    });
    return [response.status, await response.text()];
  } catch (error) {
    return [0, String(error)];
  } finally {
    clearTimeout(timer);
  }
}"""
LEARN_SECONDS = 25


class SpotifyError(Exception):
    """A user-facing reason the search cannot go on."""


class SpotifyRateLimited(SpotifyError):
    pass


def page_for(operation: str, variables: dict) -> str:
    """The player page whose opening makes the player send this request itself."""
    uri = str(variables.get("uri") or "")
    if operation == "queryArtistOverview":
        return f"{PLAYER}/artist/{uri.rsplit(':', 1)[-1]}"
    if operation == "fetchPlaylist":
        return f"{PLAYER}/playlist/{uri.rsplit(':', 1)[-1]}"
    term = quote(str(variables.get("searchTerm") or "music"), safe="")
    tab = "artists" if operation == "searchArtists" else "playlists"
    return f"{PLAYER}/search/{term}/{tab}"


class SpotifyWeb:
    def __init__(self, ready=None, playwright_factory=None):
        # ready(): raises while Chromium is still being downloaded (macOS first start).
        self.ready = ready
        self.playwright_factory = playwright_factory
        self.playwright = None
        self.browser = None
        self.page = None
        self.cdp = None
        self.headers: dict[str, str] = {}
        self.hashes: dict[str, str] = dict(KNOWN_HASHES)

    def start(self) -> None:
        if self.ready:
            self.ready()
        if self.playwright_factory is None:
            from playwright.sync_api import sync_playwright

            self.playwright_factory = lambda: sync_playwright().start()
        self.playwright = self.playwright_factory()
        try:
            self.browser = self.playwright.chromium.launch(headless=True, channel="chromium")
        except Exception:
            raise SpotifyError("Не удалось запустить Chromium для поиска в Spotify.") from None
        context = self.browser.new_context(locale="en-US")
        self.page = context.new_page()
        self.cdp = context.new_cdp_session(self.page)
        self.cdp.send("Network.enable")
        self.cdp.on("Network.requestWillBeSent", self._sent)
        self.cdp.on("Network.requestWillBeSentExtraInfo", self._headers)

    def close(self) -> None:
        for step in (
            lambda: self.browser and self.browser.close(),
            lambda: self.playwright and self.playwright.stop(),
        ):
            try:
                step()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        self.browser = self.playwright = self.page = self.cdp = None

    # ---------- what the player itself sends ----------

    def _sent(self, event: dict) -> None:
        request = event.get("request") or {}
        if not str(request.get("url", "")).startswith(API):
            return
        try:
            body = json.loads(request.get("postData") or "")
            self.hashes[body["operationName"]] = body["extensions"]["persistedQuery"]["sha256Hash"]
        except (ValueError, KeyError, TypeError):
            pass

    def _headers(self, event: dict) -> None:
        headers = {
            str(key).lower(): str(value) for key, value in (event.get("headers") or {}).items()
        }
        if "/pathfinder/" in headers.get(":path", "") and headers.get("authorization"):
            self.headers = {key: value for key, value in headers.items() if key in KEPT_HEADERS}

    def _learn(self, operation: str, variables: dict) -> None:
        """Open the page that makes the player send this request; keep its headers and hash."""
        self.headers = {}
        try:
            self.page.goto(
                page_for(operation, variables), wait_until="domcontentloaded", timeout=45000
            )
        except Exception:
            raise SpotifyError("Spotify не открывается. Проверьте интернет.") from None
        for _ in range(LEARN_SECONDS * 2):
            # Sync Playwright delivers the browser's events only during a call like this one.
            self.page.wait_for_timeout(500)
            if self.headers and operation in self.hashes:
                return
        if not self.headers:
            raise SpotifyError("Spotify не выдал доступ веб-плееру. Попробуйте позже.")

    # ---------- data ----------

    def query(self, operation: str, variables: dict) -> dict:
        learned = False
        for _ in range(3):
            if not self.headers or operation not in self.hashes:
                self._learn(operation, variables)
                learned = True
            body = {
                "variables": variables,
                "operationName": operation,
                "extensions": {
                    "persistedQuery": {"version": 1, "sha256Hash": self.hashes[operation]}
                },
            }
            status, text = self.page.evaluate(FETCH, [API, self.headers, body])
            if status == 429:
                raise SpotifyRateLimited("Spotify временно ограничил запросы.")
            if status == 401:
                # The anonymous token lives about an hour; the page gets a new one.
                self.headers = {}
                continue
            if status == 200:
                try:
                    data = json.loads(text)
                except ValueError:
                    data = {}
                if isinstance(data, dict) and data.get("data") is not None:
                    return data
            log.warning("spotify_query_failed", extra={"operation": operation, "status": status})
            if learned:
                break
            # An old hash (a new player version): learn the current one from the page.
            self.hashes.pop(operation, None)
        raise SpotifyError("Spotify не отдал данные. Возможно, изменился веб-плеер — сообщите нам.")

    # ---------- the requests the search needs ----------

    def artist(self, artist_id: str) -> dict:
        return self.query(
            "queryArtistOverview",
            {"uri": f"spotify:artist:{artist_id}", "locale": "", "preReleaseV2": False},
        )

    def playlist(self, playlist_id: str, offset: int = 0, limit: int = 100) -> dict:
        return self.query(
            "fetchPlaylist",
            {
                "uri": f"spotify:playlist:{playlist_id}",
                "offset": offset,
                "limit": limit,
                "enableWatchFeedEntrypoint": False,
                "includeEpisodeContentRatingsV2": True,
            },
        )

    def search(self, kind: str, term: str, offset: int = 0, limit: int = 30) -> dict:
        return self.query(
            "searchArtists" if kind == "artists" else "searchPlaylists",
            {
                "searchTerm": term,
                "offset": offset,
                "limit": limit,
                "numberOfTopResults": 20,
                "includeAudiobooks": False,
                "includePreReleases": False,
                "includeAlbumPreReleases": False,
                "includeAuthors": False,
                "includeEpisodeContentRatingsV2": True,
            },
        )
