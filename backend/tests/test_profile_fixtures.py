"""capture.js against sanitized fixtures in bundled Chromium.

Every instagram.com request is answered locally by a route; nothing leaves the machine.
"""

import json
import os
from pathlib import Path

import pytest

from artist_lead_finder.lead_scout.profiles import InstagramProfileResolver, ProfileResolveContext

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "instagram"
CAPTURE = (ROOT / "src-tauri" / "src" / "capture.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def browser():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        manager = sync_api.sync_playwright().start()
        instance = manager.chromium.launch(headless=True, channel="chromium")
    except Exception as error:  # pragma: no cover - depends on the local Chromium install
        pytest.skip(f"Bundled Chromium unavailable: {error}")
    yield instance
    instance.close()
    manager.stop()


def open_page(browser, url, html):
    """A page at `url`; every other request is refused locally."""
    context = browser.new_context()

    def handle(route):
        request_url = route.request.url
        if request_url.split("?")[0] == url:
            route.fulfill(status=200, content_type="text/html", body=html)
        elif "/accounts/login/" in request_url:
            route.fulfill(status=200, content_type="text/html", body="<html>login</html>")
        else:
            route.abort()

    context.route("**/*", handle)
    page = context.new_page()
    page.goto(url)
    return context, page


def capture(browser, url, fixture):
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    context, page = open_page(browser, url, html)
    try:
        return page.evaluate(CAPTURE)
    finally:
        context.close()


def test_capture_extracts_profile_fields_with_strategies(browser):
    snapshot = capture(browser, "https://www.instagram.com/artist123/", "profile-page.html")
    assert snapshot["ready"] and not snapshot["blocked"]
    profile = snapshot["profile"]
    assert profile["full_name"] == {"value": "Artist One", "strategy": "meta_title"}
    assert profile["user_id"] == {"value": "4242", "strategy": "meta_owner_id"}
    stats = profile["stats"]
    assert stats["followers"] == {"raw": "2,431", "strategy": "title_attribute"}
    assert (
        stats["following"]["strategy"] == "meta_description" and stats["following"]["raw"] == "310"
    )
    assert stats["posts"]["raw"] == "57"
    assert profile["bio"]["strategy"] == "header_heading"
    assert profile["bio"]["value"].startswith("rapper | new single out now")
    assert "Musician/band" in profile["category"]["candidates"]
    assert profile["contacts"]["emails"] == ["Booking@Artist123.com"]
    assert snapshot["external_url"] == "https://linktr.ee/artist123"
    # Auto-generated image descriptions and the suggestions block are not profile data.
    assert snapshot["captions"] == ["New single out now!", "Studio night"]
    assert "suggested_user" not in json.dumps(snapshot)
    assert "404 555 0100" not in json.dumps(snapshot)


def test_browser_provider_turns_the_snapshot_into_a_profile(browser):
    snapshot = capture(browser, "https://www.instagram.com/artist123/", "profile-page.html")
    result = InstagramProfileResolver().resolve(
        "artist123",
        ProfileResolveContext(fetch=lambda step, args: snapshot),
    )
    assert result.ok
    profile = result.profile
    assert (profile.followers_count, profile.following_count, profile.posts_count) == (
        2431,
        310,
        57,
    )
    assert profile.category_name == "Musician/band" and profile.id == "4242"
    assert profile.external_url == "https://linktr.ee/artist123"
    assert profile.emails == ["booking@artist123.com"]
    assert profile.phones == ["+442079460958"]
    assert profile.recent_captions == ["New single out now!", "Studio night"]


def test_private_and_missing_profiles(browser):
    private = capture(browser, "https://www.instagram.com/secret.singer/", "profile-private.html")
    assert private["ready"] and private["private"] is True
    assert private["profile"]["stats"]["followers"]["raw"] == "800"
    missing = capture(browser, "https://www.instagram.com/gone_user/", "profile-not-found.html")
    assert missing == {
        "url": "https://www.instagram.com/gone_user/",
        "ready": True,
        "blocked": False,
        "unavailable": True,
    }
