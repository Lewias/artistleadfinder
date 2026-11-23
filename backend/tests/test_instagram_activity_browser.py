"""Actual Chromium and routed Instagram pages. No traffic reaches Instagram."""

import time
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from artist_lead_finder.chromium_runtime import Window
from artist_lead_finder.instagram_activity_browser import ActivityTab, candidates, canonical

PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
POST = f'''<main><article><img src="{PIXEL}" style="width:100px;height:100px">
  <section><button onclick="window.clicks=(window.clicks||0)+1;
    this.querySelector('svg').setAttribute('aria-label','Unlike')">
    <svg width="20" height="20" aria-label="Like"></svg></button>
    <button><svg aria-label="Comment"></svg></button></section>
  <a href="/artist/">artist</a></article></main>'''
FEED = f'''<main><article><img src="{PIXEL}"><a href="/p/ONE/">Post</a>
  <a href="/artist/">artist</a><a href="/stories/artist/">Story</a></article></main>'''
STORY_MEDIA = f'<img src="{PIXEL}" style="position:fixed;inset:5vh 30vw;width:40vw;height:90vh">'


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, channel="chromium")
        except Exception as error:
            pytest.skip(f"Chromium unavailable: {type(error).__name__}")
        yield browser
        browser.close()


@pytest.fixture
def tab(browser):
    context = browser.new_context()
    context.set_offline(True)

    def route(request):
        url = request.request.url
        if url.endswith("/stories/artist/"):
            request.fulfill(
                body="<script>location.href='/stories/artist/123/'</script>",
                content_type="text/html",
            )
            return
        if "/stories/" in url:
            body = f"<section>{STORY_MEDIA}</section>"
        elif "/p/" in url:
            body = POST
        elif url.endswith("/artist/"):
            body = '<main><header>Artist</header><a href="/p/ONE/">Post</a></main>'
        elif "/direct/" in url:
            body = "<main><textarea>Unsent DM draft</textarea></main>"
        else:
            body = FEED
        request.fulfill(body=body, content_type="text/html; charset=utf-8")

    context.route("https://www.instagram.com/**", route)
    main = context.new_page()
    main.goto("https://www.instagram.com/direct/inbox/")
    window = Window(browser, context, main)
    tab = ActivityTab(window)
    yield tab
    assert main.url == "https://www.instagram.com/direct/inbox/"
    assert main.locator("textarea").input_value() == "Unsent DM draft"
    context.close()


def task(kind, path="", **payload):
    return SimpleNamespace(
        id=1,
        kind=kind,
        phase="open",
        payload={"url": "https://www.instagram.com/" + path, **payload},
    )


def advance(tab, task, guard=lambda: True):
    result = tab.step(task, guard)
    assert result["outcome"] == "pending"
    task.phase = result["phase"]
    expected = task.payload.get("from_url") if task.kind == "navigate" else task.payload["url"]
    if task.kind == "story":
        expected += "123/"
    tab.page.wait_for_url(lambda url: str(url).startswith(expected), timeout=5000)
    tab.page.wait_for_load_state("domcontentloaded")
    return tab.step(task, guard)


@pytest.mark.parametrize(
    "kind,path",
    [
        ("discover", ""),
        ("feed", "p/ONE/"),
        ("profile", "artist/"),
        ("story", "stories/artist/"),
    ],
)
def test_supported_views_in_separate_tab(tab, kind, path):
    result = advance(tab, task(kind, path))
    assert result["outcome"] == "done", result
    if kind == "discover":
        assert {row["kind"] for row in result["candidates"]} == {
            "feed",
            "profile",
            "story",
            "like",
            "navigate",
        }
    if kind == "story":
        assert tab.page.is_closed()  # Story autoplay ends with this task.


def test_like_is_confirmed_once_and_never_toggles_an_existing_like(tab):
    item = task("like", "p/ONE/")
    result = advance(tab, item)
    assert result == {"outcome": "pending", "phase": "confirm"}
    item.phase = "confirm"
    assert tab.step(item, lambda: True)["outcome"] == "done"
    assert tab.page.evaluate("window.clicks") == 1
    item.phase = "inspect"
    assert tab.step(item, lambda: True)["reason"] == "already_liked"
    assert tab.page.evaluate("window.clicks") == 1


@pytest.mark.parametrize("kind", ["feed", "like"])
def test_post_permalink_without_article_supports_views_and_only_the_post_like(tab, kind):
    body = POST.replace("<article>", "<div>").replace("</article>", "</div>")
    body = body.replace(
        "<section>",
        """<div><button onclick="window.commentClicks=(window.commentClicks||0)+1">
          <svg aria-label="Like"></svg></button></div><section>""",
    )
    tab.page.route("**/p/ONE/", lambda route: route.fulfill(body=body, content_type="text/html"))
    item = task(kind, "p/ONE/")
    result = advance(tab, item)
    if kind == "like":
        assert result == {"outcome": "pending", "phase": "confirm"}
        item.phase = "confirm"
        result = tab.step(item, lambda: True)
        assert tab.page.evaluate("window.clicks") == 1
    assert result["outcome"] == "done"
    assert tab.page.evaluate("window.commentClicks || 0") == 0


def test_notification_prompt_is_dismissed_before_activity_without_enabling_notifications(tab):
    body = (
        POST
        + """<div role="dialog"><h2>Включить уведомления</h2>
      <button onclick="window.enabled=true">Включить</button>
      <button onclick="this.parentElement.remove()">Не сейчас</button></div>"""
    )
    tab.page.route(
        "**/p/ONE/",
        lambda route: route.fulfill(body=body, content_type="text/html; charset=utf-8"),
    )
    item = task("like", "p/ONE/")
    assert advance(tab, item) == {"outcome": "pending"}
    assert tab.page.get_by_role("dialog").count() == 0
    assert not tab.page.evaluate("window.enabled || window.clicks || false")
    assert tab.step(item, lambda: True) == {"outcome": "pending", "phase": "confirm"}
    item.phase = "confirm"
    assert tab.step(item, lambda: True)["outcome"] == "done"


def test_transition_uses_existing_link_and_rechecks_guard_before_click(tab):
    item = task("navigate", "p/ONE/", from_url="https://www.instagram.com/artist/")
    checks = iter([True, True, False])
    result = advance(tab, item, lambda: next(checks))
    assert result == {"outcome": "pending"}
    assert tab.page.url == "https://www.instagram.com/artist/"
    result = tab.step(item, lambda: True)
    assert result == {"outcome": "pending", "phase": "confirm"}
    item.phase = "confirm"
    tab.page.wait_for_url("**/p/ONE/")
    assert tab.step(item, lambda: True)["outcome"] == "done"


@pytest.mark.parametrize(
    "status,reason",
    [(401, "login"), (403, "action_blocked"), (429, "rate_limited"), (500, "instagram_error")],
)
def test_instagram_http_errors_stop_before_an_action(tab, status, reason):
    tab.page.route("**/p/FAIL/", lambda route: route.fulfill(status=status, body="refused"))
    item = task("like", "p/FAIL/")
    assert advance(tab, item)["reason"] == reason


def test_checkpoint_unsupported_and_missing_controls_are_skipped_without_side_effects(tab):
    item = task("like", "p/ONE/")
    advance(tab, item)
    tab.page.goto("https://www.instagram.com/challenge/")
    assert tab.step(item, lambda: True)["reason"] == "checkpoint"
    tab.page.goto("https://www.instagram.com/artist/")
    assert tab.step(task("unknown", "artist/"), lambda: True)["reason"] == "unsupported"
    item.phase = "inspect"
    tab.page.goto("https://www.instagram.com/p/ONE/")
    tab.page.set_content("<main><article>Unavailable controls</article></main>")
    tab.opened_at = time.monotonic() - 20
    assert tab.step(item, lambda: True)["outcome"] == "skipped"


@pytest.mark.parametrize(
    "code,reason",
    [
        ("feedback_required", "rate_limited"),
        ("checkpoint_required", "checkpoint"),
        ("login_required", "login"),
        ("unknown_error", "instagram_error"),
    ],
)
def test_structured_api_errors_with_http_200_stop_actions(tab, code, reason):
    item = task("feed", "p/ONE/")
    assert advance(tab, item)["outcome"] == "done"
    tab.page.route(
        "**/api/v1/test/", lambda route: route.fulfill(json={"status": "fail", "message": code})
    )
    tab.page.evaluate("async () => (await fetch('/api/v1/test/')).json()")
    assert tab.step(item, lambda: True)["reason"] == reason


def test_content_keys_are_canonical_and_foreign_or_private_urls_are_rejected():
    assert canonical("https://www.instagram.com/artist/p/ONE/?tracking=x", "feed") == (
        "https://www.instagram.com/p/ONE/"
    )
    assert canonical("https://www.instagram.com/stories/artist/123/", "story") == (
        canonical("https://www.instagram.com/stories/artist/", "story")
    )
    for url in (
        "http://www.instagram.com/p/ONE/",
        "https://evil.test/p/ONE/",
        "https://key:secret@www.instagram.com/p/ONE/",
        "https://www.instagram.com/direct/",
    ):
        assert canonical(url, "navigate") is None
    assert canonical("https://www.instagram.com/stories/highlights/123/", "story") is None
    assert canonical("https://www.instagram.com/stories/highlights/", "story") is None


def test_story_tray_buttons_without_hrefs_offer_only_visible_story_owners(tab):
    tab.page.set_content("""<main>
      <div role="button" aria-label="История artist, не просмотрена">Artist</div>
      <button aria-label="musician's story, unseen">Musician</button>
      <div role="button" aria-label="История hidden, не просмотрена"
           style="display:none">Hidden</div>
      <button aria-label="История highlights, не просмотрена">Unsupported</button>
      <button aria-label="Like">Other action</button>
      <a href="/stories/artist/123/">Same viewer</a>
      <a href="/stories/highlights/123/">Highlight</a>
    </main>""")
    stories = [item["url"] for item in candidates(tab.page) if item["kind"] == "story"]
    assert stories == [
        "https://www.instagram.com/stories/artist/",
        "https://www.instagram.com/stories/musician/",
    ]


def test_story_opens_the_observed_tray_button_from_its_origin_page(tab):
    tab.page.route(
        "https://www.instagram.com/",
        lambda route: route.fulfill(
            body="""<main><div role="button" aria-label="История artist, не просмотрена"
              onclick="location.href='/stories/artist/123/'">Artist</div></main>""",
            content_type="text/html; charset=utf-8",
        ),
    )
    item = task("story", "stories/artist/", from_url="https://www.instagram.com/")
    assert tab.step(item, lambda: True) == {"outcome": "pending", "phase": "inspect"}
    item.phase = "inspect"
    tab.page.wait_for_url("https://www.instagram.com/")
    tab.page.wait_for_load_state("domcontentloaded")
    assert tab.step(item, lambda: True) == {"outcome": "pending", "phase": "confirm"}
    item.phase = "confirm"
    tab.page.wait_for_url("**/stories/artist/123/")
    tab.page.wait_for_load_state("domcontentloaded")
    assert tab.step(item, lambda: True) == {"outcome": "done"}
    assert tab.page.is_closed()


def test_owner_only_story_url_waits_for_the_player_instead_of_counting_feed_or_preview(tab):
    body = f'''<title>Instagram</title><main><article>{STORY_MEDIA}</article></main>
        <section><img src="{PIXEL}" style="position:fixed;right:0;top:5vh;width:22vw;height:90vh">
        </section>'''
    tab.page.route(
        "**/stories/artist/",
        lambda route: route.fulfill(body=body, content_type="text/html; charset=utf-8"),
    )
    item = task("story", "stories/artist/")
    assert tab.step(item, lambda: True) == {"outcome": "pending", "phase": "inspect"}
    item.phase = "confirm"
    tab.page.wait_for_url("**/stories/artist/")
    tab.page.wait_for_load_state("load")
    assert tab.step(item, lambda: True) == {"outcome": "pending"}
    tab.page.evaluate("document.title = 'Истории • Instagram'")
    assert tab.step(item, lambda: True) == {"outcome": "pending"}
    tab.page.locator("section").evaluate(
        "(e, html) => e.insertAdjacentHTML('beforeend', html)", STORY_MEDIA
    )
    tab.page.locator("section img").last.evaluate("img => img.decode()")
    assert tab.step(item, lambda: True)["outcome"] == "done"
    assert tab.page.is_closed()
