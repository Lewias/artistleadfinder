"""Exercise Instagram DOM extraction through a full scout job without network access.

An optional desktop executable must contain exactly the tested extraction scripts.
This catches stale portable builds even when the source checkout passes its tests.
"""

import html
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from artist_lead_finder.database import open_database  # noqa: E402
from artist_lead_finder.service import ApplicationService  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

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


def main():
    scripts = {
        name: (ROOT / "src-tauri" / "src" / f"{name}.js").read_text(encoding="utf-8")
        for name in ("scout", "capture", "grid", "story", "follow")
    }
    if len(sys.argv) > 1:
        executable = Path(sys.argv[1]).read_bytes()
        for name in scripts:
            # include_str! preserves source line endings in the executable.
            raw = (ROOT / "src-tauri" / "src" / f"{name}.js").read_bytes()
            if raw not in executable:
                raise RuntimeError(f"Stale desktop executable: {name}.js does not match source")

    pages = {
        SOURCE: page_html(
            "Music News (@music_news)", "",
            f'<h2>music_news</h2><a href="{POST}">Post</a><a href="{REEL}">Reel</a>',
        ),
        POST: page_html(
            "Instagram", '100 likes - music_news September 25, 2026: '
            '"Rapper @caption_only has a new single". ',
            '<article><div><a href="/new_rapper/">new_rapper</a><a href="/p/first/c/123/"><time datetime="2026-09-25T00:00:00Z">1d</time></a><span>Great post @unrelated</span></div></article>', 
        ),
        REEL: page_html(
            "Instagram", 'music_news September 25, 2026: '
            '"Rapper @caption_only needs beats". ',
            '<article><div><a href="/new_rapper/">new_rapper</a><a href="/reel/second/c/456/"><time datetime="2026-09-25T00:00:00Z">1d</time></a><span>I need beats</span></div></article>', 
        ),
        ARTIST: page_html(
            "New Rapper (@new_rapper)",
            '2500 Followers - Artist on Instagram: "Independent rapper. bookings@example.com"',
            "<header><h2>new_rapper</h2>2500 followers 100 following</header>",
        ),
    }
    with tempfile.TemporaryDirectory(prefix="scout-smoke-") as temporary:
        folder = Path(temporary)
        engine, sessions = open_database(folder / "test.sqlite3")
        service = ApplicationService(sessions, folder)
        # The fixture pages cover the posts and comments methods.
        service.call("settings.save", {"scout_methods": ["posts", "comments"]})
        try:
            job = service.call("scout.start_internal", {
                "sources": [SOURCE], "profile_id": "0" * 32,
            })["id"]
            kinds = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, channel="chromium")
                try:
                    context = browser.new_context(service_workers="block")

                    def serve(route):
                        content = pages.get(route.request.url)
                        if content is None:
                            route.abort()
                        else:
                            route.fulfill(status=200, content_type="text/html", body=content)

                    context.route("**/*", serve)
                    page = context.new_page()
                    for _ in range(5):
                        state = service.call("capture.state", {"id": job})
                        if state["status"] == "completed":
                            break
                        kinds.append(state["kind"])
                        page.goto(state["url"], wait_until="load")
                        # Same script choice as page_script() in src-tauri/src/main.rs.
                        name = {"profile": "capture", "source": "grid", "tagged_grid": "grid",
                                "stories": "story", "followers": "follow",
                                "following": "follow"}.get(state["kind"], "scout")
                        args = state.get("args")
                        snapshot = (page.evaluate(scripts[name], args) if args is not None
                                    else page.evaluate(scripts[name]))
                        assert snapshot.get("ready"), f"Loaded {state['kind']} not recognized"
                        service.call("scout.commit_internal", {"id": job, "snapshot": snapshot})
                finally:
                    browser.close()
            assert kinds == ["source", "post", "post", "profile"], kinds
            state = service.call("capture.state", {"id": job})
            assert state["status"] == "completed", state
            # The artist comments under both publications; the reel sees a duplicate.
            assert state["notices"] == [f"{REEL}: новых кандидатов 0, повторов 1"], state[
                "notices"
            ]
            results = service.call("scout.results", {})
            assert len(results) == 1, results
            assert results[0]["username"] == "new_rapper"
            assert len(results[0]["evidence"]) == 2
            assert all(e["kind"] == "comment" for e in results[0]["evidence"])
            assert results[0]["contacts"] == ["bookings@example.com"]
            print("Scout smoke OK: source -> post + reel -> artist -> saved lead")
        finally:
            service.shutdown()
            engine.dispose()


if __name__ == "__main__":
    main()
