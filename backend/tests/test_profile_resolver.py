"""Profile resolver units: counts, aliases, contacts, merge, fallback chain, retries, cache.

Nothing here reaches Instagram: API answers come from JSON fixtures and page snapshots
are plain dicts shaped like the page scripts' output.
"""

import json
from datetime import timedelta
from pathlib import Path

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
    InstagramApiProfileProvider,
    InstagramProfileResolver,
    PartialProfile,
    ProfileResolveContext,
    ProfileResolveError,
    ResolveCancelled,
    ResolveStep,
    SqlProfileCache,
    is_profile_data_sufficient,
    merge_profile_data,
    normalize_external_url,
    parse_instagram_count,
    parse_instagram_profile_api_response,
    profile_from_fields,
    resolve_instagram_user_id,
)
from artist_lead_finder.lead_scout.profiles.model import ProviderUnavailable
from artist_lead_finder.lead_scout.profiles.normalize import (
    dedupe_links,
    normalize_instagram_username,
)
from artist_lead_finder.models import ScoutProfileCache, utcnow

API_FIXTURES = Path(__file__).parent / "fixtures" / "instagram" / "api"


def api_body(name: str) -> dict:
    return json.loads((API_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def api_snapshot(body=None, status=200, **extra) -> dict:
    return {"ready": True, "api": {"status": status, "body": body, "redirect": None, **extra}}


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


# ---------- Aliases and the API parser ----------


def test_api_parser_normal_profile_fixture():
    profile = parse_instagram_profile_api_response(api_snapshot(api_body("profile-normal"))["api"])
    assert (profile.username, profile.id, profile.full_name) == ("artist123", "4242", "Artist One")
    assert (profile.followers_count, profile.following_count, profile.posts_count) == (
        2431,
        310,
        57,
    )
    assert profile.external_url == "https://linktr.ee/artist123"
    # The linktree duplicate and the lynx wrapper are folded into one link.
    assert profile.bio_links == [
        "https://open.spotify.com/artist/abc123",
        "https://artist123.bandcamp.com/",
    ]
    assert profile.category_name == "" and profile.is_business is False
    assert profile.recent_captions == [
        "New single out now!",
        "Studio night with @producer_two",
        "Tour dates soon",
    ]


def test_field_aliases_of_business_fixture():
    profile = parse_instagram_profile_api_response(
        api_snapshot(api_body("profile-business"))["api"]
    )
    assert (profile.id, profile.full_name) == ("90001", "Beat Maker")
    assert (profile.followers_count, profile.following_count, profile.posts_count) == (
        12400,
        88,
        301,
    )
    assert profile.category_name == "Music Producer" and profile.is_business is True
    assert profile.external_url == "https://www.beatstars.com/beatmaker"
    assert profile.bio_links == ["https://soundcloud.com/beatmaker"]
    assert profile.emails == ["Beats@Maker.pro", "beats@maker.pro"]
    assert profile.phones == ["4045550199", "+1 404-555-0199"]
    assert profile.phone_country_code == "1"


def test_aliases_are_resolved_in_one_place():
    for followers in ({"followers_count": 5}, {"follower_count": 5}, {"followers": "5"}):
        assert profile_from_fields({"username": "a_b", **followers}, "api").followers_count == 5
    assert profile_from_fields({"username": "a_b", "posts_count": 3}, "api").posts_count == 3
    assert profile_from_fields({"username": "a_b", "fullName": "X"}, "api").full_name == "X"
    assert profile_from_fields({"username": "a_b", "is_business": 1}, "api").is_business is True
    missing = profile_from_fields({"username": "a_b"}, "api")
    # Absent keys stay unknown (None); a present null means "the source said: none".
    assert missing.biography is None and missing.external_url is None
    stated = profile_from_fields(
        {"username": "a_b", "biography": None, "external_url": None}, "api"
    )
    assert stated.biography == "" and stated.external_url == ""


def test_private_and_partial_fixtures():
    private = parse_instagram_profile_api_response(api_snapshot(api_body("profile-private"))["api"])
    assert private.is_private is True and private.followers_count == 800
    assert private.biography == "singer · private page"
    partial = parse_instagram_profile_api_response(api_snapshot(api_body("profile-partial"))["api"])
    assert partial.followers_count is None and partial.biography is None
    assert not is_profile_data_sufficient(partial)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"status": 429, "body": None}, "RATE_LIMITED"),
        (
            {"status": 401, "body": {"message": "Please wait a few minutes before you try again."}},
            "RATE_LIMITED",
        ),
        ({"status": 400, "body": {"message": "checkpoint_required"}}, "CHECKPOINT"),
        ({"status": 200, "redirect": "challenge", "body": None}, "CHECKPOINT"),
        ({"status": 200, "redirect": "login", "body": None}, "LOGIN_REQUIRED"),
        ({"status": 401, "body": {"message": "login_required"}}, "LOGIN_REQUIRED"),
        ({"status": 404, "body": None}, "NOT_FOUND"),
        ({"status": 200, "body": {"data": {"user": None}, "status": "ok"}}, "NOT_FOUND"),
        ({"status": 502, "body": None}, "NETWORK_ERROR"),
        ({"error": "timeout"}, "NETWORK_ERROR"),
        ({"status": 200, "body": None}, "PARSER_ERROR"),
        ({"status": 200, "body": {"status": "ok"}}, "PARSER_ERROR"),
    ],
)
def test_api_errors_are_typed(response, reason):
    with pytest.raises(ProfileResolveError) as caught:
        parse_instagram_profile_api_response({"redirect": None, **response})
    assert caught.value.reason == reason
    assert caught.value.retryable == (reason == "NETWORK_ERROR")


def test_api_that_cannot_be_asked_hands_over_to_the_browser():
    for response in ({"error": "no_instagram_tab"}, None, {"status": 400, "body": {}}):
        with pytest.raises(ProviderUnavailable):
            parse_instagram_profile_api_response(response)


def test_api_provider_rejects_an_answer_for_another_account():
    provider = InstagramApiProfileProvider()
    with pytest.raises(ProfileResolveError) as caught:
        provider.get_profile("someone_else", api_snapshot(api_body("profile-normal")))
    assert caught.value.reason == "PARSER_ERROR"
    request = provider.request("artist123", "https://www.instagram.com/artist123/")
    assert request["path"] == "/api/v1/users/web_profile_info/?username=artist123"
    assert request["endpoint"] == "web_profile_info"


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


# ---------- Sufficiency and merge ----------


def test_is_profile_data_sufficient():
    full = PartialProfile(
        "api", "a_b", followers_count=10, biography="x", external_url="", category_name=""
    )
    assert is_profile_data_sufficient(full)
    one_gap = PartialProfile("api", "a_b", followers_count=10, biography="x", external_url="")
    assert is_profile_data_sufficient(one_gap)
    two_gaps = PartialProfile("api", "a_b", followers_count=10, biography="x")
    assert not is_profile_data_sufficient(two_gaps)
    no_followers = PartialProfile("api", "a_b", biography="x", external_url="", category_name="")
    assert not is_profile_data_sufficient(no_followers)
    assert not is_profile_data_sufficient(None)


def test_merge_keeps_api_numbers_and_fills_gaps_from_the_browser():
    api = PartialProfile(
        "api",
        "artist123",
        id="4242",
        followers_count=12400,
        biography=None,
        bio_links=["https://open.spotify.com/a"],
        emails=["a@b.co"],
    )
    browser = PartialProfile(
        "browser",
        "artist123",
        id="9999",
        followers_count=12000,
        biography="rapper | new single out now",
        category_name="Musician/band",
        bio_links=["https://open.spotify.com/a", "https://soundcloud.com/a"],
        emails=["A@b.co"],
        strategies={"biography": "header_heading"},
    )
    merged = merge_profile_data(api, browser)
    assert merged.source == "merged"
    assert merged.profile.followers_count == 12400
    assert merged.profile.biography == "rapper | new single out now"
    assert merged.profile.category_name == "Musician/band"
    assert merged.profile.bio_links == ["https://open.spotify.com/a", "https://soundcloud.com/a"]
    assert merged.profile.emails == ["a@b.co"]
    assert merged.profile.id == "4242"
    assert merged.anomalies == ["id differs: api=4242 browser=9999"]
    # A good value is never replaced by an empty one.
    empty = PartialProfile("browser", "artist123", biography="")
    assert (
        merge_profile_data(
            PartialProfile("api", "artist123", biography="bio"), empty
        ).profile.biography
        == "bio"
    )


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
    """Scripted page answers per phase; records what the resolver asked for."""

    def __init__(self, api=None, browser=None):
        self.answers = {"api": list(api or []), "browser": list(browser or [])}
        self.calls = []

    def __call__(self, step, args):
        self.calls.append((step.phase, args))
        answer = self.answers[step.phase].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def context(fetch, **extra):
    return ProfileResolveContext(fetch=fetch, **extra)


def test_api_success_skips_the_browser():
    fetch = Fetcher(api=[api_snapshot(api_body("profile-normal"))])
    result = InstagramProfileResolver().resolve("@Artist123", context(fetch))
    assert result.ok and result.profile.source == "api"
    assert [phase for phase, _ in fetch.calls] == ["api"]
    assert fetch.calls[0][1]["path"].endswith("username=artist123")
    profile = result.profile
    assert profile.emails == ["mgmt@artist123.com"] and profile.phones == []
    log = "\n".join(result.log)
    assert "[ProfileResolver][@artist123]" in log and "not required" in log
    assert "followers: 2431" in log and "emails: 1" in log and "Resolved in" in log


def test_api_partial_falls_back_to_the_browser_and_merges():
    fetch = Fetcher(api=[api_snapshot(api_body("profile-partial"))], browser=[browser_snapshot()])
    result = InstagramProfileResolver().resolve("artist123", context(fetch))
    assert result.ok and result.profile.source == "merged"
    assert [phase for phase, _ in fetch.calls] == ["api", "browser"]
    profile = result.profile
    assert profile.followers_count == 12400 and profile.following_count == 310
    assert profile.biography == "rapper | new single out now"
    assert profile.category_name == "Musician/band"
    assert profile.emails == ["booking@artist123.com"]
    assert "partial: missing bio, followers, external link, category" in result.log
    assert "Merged profile created." in result.log


def test_unavailable_api_falls_back_and_guest_skips_it():
    fetch = Fetcher(api=[api_snapshot(error="no_instagram_tab")], browser=[browser_snapshot()])
    result = InstagramProfileResolver().resolve("artist123", context(fetch))
    assert result.ok and result.profile.source == "browser"
    fetch = Fetcher(browser=[browser_snapshot()])
    result = InstagramProfileResolver().resolve("artist123", context(fetch, use_api=False))
    assert result.ok and [phase for phase, _ in fetch.calls] == ["browser"]


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (api_snapshot(status=404), "NOT_FOUND"),
        (api_snapshot(redirect="challenge"), "CHECKPOINT"),
        (api_snapshot(redirect="login"), "LOGIN_REQUIRED"),
    ],
)
def test_final_api_errors_stop_without_retry_or_browser(snapshot, reason):
    fetch = Fetcher(api=[snapshot])
    result = InstagramProfileResolver().resolve("artist123", context(fetch, max_retries=3))
    assert not result.ok and result.reason == reason and not result.retryable
    assert result.stops_run == (reason != "NOT_FOUND")
    assert len(fetch.calls) == 1


def test_api_rate_limit_falls_back_to_the_page_without_retry():
    fetch = Fetcher(api=[api_snapshot(status=429)], browser=[browser_snapshot()])
    result = InstagramProfileResolver().resolve("artist123", context(fetch, max_retries=3))
    assert result.ok and result.profile.source == "browser"
    assert [phase for phase, _ in fetch.calls] == ["api", "browser"]
    assert "rate limited (HTTP 429); API paused" in result.log


def test_transient_errors_are_retried_within_the_limit():
    fetch = Fetcher(
        api=[
            TimeoutError(),
            api_snapshot(error="network"),
            api_snapshot(api_body("profile-normal")),
        ]
    )
    result = InstagramProfileResolver().resolve("artist123", context(fetch, max_retries=2))
    assert result.ok and [phase for phase, _ in fetch.calls] == ["api", "api", "api"]
    # Browser timeouts beyond the limit give NETWORK_ERROR (retryable for the scheduler).
    fetch = Fetcher(browser=[TimeoutError(), TimeoutError()])
    result = InstagramProfileResolver().resolve(
        "artist123", context(fetch, use_api=False, max_retries=1)
    )
    assert not result.ok and result.reason == "NETWORK_ERROR" and result.retryable


def test_private_profile_keeps_public_header_data():
    fetch = Fetcher(api=[api_snapshot(api_body("profile-private"))])
    result = InstagramProfileResolver().resolve("secret.singer", context(fetch))
    assert result.ok and result.profile.is_private is True
    assert result.profile.followers_count == 800 and result.profile.biography


def test_browser_not_found_and_parser_errors_are_typed():
    fetch = Fetcher(
        browser=[{"url": "https://www.instagram.com/gone/", "ready": True, "unavailable": True}]
    )
    result = InstagramProfileResolver().resolve("gone", context(fetch, use_api=False))
    assert result.reason == "NOT_FOUND"
    broken = {**browser_snapshot(), "title": "Instagram", "header": ""}
    fetch = Fetcher(browser=[broken])
    result = InstagramProfileResolver().resolve("artist123", context(fetch, use_api=False))
    assert result.reason == "PARSER_ERROR"


def test_cancellation_is_checked_between_steps_and_saves_nothing(tmp_path):
    engine, sessions = open_database(tmp_path / "cancel.db")
    cache = SqlProfileCache(sessions)
    state = {"stopped": False}

    def fetch(step, args):
        state["stopped"] = True  # The user presses Stop while the request is running.
        return api_snapshot(api_body("profile-partial"))

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
    fetch = Fetcher(api=[api_snapshot(api_body("profile-normal"))])
    resolver = InstagramProfileResolver()
    first = resolver.resolve("artist123", context(fetch, cache=cache, cache_ttl_hours=12))
    again = resolver.resolve("artist123", context(fetch, cache=cache, cache_ttl_hours=12))
    assert first.ok and again.ok and len(fetch.calls) == 1
    assert again.profile.as_dict() == first.profile.as_dict()
    assert any(line.startswith("hit (api") for line in again.log)
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
    step = ResolveStep("browser", "artist123", utcnow().isoformat(), api={"source": "api"})
    assert ResolveStep.from_dict(json.loads(json.dumps(step.as_dict()))) == step


def test_logs_never_carry_session_secrets():
    snapshot = api_snapshot(api_body("profile-normal"))
    snapshot["api"]["request_headers"] = {"cookie": "sessionid=SECRET", "x-csrftoken": "TOKEN"}
    result = InstagramProfileResolver().resolve("artist123", context(Fetcher(api=[snapshot])))
    text = "\n".join(result.log) + json.dumps(result.profile.as_dict())
    assert "SECRET" not in text and "TOKEN" not in text and "cookie" not in text.lower()
