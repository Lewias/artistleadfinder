"""Browse synthetic Google and site pages, including cookie banners and access checks."""

import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright

from artist_lead_finder import cookie_warmup_browser
from artist_lead_finder.cookie_warmup_browser import SessionComplete, VisitSession, browse
from artist_lead_finder.errors import UserError


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, channel="chromium")
        except Exception as error:
            pytest.skip(f"Chromium unavailable: {type(error).__name__}")
        yield browser
        browser.close()


def test_search_visit_accepts_cookies_and_opens_material_without_account_actions(browser):
    context = browser.new_context()
    page = context.new_page()
    pages = {
        "https://www.google.com/": '<form action="/search"><input name="q"></form>',
        "https://www.google.com/search?q=example.com": '<a href="https://example.com/"><h3>Example</h3></a>',
        "https://example.com/": """
            <div id="banner"></div>
            <script>
              const shadow=document.querySelector('#banner').attachShadow({mode:'open'})
              shadow.innerHTML='<button>Приемане на всички</button>'
              shadow.querySelector('button').onclick=()=>{
                document.cookie='accepted=yes; path=/'
                document.querySelector('#banner').remove()
              }
            </script>
            <a href="/account/delete">Delete account</a>
            <a href="/product/one">Product</a>""",
        "https://example.com/product/one": "<main>The product</main>",
    }
    page.route(
        "**/*",
        lambda route: route.fulfill(
            body=pages[route.request.url], content_type="text/html; charset=utf-8"
        ),
    )
    events = []
    session = VisitSession(
        page,
        time.monotonic() + 60,
        threading.Event(),
        lambda text, **values: events.append((text, values)),
    )
    session.wait = lambda seconds: session.check()
    try:
        session.visit("https://example.com/")
        assert page.url == "https://example.com/product/one"
        assert any(row["name"] == "accepted" for row in context.cookies())
        assert events[-1][1] == {"visited": True}
    finally:
        context.close()


def test_check_human_stops_and_stop_or_deadline_prevent_navigation(browser):
    page = browser.new_page()
    page.set_content("<main>Verify you are human</main>")
    stopped = threading.Event()
    session = VisitSession(page, time.monotonic() + 60, stopped, lambda *args, **kw: None)
    try:
        with pytest.raises(UserError, match="проверку человека"):
            session.check_access()
        stopped.set()
        with pytest.raises(InterruptedError):
            session.visit("https://example.com/")
        stopped.clear()
        session.end = time.monotonic() - 1
        with pytest.raises(SessionComplete):
            session.visit("https://example.com/")
        assert page.url == "about:blank"
    finally:
        page.close()


@pytest.mark.parametrize("fail", [False, True])
def test_session_leaves_user_tabs_and_profile_open_and_keeps_only_error_tab(monkeypatch, fail):
    owned_page, existing_page = Mock(), Mock()
    context = Mock(pages=[existing_page])
    context.new_page.return_value = owned_page
    browser = Mock(contexts=[context])
    playwright = Mock()
    playwright.chromium.connect_over_cdp.return_value = browser
    monkeypatch.setattr(cookie_warmup_browser, "sync_playwright", lambda: nullcontext(playwright))
    monkeypatch.setattr(
        cookie_warmup_browser.ProfileClient, "open_profile", lambda *args: ("ws://test", False)
    )
    stopping = threading.Event()

    def visit(session, url):
        if fail:
            raise UserError("Проверка человека")
        stopping.set()

    monkeypatch.setattr(VisitSession, "visit", visit)
    with pytest.raises(UserError if fail else InterruptedError):
        browse(
            {"provider": "nst", "port": 8848, "token": "test", "id": "abc"},
            SimpleNamespace(minutes=1, sites=["https://example.com/"]),
            stopping,
            Mock(),
        )
    existing_page.close.assert_not_called()
    browser.close.assert_not_called()
    context.close.assert_not_called()
    assert owned_page.close.call_count == (0 if fail else 1)
