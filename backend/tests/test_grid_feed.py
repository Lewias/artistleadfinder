"""Publication facts from the data an Instagram grid page receives (no network)."""

import json
import os

import pytest

from artist_lead_finder.lead_scout import feed
from artist_lead_finder.lead_scout.candidates import CandidateGate
from artist_lead_finder.lead_scout.discovery import (
    DiscoveryContext,
    PostDiscoveryProvider,
    TaggedDiscoveryProvider,
)
from artist_lead_finder.lead_scout.settings import ScoutSettings

SOURCE = "https://www.instagram.com/rapdaily/"


def node(code, owner, coauthors=(), **extra):
    return {
        "code": code,
        "pk": "1",
        "media_type": 1,
        "taken_at": 1760000000,
        "owner": {"username": owner, "id": "9"},
        "coauthor_producers": [{"username": name} for name in coauthors],
        **extra,
    }


# ---------- reading media objects ----------


def test_timeline_connection_nodes_give_author_and_collaborators():
    data = {
        "data": {
            "xdt_api__v1__feed__user_timeline_graphql_connection": {
                "edges": [
                    {"node": node("P1", "rapdaily", ["Guest.Artist"])},
                    {"node": node("P2", "rapdaily")},
                ]
            }
        }
    }
    found = feed.collect(data)
    assert found["P1"].author == "rapdaily" and found["P1"].collaborators == ["guest.artist"]
    assert found["P2"].author == "rapdaily" and found["P2"].collaborators == []


def test_other_shapes_and_noise():
    texts = [
        # API answers may carry a JSON-hijacking guard.
        'for (;;);{"items": [{"code": "T1", "media_type": 2, "user": {"username": "Tagger"}}]}',
        # Older GraphQL: shortcode and typename.
        json.dumps(
            {
                "shortcode": "OLD1",
                "__typename": "GraphImage",
                "is_video": False,
                "owner": {"username": "oldschool"},
            }
        ),
        # Not media: a code without media keys, an owner without a username, broken JSON.
        '{"code": "NOPE1", "owner": {"username": "x"}}',
        '{"code": "ANON1", "media_type": 1, "owner": {"id": "5"}}',
        "{broken",
        "<html>",
    ]
    found = feed.collect_texts(texts)
    assert set(found) == {"T1", "OLD1"}
    assert found["T1"].author == "tagger" and found["OLD1"].author == "oldschool"


def test_the_same_post_twice_is_merged():
    found = feed.collect(
        [node("P1", "a", ["b"]), {"x": node("P1", "a", ["c"])}, node("P1", "a", ["b"])]
    )
    assert found["P1"].collaborators == ["b", "c"]


def test_snapshot_feed_is_checked_again():
    snapshot = {
        "feed": {
            "P1": {"author": "Artist", "collaborators": ["artist", "Friend"]},
            "bad code!": {"author": "x"},
            "P2": {"author": "not a name"},
            "P3": "nope",
            "P4": {"author": "ok_name"},
        }
    }
    found = feed.from_snapshot(snapshot)
    assert set(found) == {"P1", "P4"}
    assert found["P1"].collaborators == ["friend"] and found["P4"].author == "ok_name"
    assert feed.from_snapshot({"feed": []}) == {}


# ---------- discovery from the grid ----------


def context(**settings):
    return DiscoveryContext(
        settings=ScoutSettings(**settings),
        guest=False,
        gate=CandidateGate("rapdaily"),
        skip=lambda source, kind: [],
    )


def grid_snapshot(codes, facts):
    return {
        "ready": True,
        "posts": [f"https://www.instagram.com/p/{code}/" for code in codes],
        "seen": len(codes),
        "feed": facts,
    }


def test_posts_from_grid_data_are_not_opened():
    provider, ctx = PostDiscoveryProvider(), context(scout_methods=["posts"])
    [step] = provider.start(SOURCE, ctx)
    snapshot = grid_snapshot(
        ["P1", "P2", "P3"],
        {
            "P1": {"author": "rapdaily", "collaborators": ["newartist"]},
            "P2": {"author": "otherartist", "collaborators": []},
        },
    )
    result = provider.handle(step, snapshot, ctx)
    # P3 had no data: it is opened as before.
    assert [item["code"] for item in result.backlog] == ["P3"]
    assert [(c.username, c.evidence_type) for c, _ in result.candidates] == [
        ("newartist", "collaborator"),
        ("otherartist", "post_author"),
    ]
    assert result.posts == [("P1", "post", "processed", None), ("P2", "post", "processed", None)]
    assert result.metrics["itemsProcessed"] == 2
    assert any("without opening: 2; to open: 1" in line for line in result.log)


def test_tagged_posts_from_grid_data():
    provider, ctx = TaggedDiscoveryProvider(), context(scout_methods=["tagged"])
    [step] = provider.start(SOURCE, ctx)
    snapshot = grid_snapshot(["T1", "T2"], {"T1": {"author": "tagger", "collaborators": ["duo"]}})
    result = provider.handle(step, snapshot, ctx)
    assert [item["code"] for item in result.backlog] == ["T2"]
    assert [(c.username, c.method, c.evidence_type) for c, _ in result.candidates] == [
        ("tagger", "tagged", "tagged_post_author"),
        ("duo", "tagged", "tagged_collaborator"),
    ]
    assert result.posts == [("T1", "tagged_post", "processed", None)]


# ---------- the browser side, on a local page ----------


def test_window_reads_preloaded_and_fetched_data():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    sync_api = pytest.importorskip("playwright.sync_api")
    from artist_lead_finder.chromium_runtime import Window, grid_facts, watch_data

    preload = {"require": [{"edges": [{"node": node("PRE1", "rapdaily", ["early_guest"])}]}]}
    fetched = {"data": {"edges": [{"node": node("NET1", "netartist")}]}}
    html = (
        "<html><body><main></main>"
        f'<script type="application/json">{json.dumps(preload)}</script>'
        "<script>fetch('/graphql/query', {method: 'POST'})"
        ".then(r => r.text()).then(() => document.body.dataset.done = '1')</script>"
        "</body></html>"
    )

    def answer(route):
        url = route.request.url
        if url.startswith("https://www.instagram.com/graphql/query"):
            route.fulfill(status=200, content_type="text/javascript", body=json.dumps(fetched))
        elif url.split("?")[0] == "https://www.instagram.com/rapdaily/":
            route.fulfill(status=200, content_type="text/html", body=html)
        else:
            route.abort()

    try:
        manager = sync_api.sync_playwright().start()
        browser = manager.chromium.launch(headless=True, channel="chromium")
    except Exception as error:  # pragma: no cover - depends on the local Chromium install
        pytest.skip(f"Bundled Chromium unavailable: {error}")
    try:
        context_ = browser.new_context()
        context_.route("**/*", answer)
        page = context_.new_page()
        window = Window(browser, context_, page)
        watch_data(window)
        page.goto("https://www.instagram.com/rapdaily/")
        page.wait_for_function("document.body.dataset.done === '1'")
        posts = [
            "https://www.instagram.com/p/PRE1/",
            "https://www.instagram.com/p/NET1/",
            "https://www.instagram.com/p/MISSING1/",
        ]
        facts = grid_facts(window, posts)
        assert facts == {
            "PRE1": {"author": "rapdaily", "collaborators": ["early_guest"]},
            "NET1": {"author": "netartist", "collaborators": []},
        }
    finally:
        browser.close()
        manager.stop()
