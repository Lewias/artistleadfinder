from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from artist_lead_finder.database import open_database
from artist_lead_finder.models import Lead, ScoutPost, ScoutRun
from artist_lead_finder.providers import Candidate
from artist_lead_finder.scouting import assess, post_url
from artist_lead_finder.service import ApplicationService

SOURCE = "https://www.instagram.com/music_news/"
POST = "https://www.instagram.com/p/one/"
SECOND_POST = "https://www.instagram.com/p/two/"
ARTIST = "https://www.instagram.com/new_rapper/"
NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def observation(caption, date="2026-09-21T00:00:00Z"):
    return dict(source=SOURCE, url=POST, caption=caption, published_at=date)


def profile(username="new_rapper", bio="Independent rapper. bookings@example.com"):
    return dict(
        url=f"https://www.instagram.com/{username}/",
        ready=True,
        title=f"Artist (@{username})",
        header="2500 followers 100 following",
        description=f'Artist on Instagram: "{bio}"',
    )


def test_attribution_freshness_negation_and_service_separation():
    candidate = Candidate(platform="instagram", username="new_rapper", bio="Independent rapper")
    result = assess(
        candidate,
        [observation("Rapper @new_rapper needs beats. New album by @other out now.")],
        NOW,
    )
    assert result["services"]["beats"]["score"] == 90
    assert result["services"]["promotion"]["score"] == 0
    assert result["services"]["mixing"]["score"] == 0
    for date in [None, "bad date", "2020-01-01T00:00:00Z", "2030-01-01T00:00:00Z"]:
        result = assess(candidate, [observation("Rapper @new_rapper needs beats", date)], NOW)
        assert result["services"]["beats"]["score"] == 40
    result = assess(candidate, [observation("Rapper @new_rapper does not need beats")], NOW)
    assert result["services"]["beats"]["score"] == 25
    result = assess(
        candidate, [observation("@new_rapper and @other need mixing for new album")], NOW
    )
    assert result["services"]["mixing"]["score"] == 0
    result = assess(candidate, [observation("@new_rapper готовит демо и ищет сведение")], NOW)
    assert result["services"]["mixing"]["score"] == 90
    candidate.bio = "Rapper fan page magazine"
    assert not assess(candidate, [], NOW)["eligible"]


def test_validate_publication_urls():
    assert post_url("https://instagram.com/music_news/p/one/?img_index=1") == (
        "https://www.instagram.com/music_news/p/one/"
    )
    assert post_url("https://instagram.com/music_news/reel/one/") == (
        "https://www.instagram.com/music_news/reel/one/"
    )
    for url in [
        "https://evil.test/p/one/",
        "https://instagram.com/accounts/login/",
        "https://a:b@instagram.com/p/one/",
        "https://instagram.com/p/one/extra",
        "https://instagram.com/music_news/p/one/c/123/",
    ]:
        with pytest.raises(ValueError):
            post_url(url)


def test_source_to_posts_to_leads_incremental_restart_and_crm(tmp_path):
    engine, sessions = open_database(tmp_path / "scout.db")
    service = ApplicationService(sessions, tmp_path)
    service.call("settings.save", {"scout_methods": ["posts", "comments"]})
    params = {"sources": [SOURCE], "profile_id": "0" * 32}
    job = service.call("scout.start_internal", params)["id"]

    def commit(snapshot):
        return service.call("scout.commit_internal", {"id": job, "snapshot": snapshot})

    commit(dict(url=SOURCE, ready=True, posts=[POST, POST, "https://evil.test/p/no/"]))
    assert service.call("capture.state", {"id": job})["kind"] == "post"
    caption = "Rapper @new_rapper has a new single out now. Photo by @camera\nNew album @not_artist"
    commit(
        dict(
            url=POST,
            ready=True,
            author="music_news",
            caption=caption,
            comments=[
                dict(profile_url=ARTIST, text="New single out now"),
                dict(profile_url="https://www.instagram.com/not_artist/", text="Nice"),
            ],
            published_at="2026-09-21T00:00:00Z",
        )
    )
    assert service.call("capture.state", {"id": job})["kind"] == "profile"
    result = commit(profile())
    commit(profile("not_artist", "Music magazine and news"))
    assert service.call("capture.state", {"id": job})["status"] == "completed"
    leads = service.call("scout.results", {})
    assert len(leads) == 1 and leads[0]["username"] == "new_rapper"
    assert leads[0]["contacts"] == ["bookings@example.com"]
    assert leads[0]["evidence"][0]["url"] == POST
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        assert session.scalar(select(func.count()).select_from(ScoutPost)) == 1
    service.call("leads.status", {"id": result["lead_id"], "status": "contacted"})
    assert service.call("scout.results", {}) == []
    # A processed publication is not read again; a new one is.
    job = service.call("scout.start_internal", params)["id"]
    commit(dict(url=SOURCE, ready=True, posts=[POST]))
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed"
    assert any("уже разобраны" in notice for notice in state["notices"])
    job = service.call("scout.start_internal", params)["id"]
    commit(dict(url=SOURCE, ready=True, posts=[POST, SECOND_POST]))
    assert service.call("capture.state", {"id": job})["url"] == SECOND_POST
    commit(
        dict(
            url=SECOND_POST,
            ready=True,
            author="music_news",
            comments=[dict(profile_url=ARTIST, text="New comment")],
        )
    )
    with sessions() as session:
        assert len(session.get(ScoutRun, job).observations[ARTIST]) == 1
    # Already processed: evidence is attached, the profile is not queued again.
    assert service.call("capture.state", {"id": job})["status"] == "completed"
    assert service.call("scout.results", {}) == []
    service.shutdown()
    restarted = ApplicationService(sessions, tmp_path)
    assert restarted.call("capture.state", {"id": job})["status"] == "completed"
    assert restarted.call("scout.sources", {}) == [SOURCE]
    restarted.shutdown()
    engine.dispose()


def test_source_mismatch_pause_skip_and_cancel(tmp_path):
    engine, sessions = open_database(tmp_path / "scout.db")
    service = ApplicationService(sessions, tmp_path)
    service.call("settings.save", {"scout_methods": ["posts", "comments"]})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]
    service.call("jobs.control", {"id": job, "action": "pause"})
    assert not service.call("scout.commit_internal", {"id": job, "snapshot": {}})["saved"]
    service.call("jobs.control", {"id": job, "action": "resume"})
    with pytest.raises(ValueError):
        service.call(
            "scout.commit_internal",
            {"id": job, "snapshot": dict(url=ARTIST, ready=True, posts=[POST])},
        )
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=SOURCE, ready=True, posts=[POST])}
    )
    service.call(
        "scout.commit_internal",
        {
            "id": job,
            "snapshot": dict(
                url=POST, ready=True, author="wrong", caption="New single @new_rapper"
            ),
        },
    )
    # A publication by someone else on the source grid: its author is the candidate.
    state = service.call("capture.state", {"id": job})
    assert state["kind"] == "profile" and state["url"] == "https://www.instagram.com/wrong/"
    service.call("jobs.control", {"id": job, "action": "cancel"})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]
    service.call("capture.error_internal", {"id": job, "reason": "blocked"})
    service.call("scout.skip", {"id": job})
    assert service.call("capture.state", {"id": job})["status"] == "completed"
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]
    service.call("jobs.control", {"id": job, "action": "cancel"})
    assert not service.call("scout.commit_internal", {"id": job, "snapshot": {}})["saved"]
    service.shutdown()
    engine.dispose()


def test_service_scores_age_without_new_browser_visit(tmp_path, monkeypatch):
    import artist_lead_finder.scouting as scouting

    monkeypatch.setattr(scouting, "utcnow", lambda: NOW)
    engine, sessions = open_database(tmp_path / "aging.db")
    service = ApplicationService(sessions, tmp_path)
    service.call("settings.save", {"scout_methods": ["posts", "comments"]})
    job = service.call(
        "scout.start_internal",
        {
            "sources": [SOURCE],
            "profile_id": "0" * 32,
        },
    )["id"]
    for snapshot in [
        dict(url=SOURCE, ready=True, posts=[POST]),
        dict(
            url=POST,
            ready=True,
            author="music_news",
            caption="Rapper @new_rapper needs beats",
            comments=[
                dict(profile_url=ARTIST, text="I need beats", published_at="2026-09-21T00:00:00Z")
            ],
            published_at="2026-09-21T00:00:00Z",
        ),
        profile(),
    ]:
        service.call("scout.commit_internal", {"id": job, "snapshot": snapshot})
    assert service.call("scout.results", {})[0]["services"]["beats"]["score"] == 90
    monkeypatch.setattr(scouting, "utcnow", lambda: datetime(2027, 9, 22, tzinfo=timezone.utc))
    assert service.call("scout.results", {})[0]["services"]["beats"]["score"] == 40
    service.shutdown()
    engine.dispose()


def test_only_comment_authors_are_checked_and_private_profiles_rejected(tmp_path):
    engine, sessions = open_database(tmp_path / "comments.db")
    service = ApplicationService(sessions, tmp_path)
    service.call("settings.save", {"scout_methods": ["posts", "comments"]})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]

    def commit(snapshot):
        return service.call("scout.commit_internal", {"id": job, "snapshot": snapshot})

    commit(dict(url=SOURCE, ready=True, posts=[POST]))
    commit(
        dict(
            url=POST,
            ready=True,
            author="music_news",
            caption="Rapper @caption_only new album",
            comments=[
                dict(profile_url=ARTIST, text="Nice! @tagged_artist"),
                dict(profile_url=ARTIST, text="Another comment"),
                dict(profile_url=SOURCE, text="Thanks"),
                dict(profile_url="https://evil.test/artist/", text="spam"),
            ],
        )
    )
    state = service.call("capture.state", {"id": job})
    assert state["url"] == ARTIST and state["candidates"] == 1
    with sessions() as session:
        assert len(session.get(ScoutRun, job).observations[ARTIST]) == 2
    commit({**profile(), "private": True})
    assert service.call("capture.state", {"id": job})["status"] == "completed"
    assert service.call("scout.results", {}) == []
    service.shutdown()
    engine.dispose()


def test_empty_comments_do_not_fall_back_to_caption(tmp_path):
    engine, sessions = open_database(tmp_path / "empty.db")
    service = ApplicationService(sessions, tmp_path)
    service.call("settings.save", {"scout_methods": ["posts", "comments"]})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]
    for snapshot in [
        dict(url=SOURCE, ready=True, posts=[POST]),
        dict(url=POST, ready=True, author="music_news", caption="Rapper @new_rapper", comments=[]),
    ]:
        service.call("scout.commit_internal", {"id": job, "snapshot": snapshot})
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed" and state["candidates"] == 0
    assert state["notices"]
    service.shutdown()
    engine.dispose()


def test_thirty_new_candidates_per_post_no_run_cap_and_no_revisits(tmp_path):
    engine, sessions = open_database(tmp_path / "quota.db")
    service = ApplicationService(sessions, tmp_path)
    service.call("settings.save", {"scout_methods": ["posts", "comments"]})
    params = {"sources": [SOURCE], "profile_id": "0" * 32}
    posts = [f"https://www.instagram.com/p/post{n}/" for n in range(5)]

    def commenters(post, count):
        return [
            dict(profile_url=f"https://www.instagram.com/fan_{post}_{i}/", text="fire")
            for i in range(count)
        ]

    job = service.call("scout.start_internal", params)["id"]

    def commit(snapshot):
        return service.call("scout.commit_internal", {"id": job, "snapshot": snapshot})

    commit(dict(url=SOURCE, ready=True, posts=posts))
    # Post 0: the artist, 40 other unique commenters and a repeated comment from the artist.
    commit(
        dict(
            url=posts[0],
            ready=True,
            author="music_news",
            comments=[
                dict(profile_url=ARTIST, text="I need beats"),
                *commenters(0, 40),
                dict(profile_url=ARTIST, text="Still need beats"),
            ],
        )
    )
    # Posts 1-4 repeat the artist; duplicates must not use the 30-candidate quota.
    for n in range(1, 5):
        commit(
            dict(
                url=posts[n],
                ready=True,
                author="music_news",
                comments=[dict(profile_url=ARTIST, text=f"beats {n}"), *commenters(n, 35)],
            )
        )
    state = service.call("capture.state", {"id": job})
    assert state["candidates"] == 150  # 5 posts x 30 new; the old 100-per-run cap is gone
    with sessions() as session:
        run = session.get(ScoutRun, job)
        profiles = [t["url"] for t in run.tasks if t["kind"] == "profile"]
    assert len(profiles) == len(set(profiles)) == 150
    assert ARTIST in profiles
    assert any("новых кандидатов 30, повторов 1; достигнут лимит 30" in n for n in state["notices"])

    while (state := service.call("capture.state", {"id": job}))["status"] == "running":
        url = state["url"]
        commit(profile() if url == ARTIST else profile(url.split("/")[-2], "Just a fan"))
    [lead] = service.call("scout.results", {})
    assert lead["username"] == "new_rapper"
    evidence_before = len(lead["evidence"])

    # A later run treats the already checked artist as a duplicate and only adds evidence.
    job = service.call("scout.start_internal", params)["id"]
    fresh_post = "https://www.instagram.com/p/fresh/"
    commit(dict(url=SOURCE, ready=True, posts=[posts[0], fresh_post]))
    commit(
        dict(
            url=fresh_post,
            ready=True,
            author="music_news",
            comments=[dict(profile_url=ARTIST, text="Need a mix for my new track")],
        )
    )
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed"  # no profile revisit was queued
    assert any("новых кандидатов 0, повторов 1" in n for n in state["notices"])
    [lead] = service.call("scout.results", {})
    assert len(lead["evidence"]) == min(evidence_before + 1, 5)
    assert lead["evidence"][-1]["caption"] == "Need a mix for my new track"
    service.shutdown()
    engine.dispose()
