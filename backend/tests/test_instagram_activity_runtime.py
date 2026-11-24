"""Mailing RPC -> persistent activity queue -> real browser executor, entirely offline."""

import pytest
from sqlalchemy import select
from test_instagram_activity import send_batch, tick
from test_outreach import app as outreach_app

from artist_lead_finder import instagram_activity_browser
from artist_lead_finder.chromium_runtime import Window
from artist_lead_finder.models import (
    InstagramActivityState,
    InstagramActivityTask,
    OutboundMessageJob,
    OutreachSender,
)
from artist_lead_finder.outreach.activity import ActivityEngine

app = outreach_app
HOME = "https://www.instagram.com/"
REELS_PAGE = """<main><video width="360" height="600" autoplay muted playsinline></video></main>
<script>
history.replaceState({}, '', '/reels/ONE/');
const canvas = document.createElement('canvas');
canvas.width = 180; canvas.height = 300;
const ctx = canvas.getContext('2d');
document.querySelector('video').srcObject = canvas.captureStream(10);
setInterval(() => { ctx.fillStyle = 'blue'; ctx.fillRect(0,0,180,300); }, 100);
window.scrollInputs = 0;
addEventListener('wheel', () => window.scrollInputs++);
</script>"""
TIMELINE_PAGE = '<main><article><a href="/p/ONE/"><time>Today</time></a></article></main>'
POST_PAGE = """<main><section>
<button onclick="this.querySelector('svg').setAttribute('aria-label','Unlike')">
<svg width="24" height="24" aria-label="Like"></svg></button>
<svg aria-label="Comment"></svg>
</section></main>"""


@pytest.fixture
def mailing(app, monkeypatch):
    service, sessions, clock, senders, _ = app
    runtime, sender = service.chromium, senders[0]
    requests = []
    monkeypatch.setattr(instagram_activity_browser, "monotonic", lambda: clock().timestamp())

    def open_browser():
        try:
            browser = runtime._engine().launch(headless=True, channel="chromium")
        except Exception as error:
            pytest.skip(f"Chromium unavailable: {type(error).__name__}")
        context = browser.new_context()
        context.set_offline(True)
        context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": "activity-test",
                    "domain": ".instagram.com",
                    "path": "/",
                }
            ]
        )

        def route(route):
            requests.append(dict(route.request.headers))
            if "/direct/" in route.request.url:
                body = "<main><textarea>Unsent DM draft</textarea></main>"
            elif route.request.url.endswith("/reels/"):
                body = REELS_PAGE
            elif route.request.url.endswith("/p/ONE/"):
                body = POST_PAGE
            else:
                body = TIMELINE_PAGE
            route.fulfill(body=body, content_type="text/html; charset=utf-8")

        context.route(HOME + "**", route)
        page = context.new_page()
        page.goto(HOME + "direct/inbox/")
        runtime.windows[sender] = Window(browser, context, page)

    runtime._browser_executor.submit(open_browser).result(timeout=30)
    service.outreach.window_open = runtime.is_open
    service.activity = ActivityEngine(sessions, runtime, now=clock)
    service.handlers = service._handlers()
    service.call(
        "outreach.workspace_update",
        {
            "usernames": [f"artist{i}" for i in range(6)],
            "messages": ["hello"],
            "sender_ids": [sender],
        },
    )
    service.call("outreach.workspace_start", {})
    send_batch(service, clock)
    yield service, sessions, clock, sender, runtime, requests


def settle(runtime, sender, *, reels=False):
    def wait_for_page():
        page = runtime.windows[sender].activity_tab.page
        page.wait_for_url(HOME + "**", timeout=5000)
        page.wait_for_load_state("domcontentloaded")
        if reels:
            page.wait_for_function("document.querySelector('video')?.readyState >= 2", timeout=5000)

    runtime._browser_executor.submit(wait_for_page).result(timeout=10)


def assert_main_untouched(runtime, sender, *, activity_open):
    def inspect():
        window = runtime.windows[sender]
        assert window.page.url == HOME + "direct/inbox/"
        assert window.page.locator("textarea").input_value() == "Unsent DM draft"
        assert len(window.context.pages) == (2 if activity_open else 1)
        assert bool(window.activity_tab) == activity_open

    runtime._browser_executor.submit(inspect).result(timeout=10)


def test_five_minute_reels_then_feed_likes_use_a_separate_authenticated_tab(mailing):
    service, sessions, clock, sender, runtime, requests = mailing
    first = tick(service)
    assert first["worked"] and first["outcome"] == "pending"
    settle(runtime, sender, reels=True)
    assert tick(service)["outcome"] == "pending"
    for _ in range(24):
        clock.advance(seconds=12)
        assert tick(service)["outcome"] == "pending"
    assert service.call("outreach.next_internal", {}) is None
    runtime._browser_executor.submit(
        lambda: runtime.windows[sender].activity_tab.page.wait_for_function("scrollInputs > 0")
    ).result(timeout=8)
    clock.advance(seconds=11)
    assert tick(service)["outcome"] == "pending"
    clock.advance(seconds=1)
    last = tick(service)
    assert last["task_id"] == first["task_id"] and last["outcome"] == "done"
    with sessions() as session:
        assert session.get(InstagramActivityTask, first["task_id"]).status == "done"
    assert_main_untouched(runtime, sender, activity_open=False)

    assert tick(service)["outcome"] == "pending"  # Open the main feed.
    settle(runtime, sender)
    assert_main_untouched(runtime, sender, activity_open=True)
    for _ in range(3):
        assert tick(service)["outcome"] == "pending"
    assert tick(service)["outcome"] == "done"
    assert service.call("outreach.next_internal", {}) is None
    assert tick(service)["outcome"] == "pending"  # Open the post found in the feed.
    settle(runtime, sender)
    assert tick(service)["outcome"] == "pending"  # Click Like.
    assert tick(service)["outcome"] == "done"  # Confirm Unlike.
    assert service.call("outreach.next_internal", {})
    assert len(requests) >= 4
    assert all("sessionid=activity-test" in request.get("cookie", "") for request in requests)
    service.call("outreach.workspace_stop", {})
    assert_main_untouched(runtime, sender, activity_open=False)
    assert not service.call("outreach.activity_tick_internal", {})["worked"]


@pytest.mark.parametrize("status", [200, 429])
def test_late_instagram_refusal_in_real_activity_tab_blocks_the_next_dm(mailing, status):
    service, sessions, clock, sender, runtime, _ = mailing
    assert tick(service)["worked"]
    settle(runtime, sender)

    def refuse():
        page = runtime.windows[sender].activity_tab.page
        page.route(
            "**/api/v1/activity-test/",
            lambda route: route.fulfill(
                status=status, json={"status": "fail", "message": "feedback_required"}
            ),
        )
        page.evaluate("async () => (await fetch('/api/v1/activity-test/')).json()")

    runtime._browser_executor.submit(refuse).result(timeout=10)
    clock.advance(minutes=4)
    assert tick(service)["outcome"] == "error"
    assert service.call("outreach.next_internal", {}) is None
    assert_main_untouched(runtime, sender, activity_open=False)
    with sessions() as session:
        health = session.get(OutreachSender, sender)
        assert health.status == "rate_limited" and health.until is None
        assert session.get(InstagramActivityState, sender).stopped
        assert not session.scalar(
            select(OutboundMessageJob.id).where(OutboundMessageJob.status == "sending")
        )
    clock.advance(days=1)
    assert service.call("outreach.next_internal", {}) is None
    assert not service.call("outreach.activity_tick_internal", {})["worked"]
