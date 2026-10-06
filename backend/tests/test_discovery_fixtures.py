"""Page scripts against sanitized Instagram HTML fixtures in bundled Chromium.

No request leaves the machine: every instagram.com URL is answered from a local file.
"""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "instagram"
SCRIPTS = {
    name: (ROOT / "src-tauri" / "src" / f"{name}.js").read_text(encoding="utf-8")
    for name in ("scout", "grid")
}


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


def run(browser, url: str, fixture: str, script: str, args: dict):
    context = browser.new_context()
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    context.route(
        "**/*",
        lambda route: (
            route.fulfill(status=200, content_type="text/html", body=html)
            if route.request.url.split("?")[0] == url
            else route.abort()
        ),
    )
    page = context.new_page()
    page.goto(url)
    try:
        return page.evaluate(SCRIPTS[script], args)
    finally:
        context.close()


def test_single_author_post_ignores_caption_suggestions(browser):
    result = run(
        browser,
        "https://www.instagram.com/p/ABC123/",
        "post-single-author.html",
        "scout",
        {},
    )
    assert result["author"] == "artist123" and result["author_strategy"] == "metadata"
    assert result["collaborators"] == ["artist123"]
    # Comments are no longer read from publication pages.
    assert "comments" not in result
    assert "producer_tag" not in str(result["collaborators"]) and "suggested_user" not in str(
        result
    )


def test_collab_post_reports_every_coauthor_from_the_header_only(browser):
    result = run(
        browser,
        "https://www.instagram.com/reel/COLLAB1/",
        "post-collab.html",
        "scout",
        {},
    )
    assert result["author"] == "rapdaily" and result["author_strategy"] == "post_header"
    assert result["collaborators"] == ["rapdaily", "artist_a", "artist_b"]
    assert not result["collaborators_truncated"]


def test_and_others_is_flagged_not_guessed(browser):
    result = run(
        browser,
        "https://www.instagram.com/p/OTH1/",
        "post-others.html",
        "scout",
        {},
    )
    assert result["collaborators"] == ["label_page"] and result["collaborators_truncated"] is True


def test_tagged_post_author_comes_from_structured_data(browser):
    result = run(
        browser,
        "https://www.instagram.com/p/TAG1/",
        "tagged-post.html",
        "scout",
        {},
    )
    assert result["author"] == "newrapper" and result["author_strategy"] == "structured_data"
    assert "musicblog" not in result["collaborators"]


def test_missing_author_is_reported_with_a_sanitised_fragment(browser):
    result = run(
        browser,
        "https://www.instagram.com/p/NONE1/",
        "post-no-author.html",
        "scout",
        {"debug": True},
    )
    assert result["author"] is None and result["parse_error"] == "author_not_found"
    assert (
        result["debug"]["reason"] == "author_not_found" and "<script" not in result["debug"]["html"]
    )


def test_grid_scrolls_lazily_skips_processed_and_stops(browser):
    args = {
        "maxPosts": 4,
        "maxScrollRounds": 10,
        "scrollDelayMs": 300,
        "maxNoProgressRounds": 2,
        "skip": ["P1", "P3"],
    }
    result = run(browser, "https://www.instagram.com/musicblog/", "profile-grid.html", "grid", args)
    assert result["ready"] is True
    assert result["posts"] == [
        "https://www.instagram.com/musicblog/reel/R2/",
        "https://www.instagram.com/p/P4/",
        "https://www.instagram.com/p/P5/",
        "https://www.instagram.com/tv/T6/",
    ]
    assert result["already_processed"] == 2 and result["end_reason"] == "enough"
    # Asking for more than exists ends at the grid end, not in an endless scroll.
    result = run(
        browser,
        "https://www.instagram.com/musicblog/",
        "profile-grid.html",
        "grid",
        {**args, "maxPosts": 50},
    )
    assert result["end_reason"] == "end_of_grid" and result["seen"] == 9
