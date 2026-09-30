"""Sending through Instagram's interface, against local stand-in pages in real Chromium.

Every request is answered by `page.route`: nothing reaches Instagram.
"""

import os

import pytest

from artist_lead_finder import direct_message
from artist_lead_finder.direct_message import send_direct

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
sync_api = pytest.importorskip("playwright.sync_api")

PROFILE = """<!doctype html><meta charset="utf-8"><body><main>
<h2>{name}</h2>{buttons}</main>
<div role="button">Сообщения</div>
<script>
const button = [...document.querySelectorAll('main [role=button]')]
  .find(item => item.textContent === 'Отправить сообщение');
if (button) button.onclick = () => setTimeout(() => {{
  history.pushState(null, '', '/direct/t/340282366/');
  document.querySelector('main').innerHTML =
    '<div id="log"></div>' +
    '<div role="textbox" contenteditable="true" aria-label="Сообщение"></div>';
  const box = document.querySelector('[role=textbox]');
  box.addEventListener('keydown', event => {{
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    window.sent = (window.sent || []).concat(box.innerText);
    box.innerHTML = '';
    if ({fail_send}) document.querySelector('#log').textContent = 'Не удалось отправить';
  }});
}}, 300);
</script></body>"""
FOLLOW = '<button type="button">Подписаться</button>'
MESSAGE = '<div role="button" tabindex="0">Отправить сообщение</div>'


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_api.sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True, channel="chromium")
            yield chromium
            chromium.close()
    except Exception as error:  # Chromium is not installed in this environment.
        pytest.skip(f"Chromium unavailable: {type(error).__name__}")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct_message, "PROFILE_WAIT", 3.0)
    monkeypatch.setattr(direct_message, "SETTLE", 0.5)


def profile_page(browser, username, buttons, fail_send=False):
    page = browser.new_page()
    html = PROFILE.format(name=username, buttons=buttons, fail_send=str(fail_send).lower())
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(f"https://www.instagram.com/{username}/")
    return page


def test_sends_through_the_message_button(browser):
    page = profile_page(browser, "jaycarter", FOLLOW + MESSAGE)
    result = send_direct(page, "jaycarter", "Yo Jay\nlets work")
    assert result == {"outcome": "sent", "thread_id": "340282366"}
    assert page.evaluate("window.sent") == ["Yo Jay\nlets work"]
    page.close()


def test_follow_only_profile_is_not_written_to(browser):
    page = profile_page(browser, "followonly", FOLLOW)
    assert send_direct(page, "followonly", "hi")["error"] == "messages_closed"
    assert page.evaluate("window.sent") is None
    page.close()


def test_a_send_error_shown_by_instagram_is_a_refusal(browser):
    page = profile_page(browser, "jaycarter", MESSAGE, fail_send=True)
    assert send_direct(page, "jaycarter", "hi")["error"] == "rejected"
    page.close()
    page = profile_page(browser, "jaycarter", MESSAGE, fail_send=True)
    assert send_direct(page, "jaycarter", "hi", rate_limited=lambda: True)["error"] == (
        "rate_limited"
    )
    page.close()


def test_wrong_page_or_input_sends_nothing(browser):
    page = profile_page(browser, "someoneelse", MESSAGE)
    assert send_direct(page, "jaycarter", "hi")["error"] == "no_instagram_tab"
    assert send_direct(page, "someoneelse", "")["error"] == "bad_request"
    assert page.evaluate("window.sent") is None
    page.close()
