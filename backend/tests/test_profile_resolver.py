"""Profile resolver units: counts, aliases, contacts, page reading, retries, cache.

Nothing here reaches Instagram: page snapshots are plain dicts shaped like the page
scripts' output.
"""

import json
from datetime import timedelta

import pytest

from artist_lead_finder.database import open_database
from artist_lead_finder.lead_scout.contacts import (
    ContactExtractor,
    ContactSource,
    build_contact_text,
    extract_emails,
    extract_phones,
    normalize_phone,
)
from artist_lead_finder.lead_scout.profiles import (
    AbortSignal,
    InstagramProfileResolver,
    PartialProfile,
    ProfileResolveContext,
    ResolveCancelled,
    ResolveStep,
    SqlProfileCache,
    normalize_external_url,
    parse_instagram_count,
    profile_from_fields,
    resolve_instagram_user_id,
)
from artist_lead_finder.lead_scout.profiles.normalize import (
    dedupe_links,
    normalize_instagram_username,
)
from artist_lead_finder.models import ScoutProfileCache, utcnow


def browser_snapshot(name="artist123", bio="rapper | new single out now", followers="2,431"):
    return {
        "url": f"https://www.instagram.com/{name}/",
        "ready": True,
        "title": f"Artist One (@{name}) • Instagram photos and videos",
        "description": f"{followers} Followers, 310 Following, 57 Posts",
        "header": f"{name}\nArtist One\nMusician/band\n{followers} followers 310 following\n{bio}",
        "links": ["https://linktr.ee/artist123", "https://open.spotify.com/artist/abc123"],
        "external_url": "https://linktr.ee/artist123",
        "captions": ["New single out now!"],
        "user_id": "4242",
        "profile": {
            "full_name": {"value": "Artist One", "strategy": "meta_title"},
            "user_id": {"value": "4242", "strategy": "meta_owner_id"},
            "stats": {
                "followers": {"raw": "12.4K", "strategy": "header_link"},
                "posts": {"raw": "57", "strategy": "meta_description"},
            },
            "bio": {"value": bio, "strategy": "header_heading"},
            "category": {"candidates": ["Follow", "Musician/band"]},
            "contacts": {"emails": ["Booking@Artist123.com"], "phones": [], "text": "Email"},
        },
    }


# ---------- Counts, usernames, links ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234", 1234),
        ("1.2K", 1200),
        ("12K", 12000),
        ("1.5M", 1_500_000),
        ("2,1 тыс.", 2100),
        ("12,4 млн", 12_400_000),
        ("1 234", 1234),
        ("12 345", 12345),
        ("1.234", 1234),
        ("2431 followers", 2431),
        ("12.4k followers", 12400),
        (2431, 2431),
        ({"count": 57}, 57),
        ("1,5", None),
        ("followers", None),
        ("", None),
        (None, None),
        (True, None),
        (-5, None),
    ],
)
def test_parse_instagram_count(raw, expected):
    assert parse_instagram_count(raw) == expected


def test_normalize_username_rejects_system_and_malformed_names():
    assert normalize_instagram_username("@Artist123") == "artist123"
    assert normalize_instagram_username("https://www.instagram.com/New.Rapper/") == "new.rapper"
    assert normalize_instagram_username("explore") is None
    assert normalize_instagram_username("bad..name") is None
    assert normalize_instagram_username("x" * 31) is None


def test_external_url_unwraps_instagram_redirects_without_opening_them():
    wrapped = "https://l.instagram.com/?u=https%3A%2F%2Flinktr.ee%2Fartist&e=AT0"
    assert normalize_external_url(wrapped) == "https://linktr.ee/artist"
    assert normalize_external_url("www.beatstars.com/x") == "https://www.beatstars.com/x"
    assert normalize_external_url("javascript:alert(1)") is None
    assert normalize_external_url("https://user:pw@evil.test/") is None
    links = dedupe_links(
        [
            "https://linktr.ee/artist/",
            "http://www.linktr.ee/artist",
            "https://open.spotify.com/artist/1",
            "https://www.instagram.com/other/",
            wrapped,
        ],
        exclude=["https://open.spotify.com/artist/1"],
    )
    assert links == ["https://linktr.ee/artist/"]


# ---------- Field aliases ----------


def test_aliases_are_resolved_in_one_place():
    for followers in ({"followers_count": 5}, {"follower_count": 5}, {"followers": "5"}):
        assert profile_from_fields({"username": "a_b", **followers}, "browser").followers_count == 5
    assert profile_from_fields({"username": "a_b", "posts_count": 3}, "browser").posts_count == 3
    assert profile_from_fields({"username": "a_b", "fullName": "X"}, "browser").full_name == "X"
    assert profile_from_fields({"username": "a_b", "is_business": 1}, "browser").is_business
    missing = profile_from_fields({"username": "a_b"}, "browser")
    # Absent keys stay unknown (None); a present null means "the source said: none".
    assert missing.biography is None and missing.external_url is None
    stated = profile_from_fields(
        {"username": "a_b", "biography": None, "external_url": None}, "browser"
    )
    assert stated.biography == "" and stated.external_url == ""


# ---------- Contacts ----------


def test_email_extraction_normalizes_and_rejects_false_matches():
    text = (
        "Booking: Mgmt@Example-Label.com, mgmt@example-label.com. "
        "art [at] studio [dot] io · logo@2x.png · a..b@mail.com · (press@label.co)"
    )
    assert extract_emails(text) == ["mgmt@example-label.com", "art@studio.io", "press@label.co"]


def test_phone_extraction_uses_country_context_only_when_known():
    assert normalize_phone("+1 (404) 555-0199") == "+14045550199"
    assert normalize_phone("404 555 0199", country_code="1") == "+14045550199"
    # No country: the cleaned original, never an invented code.
    assert normalize_phone("8 (912) 345-67-89") == "8 (912) 345-67-89"
    assert normalize_phone("12345") is None
    assert extract_phones("2500 followers 100 following 2024") == []
    assert extract_phones("tel: 8 912 345 67 89 · WhatsApp +7 912 345-67-89") == [
        "+79123456789",
        "8 912 345 67 89",
    ]


def test_contact_extractor_reads_only_the_profile_contact_surface():
    source = ContactSource(
        biography="Rapper. mgmt: team@artist.com",
        public_emails=["Team@Artist.com", "booking@artist.com"],
        public_phones=["4045550199", "+1 404-555-0199"],
        phone_country_code="1",
        contact_text="Call +44 20 7946 0958",
    )
    result = ContactExtractor().extract(source)
    assert result.emails == ["team@artist.com", "booking@artist.com"]
    assert result.phones == ["+14045550199", "+442079460958"]
    text = build_contact_text(source)
    assert "Rapper" in text and "Call +44" in text


# ---------- User id ----------


def test_user_id_fallback_order_never_guesses():
    api = PartialProfile("api", "a_b", id="11")
    browser = PartialProfile("browser", "a_b", id="33")
    assert resolve_instagram_user_id("a_b", api=api, browser=browser) == "11"
    assert (
        resolve_instagram_user_id("a_b", api=PartialProfile("api", "a_b"), lookups=[lambda u: "22"])
        == "22"
    )
    assert resolve_instagram_user_id("a_b", lookups=[lambda u: None], browser=browser) == "33"
    assert (
        resolve_instagram_user_id("a_b", browser=PartialProfile("browser", "other", id="5")) is None
    )
    assert resolve_instagram_user_id("a_b") is None


# ---------- Resolver chain ----------


class Fetcher:
    """Scripted page answers; records what the resolver asked for."""

    def __init__(self, browser=None):
        self.answers = list(browser or [])
        self.calls = []

    def __call__(self, step, args):
        self.calls.append((step.phase, args))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def context(fetch, **extra):
    return ProfileResolveContext(fetch=fetch, **extra)


def test_profile_is_read_from_its_page():
    fetch = Fetcher([browser_snapshot()])
    result = InstagramProfileResolver().resolve("@Artist123", context(fetch))
    assert result.ok and result.profile.source == "browser"
    assert fetch.calls == [("browser", None)]
    profile = result.profile
    assert profile.followers_count == 12400 and profile.following_count == 310
    assert profile.biography == "rapper | new single out now"
    assert profile.category_name == "Musician/band"
    assert profile.emails == ["booking@artist123.com"]
    log = "\n".join(result.log)
    assert "[ProfileResolver][@artist123]" in log and "API" not in log
    assert "emails: 1" in log and "Resolved in" in log


def test_transient_errors_are_retried_within_the_limit():
    fetch = Fetcher([TimeoutError(), browser_snapshot()])
    result = InstagramProfileResolver().resolve("artist123", context(fetch, max_retries=2))
    assert result.ok and len(fetch.calls) == 2
    # Timeouts beyond the limit give NETWORK_ERROR (retryable for the scheduler).
    fetch = Fetcher([TimeoutError(), TimeoutError()])
    result = InstagramProfileResolver().resolve("artist123", context(fetch, max_retries=1))
    assert not result.ok and result.reason == "NETWORK_ERROR" and result.retryable


def test_browser_not_found_and_parser_errors_are_typed():
    gone = {"url": "https://www.instagram.com/gone/", "ready": True, "unavailable": True}
    result = InstagramProfileResolver().resolve("gone", context(Fetcher([gone])))
    assert result.reason == "NOT_FOUND"
    broken = {**browser_snapshot(), "title": "Instagram", "header": ""}
    result = InstagramProfileResolver().resolve("artist123", context(Fetcher([broken])))
    assert result.reason == "PARSER_ERROR"


def test_cancellation_is_checked_between_steps_and_saves_nothing(tmp_path):
    engine, sessions = open_database(tmp_path / "cancel.db")
    cache = SqlProfileCache(sessions)
    state = {"stopped": False}

    def fetch(step, args):
        state["stopped"] = True  # The user presses Stop while the page is read.
        return browser_snapshot()

    ctx = ProfileResolveContext(
        fetch=fetch, cache=cache, signal=AbortSignal(lambda: state["stopped"])
    )
    with pytest.raises(ResolveCancelled):
        InstagramProfileResolver().resolve("artist123", ctx)
    assert cache.get("artist123", 12) is None
    engine.dispose()


def test_persistent_cache_serves_repeated_resolves_within_the_ttl(tmp_path):
    engine, sessions = open_database(tmp_path / "cache.db")
    cache = SqlProfileCache(sessions)
    fetch = Fetcher([browser_snapshot()])
    resolver = InstagramProfileResolver()
    first = resolver.resolve("artist123", context(fetch, cache=cache, cache_ttl_hours=12))
    again = resolver.resolve("artist123", context(fetch, cache=cache, cache_ttl_hours=12))
    assert first.ok and again.ok and len(fetch.calls) == 1
    assert again.profile.as_dict() == first.profile.as_dict()
    assert any(line.startswith("hit (browser") for line in again.log)
    # Outside the TTL the profile is resolved again.
    with sessions.begin() as session:
        row = session.get(ScoutProfileCache, "artist123")
        row.resolved_at = utcnow() - timedelta(hours=2)
    assert cache.get("artist123", 1) is None
    assert isinstance(
        resolver.begin("artist123", context(fetch, cache=cache, cache_ttl_hours=1)), ResolveStep
    )
    engine.dispose()


def test_resolve_step_survives_serialization():
    step = ResolveStep("browser", "artist123", utcnow().isoformat(), attempts=1, log=["x"])
    assert ResolveStep.from_dict(json.loads(json.dumps(step.as_dict()))) == step
    # A step saved before the web API was removed still loads.
    old = {**step.as_dict(), "phase": "api", "api": None, "api_limited": True}
    assert ResolveStep.from_dict(old).username == "artist123"


def test_logs_never_carry_session_secrets():
    snapshot = browser_snapshot()
    snapshot["request_headers"] = {"cookie": "sessionid=SECRET", "x-csrftoken": "TOKEN"}
    result = InstagramProfileResolver().resolve("artist123", context(Fetcher([snapshot])))
    text = "\n".join(result.log) + json.dumps(result.profile.as_dict())
    assert "SECRET" not in text and "TOKEN" not in text and "cookie" not in text.lower()


def test_page_bio_drops_header_buttons_and_keeps_typed_links():
    from artist_lead_finder.lead_scout.profiles.browser import BrowserProfileProvider

    snapshot = browser_snapshot(
        bio="Talk My Sh*t out now!\nlinktr.ee/boujielucy\nПодписаться\nОтправить сообщение"
        "\nHighlights\nraised where wolves at...\nещё"
    )
    snapshot["links"], snapshot["external_url"] = [], ""
    profile = BrowserProfileProvider().get_profile(
        "artist123", snapshot, "https://www.instagram.com/artist123/"
    )
    assert profile.biography == (
        "Talk My Sh*t out now!\nlinktr.ee/boujielucy\nraised where wolves at"
    )
    assert profile.external_url == "https://linktr.ee/boujielucy"
