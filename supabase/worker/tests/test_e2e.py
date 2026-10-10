"""The real core, the page loop and a real Chromium on stand-in Instagram pages (no
network), the way the cloud parser runs a job: source grid → post and reel → the artist's
profile → one lead with its contact. The same pages as scripts/smoke-scout.py.
"""

import html
import importlib.util
import uuid

import pytest

from cloud_worker.driver import Driver, load_scripts
from cloud_worker.engine import Engine

from .conftest import ROOT

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("playwright") is None, reason="Playwright is not installed"
)

PROFILE = "c" * 32
SOURCE = "https://www.instagram.com/music_news/"
POST = "https://www.instagram.com/music_news/p/first/"
REEL = "https://www.instagram.com/music_news/reel/second/"
ARTIST = "https://www.instagram.com/new_rapper/"


def page_html(title, description, body):
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Instagram</title>'
        f'<meta property="og:title" content="{html.escape(title, quote=True)}">'
        f'<meta property="og:description" content="{html.escape(description, quote=True)}">'
        f"</head><body><main>{body}</main></body></html>"
    )


PAGES = {
    SOURCE: page_html(
        "Music News (@music_news)",
        "",
        f'<h2>music_news</h2><a href="{POST}">Post</a><a href="{REEL}">Reel</a>',
    ),
    POST: page_html(
        "Instagram",
        '100 likes - new_rapper on September 25, 2026: "New single with @caption_only". ',
        '<article><header><a href="/new_rapper/">new_rapper</a></header>'
        "<span>Great post @unrelated</span></article>",
    ),
    REEL: page_html(
        "Instagram",
        'new_rapper on September 25, 2026: "Studio day". ',
        '<article><header><a href="/new_rapper/">new_rapper</a></header></article>',
    ),
    ARTIST: page_html(
        "New Rapper (@new_rapper)",
        '2500 Followers - Artist on Instagram: "Independent rapper. bookings@example.com"',
        "<header><h2>new_rapper</h2>2500 followers 100 following</header>",
    ),
}


def test_a_scout_run_goes_through_the_cloud_loop_in_a_real_browser(tmp_path, monkeypatch):
    engine = Engine(tmp_path, str(uuid.uuid4()))
    runtime = engine.service.chromium
    real_engine = runtime._engine

    class Headless:
        """The server runs a full Chromium on a virtual screen; here it stays hidden."""

        def __getattr__(self, name):
            return getattr(real_engine(), name)

        def launch(self, **options):
            return real_engine().launch(**{**options, "headless": True, "channel": "chromium"})

    monkeypatch.setattr(runtime, "_engine", lambda: Headless())
    original_open = runtime.open

    def open_with_pages(identifier, proxy_override=None):
        result = original_open(identifier, proxy_override)
        context = runtime.windows[identifier].context

        def serve(route):
            content = PAGES.get(route.request.url)
            if content is None:
                route.abort()
            else:
                route.fulfill(status=200, content_type="text/html", body=content)

        context.route("**/*", serve)
        return result

    monkeypatch.setattr(runtime, "open", open_with_pages)
    try:
        engine.put_session(PROFILE, "Тест", {"cookies": [], "proxy": None})
        engine.apply_settings({"scout_methods": ["posts"]})
        engine.call(
            "settings.save",
            {**engine.call("settings.get", {}), "page_delay_min": 3, "page_delay_max": 3},
        )
        # A bare username, as an older app sent it, and a stray word that is no profile.
        run_id = engine.start(PROFILE, ["music_news", "explore"], 5)
        kinds = []

        def tick(state):
            if state.get("kind") and (not kinds or kinds[-1] != (state["kind"], state.get("url"))):
                kinds.append((state["kind"], state.get("url")))
            if state.get("status") == "paused":
                raise AssertionError(f"queue paused: {state.get('error')}")
            return None

        final = Driver(engine.call, load_scripts(ROOT / "src-tauri" / "src")).run(run_id, tick)
        assert final["status"] == "completed", final
        assert [kind for kind, _ in kinds if kind != "done"] == [
            "source",
            "post",
            "post",
            "profile",
        ]
        [lead] = engine.new_leads(run_id)
        assert lead["username"] == "new_rapper" and lead["category"] == "ARTIST"
        assert lead["profile"]["emails"] == ["bookings@example.com"]
        assert lead["profile"]["followers"] == 2500
    finally:
        engine.close()
