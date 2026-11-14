"""Selectors against synthetic pages routed locally, with no Instagram requests."""

import os
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from artist_lead_finder.errors import UserError
from artist_lead_finder.instagram_warmup_browser import warmup_action


@pytest.fixture(scope="module")
def browser():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True, channel="chromium")
        yield instance
        instance.close()


@pytest.fixture
def window(browser):
    page = browser.new_page()
    page.route("**/*", lambda route: route.fulfill(body="<main></main>"))
    page.goto("https://www.instagram.com/")
    yield SimpleNamespace(page=page, rate_limited=False)
    page.close()


def test_search_reuses_an_open_panel_and_extracts_only_profile_names(window):
    window.page.set_content("""<input placeholder="Поиск" />
      <a href="/small_shop/">small_shop Магазин</a>
      <a href="/explore/">explore</a><a href="/myself/">Профиль</a>
      <a href="https://example.com/foreign/">foreign</a>""")
    result = warmup_action(None, window, "a", "search", "магазин")
    assert result == {"usernames": ["small_shop"]}
    assert window.page.locator("input").input_value() == "магазин"


def test_search_ignores_hidden_controls_and_dismisses_the_initial_notice(window):
    window.page.set_content("""<a href="#" style="display:none">Search</a>
      <button onclick="document.querySelector('input').style.display='block'">Поиск Поиск</button>
      <input placeholder="Search" style="display:none" />
      <a href="/small_shop/">small_shop</a>
      <div role="dialog" style="position:fixed;inset:0;background:white">
        <button onclick="this.parentElement.remove()">Not Now</button>
      </div>""")
    assert warmup_action(None, window, "a", "search", "магазин")["usernames"] == ["small_shop"]


@pytest.mark.parametrize(
    "label,after",
    [
        ("Follow", "Following"),
        ("Подписаться", "Вы подписаны"),
        ("Подписаться", "Вы подписаны Значок стрелки вниз"),
    ],
)
def test_follow_is_confirmed_and_never_toggles_an_existing_follow(window, label, after):
    window.page.goto("https://www.instagram.com/shop/")
    window.page.set_content(f"""<main><header>
      <button onclick="this.textContent='{after}'; window.clicks=(window.clicks||0)+1">
      {label}</button></header></main>""")
    assert warmup_action(None, window, "a", "follow", "shop")["confirmed"]
    assert not warmup_action(None, window, "a", "follow", "shop")["confirmed"]
    assert window.page.evaluate("window.clicks") == 1


def test_feed_likes_posts_once_and_does_not_unlike(window):
    window.page.set_content("""<main>
      <article><button onclick="window.oldClicked=true">
        <svg aria-label="Unlike"></svg></button></article>
      <article><button onclick="this.querySelector('svg').setAttribute('aria-label','Unlike');
        window.newClicks=(window.newClicks||0)+1"><svg aria-label="Like"></svg></button></article>
      </main>""")
    assert warmup_action(None, window, "a", "like", "")["confirmed"]
    assert not warmup_action(None, window, "a", "like", "")["confirmed"]
    assert window.page.evaluate("window.newClicks") == 1
    assert window.page.evaluate("window.oldClicked || false") is False


def test_profile_fallback_keeps_exact_follower_count(window):
    window.page.goto("https://www.instagram.com/shop/")
    window.page.set_content("""<main><header>Магазин подарков, доставка
      <a href="/shop/followers/"><span title="9,999">9.9K followers</span></a>
      </header></main>""")
    runtime = SimpleNamespace(page_profile=lambda *args: None)
    result = warmup_action(runtime, window, "a", "profile", "shop")
    assert result["followers_count"] == 9999
    assert result["is_private"] is False


@pytest.mark.parametrize("path", ["/accounts/login/", "/challenge/", "/checkpoint/"])
def test_login_and_checkpoint_stop_before_a_click(window, path):
    window.page.goto("https://www.instagram.com" + path)
    with pytest.raises(UserError):
        warmup_action(None, window, "a", "like", "")


def test_rate_limit_and_wrong_profile_stop_before_a_click(window):
    window.rate_limited = True
    with pytest.raises(UserError):
        warmup_action(None, window, "a", "follow", "shop")
    window.rate_limited = False
    with pytest.raises(UserError):
        warmup_action(None, window, "a", "follow", "shop")
