"""Local browser fixtures only: no requests or posts reach Threads."""

import html
import sqlite3

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from sqlalchemy import select

from artist_lead_finder.database import open_database
from artist_lead_finder.models import ThreadsAccount
from artist_lead_finder.threads.api import ThreadsError
from artist_lead_finder.threads.browser import identity, publish, recent


@pytest.fixture
def website():
    posts = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True, channel="chromium")
        except PlaywrightError as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Chromium is not installed on this runner")
            raise
        context = browser.new_context(service_workers="block")

        def create(_source, text):
            item = {"id": f"post{len(posts) + 1}", "text": text}
            posts.append(item)

        context.expose_binding("fixtureStorePost", create)

        def serve(route):
            if route.request.resource_type != "document":
                route.abort()
                return
            articles = "".join(
                f'<article><a href="/@beats.daily/post/{post["id"]}">'
                '<time datetime="2026-11-16T12:00:00Z">Now</time></a>'
                f'<div data-thread-text style="white-space:pre-wrap">'
                f"{html.escape(post['text'])}</div></article>"
                for post in posts
            )
            route.fulfill(
                content_type="text/html; charset=utf-8",
                body="""
                <html><body><nav><a href="/@beats.daily">
                  <svg aria-label="Profile"></svg></a>
                  <button onclick="compose()">Create</button></nav>
                """
                + articles
                + """
                <script>
                function compose() {
                  const d = document.createElement('div'); d.setAttribute('role', 'dialog');
                  const editor = document.createElement('div'); editor.contentEditable = 'true';
                  editor.setAttribute('role', 'textbox'); d.append(editor);
                  const b = document.createElement('button'); b.textContent = 'Post';
                  b.onclick = async () => {await fixtureStorePost(editor.innerText); d.remove();};
                  d.append(b); document.body.append(d);
                }
                </script></body></html>""",
            )

        context.route("**/*", serve)
        page = context.new_page()
        page.goto("https://www.threads.com/@beats.daily")
        yield page, posts
        browser.close()


def test_browser_recognizes_own_navigation_and_confirms_a_new_post(website):
    page, posts = website
    assert identity(page)["username"] == "beats.daily"
    text = "Новый релиз\nПремьера сегодня <3"
    found = publish(page, "beats.daily", text)
    assert found["id"] == "post1" and found["text"] == text
    assert len(posts) == 1
    assert recent(page, "beats.daily")[0]["permalink"].endswith("/post/post1")


def test_browser_refuses_to_publish_as_a_different_account(website):
    page, posts = website
    with pytest.raises(ThreadsError, match="другой аккаунт"):
        publish(page, "another.account", "Не отправлять")
    assert posts == []


@pytest.mark.parametrize(
    "markup",
    [
        '<a href="/@beats.daily"><svg><title>Профиль</title></svg></a>',
        '<a href="https://www.threads.net/@beats.daily" aria-label=" PROFILE "></a>',
        '<nav><a href="/@beats.daily">Профиль</a></nav>',
    ],
)
def test_identity_accepts_site_profile_labels_and_legacy_threads_domain(website, markup):
    page, posts = website
    page.set_content(markup)
    assert identity(page)["username"] == "beats.daily"


def test_identity_never_takes_an_arbitrary_feed_profile_or_an_external_link(website):
    page, posts = website
    page.set_content(
        '<article><a href="/@another">Profile</a></article>'
        '<a href="https://threads.com.evil.test/@another" aria-label="Profile"></a>'
    )
    assert identity(page) == {}


def test_schema_29_api_accounts_gain_browser_fields_without_losing_tokens(tmp_path):
    path = tmp_path / "db.sqlite3"
    engine, sessions = open_database(path)
    with sessions.begin() as session:
        session.add(
            ThreadsAccount(threads_user_id="real-api-id", username="artist", token_box=b"encrypted")
        )
    engine.dispose()
    with sqlite3.connect(path) as raw:
        raw.execute("ALTER TABLE threads_accounts DROP COLUMN auth_mode")
        raw.execute("ALTER TABLE threads_accounts DROP COLUMN browser_profile_id")
        raw.execute("DELETE FROM schema_migrations WHERE version = 30")
    engine, sessions = open_database(path)
    try:
        with sessions() as session:
            account = session.scalar(select(ThreadsAccount))
            assert account.auth_mode == "api" and account.browser_profile_id is None
            assert account.token_box == b"encrypted" and account.username == "artist"
    finally:
        engine.dispose()
