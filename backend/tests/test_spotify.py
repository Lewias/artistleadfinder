"""«Spotify»: the search over a stand-in web player that answers in the player's own shapes,
with the real outreach list and CRM of the app."""

import threading
from datetime import date, datetime, timezone

import pytest

from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import Lead, SpotifyRun
from artist_lead_finder.service import ApplicationService
from artist_lead_finder.spotify import parse
from artist_lead_finder.spotify.client import SpotifyRateLimited, page_for
from artist_lead_finder.spotify.service import SpotifyService


def sid(name: str) -> str:
    """A Spotify-like 22-character id made from a readable name."""
    return (name + "0" * 22)[:22]


# ---------- the player's answers ----------


def artist_payload(
    name: str,
    listeners: int,
    instagram: str | None = None,
    related: tuple[str, ...] = (),
    single: tuple[int, int, int] | None = (2026, 9, 1),
    links: list | None = None,
) -> dict:
    if links is None:
        links = [{"name": "TWITTER", "url": f"https://twitter.com/{name}"}]
        if instagram:
            links.append({"name": "INSTAGRAM", "url": f"https://instagram.com/{instagram}"})
    singles = []
    if single:
        year, month, day = single
        singles = [
            {
                "releases": {
                    "items": [
                        {
                            "name": f"{name} single",
                            "date": {"year": year, "month": month, "day": day, "precision": "DAY"},
                        }
                    ]
                }
            }
        ]
    return {
        "data": {
            "artistUnion": {
                "__typename": "Artist",
                "uri": f"spotify:artist:{sid(name)}",
                "profile": {
                    "name": name,
                    "biography": {"text": f"{name} &#34;makes&#34; music"},
                    "externalLinks": {"items": links},
                },
                "stats": {
                    "monthlyListeners": listeners,
                    "followers": listeners // 2,
                    "topCities": {"items": [{"city": "Chicago", "country": "US"}]},
                },
                "discography": {
                    "latest": None,
                    "singles": {"items": singles},
                    "albums": {"items": []},
                },
                "relatedContent": {
                    "relatedArtists": {
                        "items": [{"uri": f"spotify:artist:{sid(other)}"} for other in related]
                    }
                },
            }
        }
    }


def playlist_payload(name: str, artists: list[str], total: int | None = None) -> dict:
    return {
        "data": {
            "playlistV2": {
                "name": name,
                "content": {
                    "totalCount": total if total is not None else len(artists),
                    "items": [
                        {
                            "itemV2": {
                                "data": {
                                    "artists": {"items": [{"uri": f"spotify:artist:{sid(artist)}"}]}
                                }
                            }
                        }
                        for artist in artists
                    ],
                },
            }
        }
    }


def playlists_search(names: list[str]) -> dict:
    return {
        "data": {
            "searchV2": {
                "playlists": {
                    "items": [
                        {
                            "data": {
                                "uri": f"spotify:playlist:{sid(name)}",
                                "name": name,
                                "ownerV2": {"data": {"name": "curator"}},
                            }
                        }
                        for name in names
                    ]
                }
            }
        }
    }


def artists_search(names: list[str]) -> dict:
    return {
        "data": {
            "searchV2": {
                "artists": {"items": [{"data": {"uri": f"spotify:artist:{sid(n)}"}} for n in names]}
            }
        }
    }


class FakeWeb:
    """The player: artists by name, playlists by name, search answers by term."""

    def __init__(self, artists=(), playlists=None, found_playlists=None, found_artists=None):
        self.artists = {
            sid(payload["data"]["artistUnion"]["profile"]["name"]): payload for payload in artists
        }
        self.playlists = {sid(name): (name, items) for name, items in (playlists or {}).items()}
        self.found_playlists = found_playlists or {}
        self.found_artists = found_artists or {}
        self.asked: list[str] = []
        self.fail_after: int | None = None
        self.closed = False
        self.on_artist = None

    def start(self):
        pass

    def close(self):
        self.closed = True

    def artist(self, artist_id):
        self.asked.append(artist_id)
        if self.fail_after is not None and len(self.asked) > self.fail_after:
            raise SpotifyRateLimited("limit")
        if self.on_artist:
            self.on_artist(artist_id)
        return self.artists.get(artist_id, {"data": {"artistUnion": None}})

    def playlist(self, playlist_id, offset=0, limit=100):
        name, items = self.playlists[playlist_id]
        return playlist_payload(name, items[offset : offset + limit], total=len(items))

    def search(self, kind, term, offset=0, limit=30):
        if kind == "artists":
            return artists_search(self.found_artists.get(term, []) if offset == 0 else [])
        names = self.found_playlists.get(term, [])[offset : offset + limit]
        return playlists_search(names)


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "spotify.db")
    service = ApplicationService(sessions, tmp_path / "data")
    yield service, sessions
    service.shutdown()
    engine.dispose()


def spotify(app, web: FakeWeb, **settings) -> SpotifyService:
    service, sessions = app
    service.spotify = SpotifyService(
        sessions,
        service.workspace.add_usernames,
        service.crm.merge,
        web_factory=lambda: web,
        pause=lambda stop: None,
    )
    defaults = {"queries": ["new rap"], "use_search": False}
    service.call("spotify.settings", {**defaults, **settings})
    return service.spotify


def run(app, target=10) -> dict:
    service, _ = app
    service.call("spotify.start", {"target": target})
    service.spotify.thread.join(10)
    assert not service.spotify.running
    return service.call("spotify.state", {})


# ---------- reading the player's answers ----------


def test_the_artist_answer_gives_listeners_instagram_and_the_newest_release():
    payload = artist_payload("Mike", 6328, instagram="MikeXDece", related=("Papo",))
    payload["data"]["artistUnion"]["discography"]["latest"] = {
        "name": "Newest",
        "date": {"isoString": "2026-10-02T00:00:00Z"},
    }
    facts = parse.artist_facts(payload)
    assert facts.id == sid("Mike") and facts.name == "Mike"
    assert facts.listeners == 6328 and facts.followers == 3164
    assert facts.instagram == "mikexdece"
    assert facts.bio == 'Mike "makes" music'
    assert (facts.release_name, facts.release_date) == ("Newest", date(2026, 10, 2))
    assert (facts.city, facts.country) == ("Chicago", "US")
    assert facts.related == [sid("Papo")]
    assert parse.artist_facts({"data": {"artistUnion": None}}) is None
    assert parse.artist_facts({"errors": [{"message": "x"}]}) is None


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://instagram.com/gunna", "gunna"),
        ("https://www.instagram.com/Some.Artist/?hl=en", "some.artist"),
        ("instagram.com/_u/artist_1", "artist_1"),
        ("https://instagram.com/p/ABC123", None),
        ("https://instagram.com/explore", None),
        ("https://notinstagram.com/gunna", None),
        ("https://instagram.com/", None),
    ],
)
def test_only_a_profile_link_counts_as_the_instagram(url, expected):
    assert parse.instagram_username([{"name": "INSTAGRAM", "url": url}]) == expected
    assert parse.instagram_username([{"name": "TWITTER", "url": "https://instagram.com/x"}]) is None


def test_links_the_user_pastes():
    artist = sid("Artist")
    assert parse.parse_link(f"https://open.spotify.com/artist/{artist}?si=abc") == (
        "artist",
        artist,
    )
    assert parse.parse_link(f"open.spotify.com/intl-de/playlist/{artist}") == ("playlist", artist)
    assert parse.parse_link(f"spotify:playlist:{artist}") == ("playlist", artist)
    assert parse.parse_link("https://open.spotify.com/album/" + artist) is None
    assert parse.parse_link("https://evil.com/artist/" + artist) is None
    assert parse.parse_link("hello") is None


def test_the_player_page_that_teaches_each_request():
    assert page_for("queryArtistOverview", {"uri": "spotify:artist:abc"}).endswith("/artist/abc")
    assert page_for("fetchPlaylist", {"uri": "spotify:playlist:xyz"}).endswith("/playlist/xyz")
    assert page_for("searchPlaylists", {"searchTerm": "new rap"}).endswith(
        "/search/new%20rap/playlists"
    )
    assert page_for("searchArtists", {"searchTerm": "drill"}).endswith("/search/drill/artists")


# ---------- the search ----------


def test_small_artists_with_instagram_are_found_and_their_fans_also_like_comes_first(app):
    service, sessions = app
    service.crm.save(
        {
            "crm": "instagram",
            "name": "In CRM",
            "channels": [{"kind": "instagram", "value": "incrm"}],
        }
    )
    web = FakeWeb(
        artists=[
            artist_payload("Star", 2_000_000, instagram="star", related=("Huge",)),
            artist_payload("Small", 900, instagram="small_one", related=("Near", "Star")),
            artist_payload("Silent", 500),
            artist_payload("Near", 4000, instagram="near.artist"),
            artist_payload("Crm", 300, instagram="incrm"),
            artist_payload("Huge", 5_000_000, instagram="huge"),
        ],
        playlists={"Fresh": ["Star", "Small", "Silent", "Crm"]},
        found_playlists={"new rap": ["Fresh"]},
    )
    spotify(app, web)
    state = run(app)
    assert state["run"]["status"] == "done"
    assert "Источники закончились" in state["run"]["message"]
    # «Near» (fans of «Small» also like) is asked right after «Small», before the playlist goes on;
    # the related artists of a star are not followed.
    assert web.asked == [sid(name) for name in ("Star", "Small", "Near", "Silent", "Crm")]
    found = service.call("spotify.results", {"view": "found"})["rows"]
    assert {row["instagram"] for row in found} == {"small_one", "near.artist"}
    near = next(row for row in found if row["instagram"] == "near.artist")
    assert near["source"] == "похож на Small" and near["listeners"] == 4000
    assert near["url"] == f"https://open.spotify.com/artist/{sid('Near')}"
    skipped = {
        row["name"]: row["status"]
        for row in service.call("spotify.results", {"view": "skipped"})["rows"]
    }
    assert skipped == {"Star": "out_of_range", "Silent": "no_instagram", "Crm": "known"}
    assert state["totals"] == {"found": 2, "new": 2, "checked": 5}
    assert web.closed


def test_the_run_stops_at_the_target_and_the_next_run_goes_further(app):
    service, _ = app
    names = [f"Artist{i}" for i in range(6)]
    web = FakeWeb(
        artists=[
            artist_payload(name, 100 + i, instagram=f"art{i}") for i, name in enumerate(names)
        ],
        playlists={"List": names},
        found_playlists={"new rap": ["List"]},
    )
    spotify(app, web)
    state = run(app, target=2)
    assert state["run"]["found"] == 2 and state["run"]["status"] == "done"
    assert len(web.asked) == 2
    # The playlist was not finished, so the next run takes it again and skips who it asked.
    state = run(app, target=2)
    assert web.asked == [sid(name) for name in names[:4]]
    assert state["totals"]["found"] == 4


def test_the_next_run_goes_on_with_fans_also_like_where_the_last_one_stopped(app):
    service, _ = app
    web = FakeWeb(
        artists=[
            artist_payload("Small", 900, instagram="small", related=("Near1", "Near2")),
            artist_payload("Near1", 800, instagram="near1"),
            artist_payload("Near2", 700, instagram="near2"),
        ],
        playlists={"P": ["Small"]},
        found_playlists={"new rap": ["P"]},
    )
    spotify(app, web)
    run(app, target=1)
    assert web.asked == [sid("Small")]
    run(app, target=5)
    assert web.asked == [sid(name) for name in ("Small", "Near1", "Near2")]
    found = {
        row["name"]: row["source"]
        for row in service.call("spotify.results", {"view": "found"})["rows"]
    }
    assert found["Near1"] == "похож на Small"


def test_a_finished_playlist_waits_and_a_pasted_link_is_taken_every_run(app):
    service, _ = app
    playlist = sid("Mine")
    web = FakeWeb(
        artists=[artist_payload("Solo", 50, instagram="solo"), artist_payload("Duo", 60)],
        playlists={"Mine": ["Solo", "Duo"]},
        found_playlists={"new rap": ["Mine"]},
    )
    spotify(app, web)
    run(app)
    assert len(web.asked) == 2
    asked_playlists = []
    original = web.playlist
    web.playlist = lambda *args, **kwargs: (
        asked_playlists.append(args[0]) or original(*args, **kwargs)
    )
    run(app)
    assert asked_playlists == []
    service.call("spotify.settings", {"seeds": [f"https://open.spotify.com/playlist/{playlist}"]})
    run(app)
    assert asked_playlists == [playlist]


def test_release_freshness_and_search_by_artist_name(app):
    service, _ = app
    web = FakeWeb(
        artists=[
            artist_payload("Fresh", 100, instagram="fresh", single=(2026, 10, 1)),
            artist_payload("Old", 100, instagram="old", single=(2023, 1, 1)),
            artist_payload("Never", 100, instagram="never", single=None),
        ],
        found_artists={"drill": ["Fresh", "Old", "Never"]},
    )
    spotify(app, web, queries=["drill"], use_playlists=False, use_search=True, release_days=90)
    service.spotify.now = lambda: datetime(2026, 10, 10, tzinfo=timezone.utc)
    run(app)
    rows = {
        row["name"]: row["status"]
        for row in service.call("spotify.results", {"view": "skipped"})["rows"]
    }
    assert rows == {"Old": "old_release", "Never": "old_release"}
    found = service.call("spotify.results", {"view": "found"})["rows"]
    assert [(row["name"], row["source"]) for row in found] == [("Fresh", "поиск «drill»")]


def test_contacted_leads_are_not_found_again(app):
    service, sessions = app
    with sessions.begin() as session:
        session.add(
            Lead(
                platform="instagram",
                username="written",
                last_contacted_at=datetime.now(timezone.utc),
            )
        )
    web = FakeWeb(
        artists=[artist_payload("Written", 100, instagram="written")],
        playlists={"P": ["Written"]},
        found_playlists={"new rap": ["P"]},
    )
    spotify(app, web)
    run(app)
    assert service.call("spotify.results", {"view": "skipped"})["rows"][0]["status"] == "known"


def test_stop_and_spotify_limits_end_the_run_with_a_message(app):
    service, sessions = app
    names = [f"Busy{i}" for i in range(5)]
    web = FakeWeb(
        artists=[artist_payload(name, 100, instagram=f"busy{i}") for i, name in enumerate(names)],
        playlists={"P": names},
        found_playlists={"new rap": ["P"]},
    )
    spotify(app, web)
    web.on_artist = lambda artist_id: service.call("spotify.stop", {})
    state = run(app)
    assert state["run"]["status"] == "stopped" and len(web.asked) == 1
    web.on_artist = None
    web.fail_after = 2
    state = run(app)
    assert state["run"]["status"] == "failed"
    assert "ограничил запросы" in state["run"]["message"]
    assert state["running"] is False


def test_found_artists_go_to_the_outreach_list_and_the_crm(app):
    service, sessions = app
    web = FakeWeb(
        artists=[
            artist_payload("One", 1234, instagram="one_ig"),
            artist_payload("Two", 50, instagram="two_ig"),
        ],
        playlists={"P": ["One", "Two"]},
        found_playlists={"new rap": ["P"]},
    )
    spotify(app, web)
    run(app)
    result = service.call("spotify.to_outreach", {"ids": [sid("One")]})
    assert result == {"added": 1, "count": 1}
    workspace = service.call("outreach.workspace", {})
    assert [row["username"] for row in workspace["usernames"]] == ["one_ig"]
    assert [row["name"] for row in service.call("spotify.results", {"view": "new"})["rows"]] == [
        "Two"
    ]
    assert service.call("spotify.to_outreach", {"all_new": True}) == {"added": 1, "count": 1}
    with pytest.raises(UserError):
        service.call("spotify.to_outreach", {"all_new": True})

    assert service.call("spotify.to_crm", {"ids": [sid("One")]})["added"] == 1
    contact = service.call("crm.list", {"crm": "instagram"})["items"][0]
    assert contact["name"] == "One"
    assert contact["channels"] == [{"kind": "instagram", "value": "one_ig"}]
    assert "1 234 слушателей" in contact["notes"] and sid("One") in contact["notes"]
    found = {row["name"]: row for row in service.call("spotify.results", {"view": "found"})["rows"]}
    assert found["One"]["crm_at"] and found["Two"]["crm_at"] is None


def test_settings_are_checked(app):
    service, _ = app
    spotify(app, FakeWeb())
    state = service.call("spotify.state", {})
    assert state["settings"]["min_listeners"] == 0 and state["settings"]["max_listeners"] == 10000
    for bad in (
        {"min_listeners": 500, "max_listeners": 100},
        {"max_listeners": -1},
        {"seeds": ["https://example.com/x"]},
        {"queries": "rap"},
        {"target": 0},
    ):
        with pytest.raises(UserError):
            service.call("spotify.settings", bad)
    service.call("spotify.settings", {"queries": [" rap ", "rap", ""], "use_playlists": False})
    assert service.call("spotify.state", {})["settings"]["queries"] == ["rap"]
    service.call("spotify.settings", {"queries": [], "seeds": []})
    with pytest.raises(UserError, match="фразы поиска"):
        service.call("spotify.start", {})


def test_a_run_cut_off_by_closing_the_app_is_marked_interrupted(app):
    service, sessions = app
    with sessions.begin() as session:
        session.add(SpotifyRun(status="running"))
    spotify(app, FakeWeb())
    run_row = service.call("spotify.state", {})["run"]
    assert run_row["status"] == "interrupted" and "прервался" in run_row["message"]


def test_one_run_at_a_time(app):
    service, _ = app
    release = threading.Event()
    web = FakeWeb(
        artists=[artist_payload("Slow", 10, instagram="slow")],
        playlists={"P": ["Slow"]},
        found_playlists={"new rap": ["P"]},
    )
    web.on_artist = lambda artist_id: release.wait(5)
    spotify(app, web)
    service.call("spotify.start", {})
    try:
        with pytest.raises(UserError, match="уже идёт"):
            service.call("spotify.start", {})
    finally:
        release.set()
        service.spotify.thread.join(10)
