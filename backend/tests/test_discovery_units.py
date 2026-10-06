"""Discovery helpers and providers without a browser."""

import pytest

from artist_lead_finder.database import open_database
from artist_lead_finder.lead_scout.candidates import (
    CandidateGate,
    normalize_instagram_username,
    parse_instagram_post_url,
)
from artist_lead_finder.lead_scout.discovery import (
    DiscoveryContext,
    PostDiscoveryProvider,
    TaggedDiscoveryProvider,
    initial_tasks,
)
from artist_lead_finder.lead_scout.errors import (
    InstagramAuthRequiredError,
    InstagramCheckpointError,
    InstagramRateLimitError,
    InstagramTransientError,
    InstagramUnavailableError,
    error_for,
)
from artist_lead_finder.lead_scout.settings import ScoutSettings
from artist_lead_finder.models import ScoutProcessedPost
from artist_lead_finder.service import ApplicationService

SOURCE = "https://www.instagram.com/rapdaily/"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("@Artist.Name", "artist.name"),
        ("  artist_1 ", "artist_1"),
        ("https://www.instagram.com/New_Rapper/", "new_rapper"),
        ("https://instagram.com/new_rapper?hl=en", "new_rapper"),
        ("instagram.com/new_rapper", "new_rapper"),
        ("https://www.instagram.com/p/ABC/", None),
        ("https://www.instagram.com/stories/artist/123/", None),
        ("https://evil.test/new_rapper/", None),
        ("@explore", None),
        ("reels", None),
        ("instagram", None),
        ("", None),
        (None, None),
        ("bad name", None),
        ("a" * 31, None),
        ("double..dot", None),
    ],
)
def test_normalize_instagram_username(value, expected):
    assert normalize_instagram_username(value) == expected


@pytest.mark.parametrize(
    "url, kind, code, canonical",
    [
        (
            "https://www.instagram.com/p/ABC123/",
            "post",
            "ABC123",
            "https://www.instagram.com/p/ABC123/",
        ),
        (
            "https://www.instagram.com/reel/ABC123/",
            "reel",
            "ABC123",
            "https://www.instagram.com/reel/ABC123/",
        ),
        (
            "https://www.instagram.com/reels/ABC123/",
            "reel",
            "ABC123",
            "https://www.instagram.com/reel/ABC123/",
        ),
        (
            "https://instagram.com/tv/X_1/?igsh=1",
            "reel",
            "X_1",
            "https://www.instagram.com/tv/X_1/",
        ),
        (
            "https://www.instagram.com/Artist/p/ABC/",
            "post",
            "ABC",
            "https://www.instagram.com/artist/p/ABC/",
        ),
    ],
)
def test_parse_instagram_post_url(url, kind, code, canonical):
    parsed = parse_instagram_post_url(url)
    assert (parsed.type, parsed.shortcode, parsed.canonical_url) == (kind, code, canonical)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/artist/",
        "https://evil.test/p/ABC/",
        "http://www.instagram.com/p/ABC/",
        "https://user:pw@www.instagram.com/p/ABC/",
        "https://www.instagram.com/p/ABC/c/123/",
    ],
)
def test_post_url_detection_rejects_non_publications(url):
    assert parse_instagram_post_url(url) is None


def test_candidate_gate_filters_source_ignore_system_and_duplicates():
    gate = CandidateGate("@RapDaily", ignore={"@blocked_one"}, seen={"queued_before"})
    admitted = [
        gate.admit(value)
        for value in [
            "rapdaily",
            "https://www.instagram.com/RapDaily/",
            "blocked_one",
            "instagram",
            "explore",
            "artist",
            "@Artist",
            "queued_before",
            "second",
        ]
    ]
    assert [name for name in admitted if name] == ["artist", "second"]
    assert gate.duplicates == 2  # @Artist again and a name queued earlier in the run
    assert gate.valid("artist") == "artist" and gate.valid("rapdaily") is None


def test_typed_errors_map_stop_reasons():
    assert error_for("login") is InstagramAuthRequiredError
    assert error_for("blocked") is InstagramAuthRequiredError
    assert error_for("checkpoint") is InstagramCheckpointError
    assert error_for("rate_limited") is InstagramRateLimitError
    assert error_for("unavailable") is InstagramUnavailableError
    assert error_for("loading") is InstagramTransientError and InstagramTransientError.retryable
    assert not InstagramRateLimitError.retryable


def context(**settings):
    return DiscoveryContext(
        settings=ScoutSettings(**settings),
        guest=False,
        gate=CandidateGate("rapdaily"),
        skip=lambda source, kind: ["DONE1"],
    )


def test_posts_provider_yields_author_and_collaborators_never_the_source():
    provider, ctx = PostDiscoveryProvider(), context(scout_methods=["posts"])
    [grid] = provider.start(SOURCE, ctx)
    assert grid["kind"] == "source" and grid["args"]["skip"] == ["DONE1"]
    grid_result = provider.handle(
        grid,
        {
            "posts": ["https://www.instagram.com/p/DONE1/", "https://www.instagram.com/reel/NEW1/"],
            "seen": 2,
        },
        ctx,
    )
    assert [item["url"] for item in grid_result.backlog] == ["https://www.instagram.com/reel/NEW1/"]
    assert grid_result.metrics["alreadyProcessed"] == 1
    step = {"kind": "post", "url": "https://www.instagram.com/reel/NEW1/", "source": SOURCE}
    result = provider.handle(
        step,
        {"author": "rapdaily", "collaborators": ["rapdaily", "artist_a", "Artist_A", "artist_b"]},
        ctx,
    )
    names = [candidate.username for candidate, _ in result.candidates]
    assert names == ["artist_a", "artist_b"]
    assert {candidate.method for candidate, _ in result.candidates} == {"reel"}
    assert result.posts == [("NEW1", "post", "processed", None)]
    assert "Candidates emitted: @artist_a, @artist_b" in result.log


def test_unknown_author_is_a_failure_not_a_guess():
    provider, ctx = PostDiscoveryProvider(), context(scout_methods=["posts"])
    step = {"kind": "post", "url": "https://www.instagram.com/p/X1/", "source": SOURCE}
    result = provider.handle(step, {"author": None, "parse_error": "author_not_found"}, ctx)
    assert result.candidates == [] and result.metrics["failures"] == 1
    assert result.posts == [("X1", "post", "failed", "author_not_found")]


def test_tagged_provider_yields_the_original_author():
    provider, ctx = TaggedDiscoveryProvider(), context(scout_methods=["tagged"])
    [grid] = provider.start(SOURCE, ctx)
    assert grid["url"] == SOURCE + "tagged/"
    step = {"kind": "tagged_post", "url": "https://www.instagram.com/p/T1/", "source": SOURCE}
    result = provider.handle(step, {"author": "newrapper", "collaborators": ["newrapper"]}, ctx)
    assert [(c.username, c.method) for c, _ in result.candidates] == [("newrapper", "tagged")]
    assert result.posts == [("T1", "tagged_post", "processed", None)]


def test_settings_saved_with_comments_and_stories_keep_the_other_methods():
    settings = ScoutSettings.model_validate(
        {"scout_methods": ["posts", "comments", "tagged", "stories"]}
    )
    assert settings.scout_methods == ["posts", "tagged"]
    # Only removed methods: the defaults.
    assert ScoutSettings.model_validate({"scout_methods": ["stories"]}).scout_methods == [
        "posts",
        "tagged",
    ]
    ctx = DiscoveryContext(settings, False, CandidateGate("rapdaily"), lambda s, k: [])
    tasks, notices = initial_tasks(SOURCE, ctx)
    assert [t["kind"] for t in tasks] == ["source", "tagged_grid"] and notices == []


@pytest.fixture
def service(tmp_path):
    engine, sessions = open_database(tmp_path / "units.db")
    app = ApplicationService(sessions, tmp_path)
    app.call("settings.save", {"scout_methods": ["posts"], "scout_max_retries": 2})
    yield app, sessions
    app.shutdown()
    engine.dispose()


def test_transient_errors_retry_then_skip_and_login_pauses(service):
    app, sessions = service
    job = app.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]
    app.call(
        "scout.commit_internal",
        {
            "id": job,
            "snapshot": {
                "url": SOURCE,
                "ready": True,
                "seen": 1,
                "posts": ["https://www.instagram.com/p/X1/"],
            },
        },
    )
    assert app.call("capture.state", {"id": job})["kind"] == "post"
    for expected in (1, 2):
        app.call("capture.error_internal", {"id": job, "reason": "loading"})
        state = app.call("capture.state", {"id": job})
        assert (state["status"], state["attempt"]) == ("running", expected)
    # Retries exhausted: the publication is recorded as failed and the run moves on.
    app.call("capture.error_internal", {"id": job, "reason": "loading"})
    state = app.call("capture.state", {"id": job})
    assert state["status"] == "completed"
    with sessions() as session:
        row = session.get(ScoutProcessedPost, "X1")
        assert (row.status, row.last_error) == ("failed", "loading")
    job = app.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]
    app.call("capture.error_internal", {"id": job, "reason": "checkpoint"})
    state = app.call("capture.state", {"id": job})
    assert state["status"] == "paused" and "checkpoint" in state["error"]
    error = [e for e in app.call("scout.events", {"job_id": job}) if e["type"] == "scout:error"][-1]
    assert error["payload"]["error"] == "InstagramCheckpointError"
