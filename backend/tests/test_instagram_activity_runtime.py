"""Mailing RPC -> persistent activity queue -> real browser executor, entirely offline."""

import random

import pytest
from sqlalchemy import select
from test_outreach import app as outreach_app

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


@pytest.fixture
def mailing(app):
    service, sessions, clock, senders, _ = app
    runtime, sender = service.chromium, senders[0]
    requests = []

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
            else:
                body = '<main><header>Artist</header><a href="/artistone/">Artist</a></main>'
            route.fulfill(body=body, content_type="text/html; charset=utf-8")

        context.route(HOME + "**", route)
        page = context.new_page()
        page.goto(HOME + "direct/inbox/")
        runtime.windows[sender] = Window(browser, context, page)

    runtime._browser_executor.submit(open_browser).result(timeout=30)
    service.outreach.window_open = runtime.is_open
    service.activity = ActivityEngine(
        sessions, runtime, service.settings, now=clock, rng=random.Random(4)
    )
    service.handlers = service._handlers()
    service.call(
        "outreach.workspace_update",
        {"usernames": ["artistone", "artisttwo"], "messages": ["hello"], "sender_ids": [sender]},
    )
    service.call("outreach.workspace_start", {})
    job = service.call("outreach.next_internal", {})
    assert job
    service.call(
        "outreach.commit_internal",
        {
            "job_id": job["job_id"],
            "token": job["token"],
            "result": {"outcome": "sent", "message_id": "fixture-message", "thread_id": "fixture"},
        },
    )
    yield service, sessions, clock, sender, runtime, requests


def settle(runtime, sender):
    def wait_for_page():
        page = runtime.windows[sender].activity_tab.page
        page.wait_for_url(HOME + "**", timeout=5000)
        page.wait_for_load_state("domcontentloaded")

    runtime._browser_executor.submit(wait_for_page).result(timeout=10)


def assert_main_untouched(runtime, sender, *, activity_open):
    def inspect():
        window = runtime.windows[sender]
        assert window.page.url == HOME + "direct/inbox/"
        assert window.page.locator("textarea").input_value() == "Unsent DM draft"
        assert len(window.context.pages) == (2 if activity_open else 1)
        assert bool(window.activity_tab) == activity_open

    runtime._browser_executor.submit(inspect).result(timeout=10)


def test_start_mailing_runs_real_browser_activity_and_stop_closes_only_its_tab(mailing):
    service, sessions, clock, sender, runtime, requests = mailing
    first = service.call("outreach.activity_tick_internal", {})
    assert first["worked"] and first["outcome"] == "pending"
    settle(runtime, sender)
    clock.advance(seconds=3)
    second = service.call("outreach.activity_tick_internal", {})
    assert second["task_id"] == first["task_id"] and second["outcome"] == "done"
    with sessions() as session:
        assert session.get(InstagramActivityTask, first["task_id"]).status == "done"
    assert_main_untouched(runtime, sender, activity_open=True)
    assert len(requests) >= 2
    assert all("sessionid=activity-test" in request.get("cookie", "") for request in requests)
    service.call("outreach.workspace_stop", {})
    assert_main_untouched(runtime, sender, activity_open=False)
    assert not service.call("outreach.activity_tick_internal", {})["worked"]


@pytest.mark.parametrize("status", [200, 429])
def test_late_instagram_refusal_in_real_activity_tab_blocks_the_next_dm(mailing, status):
    service, sessions, clock, sender, runtime, _ = mailing
    assert service.call("outreach.activity_tick_internal", {})["worked"]
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
