import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from artist_lead_finder.chromium_runtime import GUEST_ID
from artist_lead_finder.database import open_database
from artist_lead_finder.lead_scout import memory
from artist_lead_finder.lead_scout.candidates import ScoutCandidate, clean_username
from artist_lead_finder.lead_scout.classification import AIClassificationResult, AITransientError
from artist_lead_finder.lead_scout.contacts import extract_emails, extract_phones
from artist_lead_finder.lead_scout.discovery import post_url
from artist_lead_finder.models import (
    Lead,
    LeadScoutProfile,
    ScoutProcessedPost,
    ScoutProcessedProfile,
    ScoutProcessedStory,
    ScoutSource,
    utcnow,
)
from artist_lead_finder.service import ApplicationService

SOURCE = "https://www.instagram.com/rapdaily/"
ACCOUNT = "a" * 32


def test_contacts_are_normalised_and_deduplicated():
    text = "Booking: Mgmt@Example.com, mgmt@example.com. Call +1 (404) 555-0199 · 12.5k followers"
    assert extract_emails(text) == ["mgmt@example.com"]
    assert extract_phones(text, "WhatsApp: +14045550199") == ["+14045550199"]
    assert extract_phones("2500 followers 100 following 2024") == []


def test_candidates_reject_system_links():
    assert clean_username("https://www.instagram.com/New.Rapper/") == "new.rapper"
    assert clean_username("@explore") is None
    assert clean_username("https://www.instagram.com/p/abc/") is None
    assert clean_username("https://evil.test/artist/") is None
    with pytest.raises(ValueError):
        ScoutCandidate("a", "b", "dm")
    assert post_url("https://www.instagram.com/tv/Xy1/") == "https://www.instagram.com/tv/Xy1/"
    assert post_url("https://instagram.com/reels/Ab2/") == "https://www.instagram.com/reel/Ab2/"


def test_source_rotation_and_cooldown(tmp_path):
    engine, sessions = open_database(tmp_path / "rotation.db")
    with sessions.begin() as session:
        for index, name in enumerate("abcdef"):
            session.add(
                ScoutSource(
                    url=f"https://www.instagram.com/{name}/",
                    added_at=utcnow() + timedelta(seconds=index),
                )
            )
    names = []
    for _ in range(4):
        with sessions.begin() as session:
            picked, _ = memory.pick_sources(session, 2, 24, skip_recent=True)
        names.append("".join(url.split("/")[-2] for url in picked))
    assert names == ["ab", "cd", "ef", "ab"]
    with sessions.begin() as session:
        session.get(ScoutSource, "https://www.instagram.com/c/").last_scanned_at = utcnow()
        memory.state_set(session, "source_cursor", 2)
    with sessions.begin() as session:
        picked, cooling = memory.pick_sources(session, 2, 24, skip_recent=True)
    assert [url.split("/")[-2] for url in picked] == ["d", "e"]
    assert cooling == ["https://www.instagram.com/c/"]
    # Rotation off: every run starts from the top and the cursor stays where it was.
    with sessions.begin() as session:
        picked, _ = memory.pick_sources(session, 2, 24, skip_recent=False, rotate=False)
        assert [url.split("/")[-2] for url in picked] == ["a", "b"]
        assert memory.state_get(session, "source_cursor") == 5
    engine.dispose()


def test_settings_of_an_older_version_still_save(scout):
    from artist_lead_finder.models import Setting

    service, sessions = scout
    with sessions.begin() as session:
        session.add(Setting(key="scout_follow_max", value=250))
        session.add(Setting(key="scout_following_max", value=40))
        session.add(Setting(key="removed_long_ago", value=1))
    loaded = service.call("settings.get", {})
    assert (loaded["scout_followers_max"], loaded["scout_following_max"]) == (250, 40)
    assert "scout_follow_max" not in loaded and "removed_long_ago" not in loaded
    # The UI sends back everything it got.
    assert service.call("settings.save", loaded)["scout_followers_max"] == 250


def profile_page(name, bio, followers=2500, category="", links=(), user_id=None, captions=()):
    header = (
        f"{name}\n{category}\n{followers} followers 100 following 40 posts\n{bio}"
        if category
        else (f"{name}\n{followers} followers 100 following 40 posts\n{bio}")
    )
    return dict(
        url=f"https://www.instagram.com/{name}/",
        ready=True,
        title=f"{name} (@{name})",
        header=header,
        description=f'{followers} Followers - Artist on Instagram: "{bio}"',
        links=list(links),
        external_url=links[0] if links else "",
        captions=list(captions),
        # Distinct Instagram ids: a shared id means one account and merges leads.
        user_id=user_id or str(1000 + sum(map(ord, name))),
    )


@pytest.fixture
def scout(tmp_path):
    engine, sessions = open_database(tmp_path / "scout.db")
    service = ApplicationService(sessions, tmp_path)
    service.scout.ai = None
    yield service, sessions
    service.shutdown()
    engine.dispose()


def run_pages(service, settings, pages, sources=(SOURCE,)):
    # Profile pages are committed as page snapshots unless a test turns the API step on.
    service.call("settings.save", {"profiles_per_hour": 0, "scout_profile_api": False, **settings})
    service.call("scout.source_add", {"values": list(sources)})
    job = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    visited = []
    while (state := service.call("capture.state", {"id": job}))["status"] == "running":
        visited.append((state["kind"], state["url"]))
        service.call("scout.commit_internal", {"id": job, "snapshot": pages(state)})
    return job, state, visited


def test_full_flow_posts_tagged_followers_to_leads(scout):
    service, sessions = scout
    post = "https://www.instagram.com/p/C1/"
    collab = "https://www.instagram.com/p/C2/"
    tagged = "https://www.instagram.com/p/T1/"
    profiles = {
        "artist_one": profile_page(
            "artist_one", "Rapper. bookings: one@mail.com", links=["https://open.spotify.com/a"]
        ),
        "producer_two": profile_page(
            "producer_two", "Music producer, beatmaker", links=["https://beatstars.com/p"]
        ),
        "too_big": profile_page("too_big", "Singer", followers=2_000_000),
        "photo_guy": profile_page("photo_guy", "Wedding photographer"),
        "fan_one": profile_page(
            "fan_one", "Rapper and songwriter · new album out now", category="Musician/band"
        ),
        "story_artist": profile_page("story_artist", "Singer · new single out now"),
        "reel_author": profile_page("reel_author", "Rap artist, new album out now"),
    }
    shared_reel = "https://www.instagram.com/reel/SR1/"

    def pages(state):
        kind, url = state["kind"], state["url"]
        if kind == "source":
            return dict(url=url, ready=True, posts=[post, collab])
        if kind == "tagged_grid":
            return dict(url=url, ready=True, posts=[tagged])
        if kind == "followers":
            assert state["args"] == {"pageSize": 12, "delayMs": 2000, "max": 100}
            return dict(url=url, ready=True, users=["rapdaily", "fan_one", "photo_guy"])
        if kind == "post" and url == post:
            return dict(
                url=url,
                ready=True,
                author="rapdaily",
                collaborators=["rapdaily"],
                comments=[dict(profile_url="https://www.instagram.com/too_big/", text="fire")],
            )
        if kind == "post":
            return dict(
                url=url,
                ready=True,
                author="rapdaily",
                collaborators=["rapdaily", "artist_one"],
                comments=[],
            )
        if kind == "tagged_post":
            return dict(url=url, ready=True, author="producer_two", collaborators=[], comments=[])
        if kind == "stories":
            assert state["args"]["source"] == "rapdaily" and state["args"]["maxStories"] == 20
            story = "https://www.instagram.com/stories/rapdaily/3001/"
            return dict(
                url=url,
                ready=True,
                end_reason="end_of_stories",
                stories=[
                    dict(
                        id="3001",
                        url=story,
                        candidates=[
                            dict(
                                username="story_artist",
                                evidenceType="mention_sticker",
                                confidence=1,
                            ),
                            dict(
                                username="maybe_text", evidenceType="text_mention", confidence=0.6
                            ),
                            dict(username="rapdaily", evidenceType="profile_link", confidence=0.9),
                        ],
                        shared_media=[dict(url=shared_reel, kind="reel")],
                    )
                ],
            )
        if kind == "story_media":
            assert state["args"] == {"comments": False, "debug": False}
            return dict(url=url, ready=True, author="reel_author", collaborators=[])
        return profiles[url.rstrip("/").split("/")[-1]]

    job, state, visited = run_pages(
        service,
        {
            "scout_methods": ["posts", "comments", "tagged", "followers", "stories"],
            "scout_profile_type": "artists_producers",
            "scout_max_followers": 1_000_000,
        },
        pages,
    )
    kinds = [kind for kind, _ in visited]
    assert kinds[:4] == ["source", "tagged_grid", "stories", "followers"]
    assert "story_media" in kinds
    assert state["status"] == "completed"
    stats = state["stats"]
    assert (stats["leads"], stats["discovered"]) == (5, 7)
    metrics = stats["providers"]["rapdaily"]
    assert metrics["posts"]["itemsProcessed"] == 2 and metrics["posts"]["candidatesFound"] == 1
    assert metrics["tagged"]["candidatesFound"] == 1
    assert metrics["stories"]["itemsSeen"] == 1 and metrics["stories"]["candidatesFound"] == 2
    with sessions() as session:
        leads = {row.username: row for row in session.scalars(select(Lead))}
        assert set(leads) == {
            "artist_one",
            "producer_two",
            "fan_one",
            "story_artist",
            "reel_author",
        }
        story_lead = session.get(LeadScoutProfile, leads["story_artist"].id)
        assert (story_lead.discovery_method, story_lead.origin_url) == (
            "story",
            "https://www.instagram.com/stories/rapdaily/3001/",
        )
        assert session.get(LeadScoutProfile, leads["reel_author"].id).origin_url == shared_reel
        details = session.get(LeadScoutProfile, leads["artist_one"].id)
        assert details.profile_type == "artist" and details.discovery_method == "post"
        assert details.source_username == "rapdaily" and details.origin_url == collab
        assert details.emails == ["one@mail.com"] and details.instagram_id == str(
            1000 + sum(map(ord, "artist_one"))
        )
        assert details.posts_count == 40
        assert session.get(LeadScoutProfile, leads["producer_two"].id).discovery_method == "tagged"
        assert session.get(LeadScoutProfile, leads["fan_one"].id).discovery_method == "followers"
        skipped = {
            row.username: row.reason
            for row in session.scalars(select(ScoutProcessedProfile))
            if row.result == "skipped"
        }
        assert skipped == {"too_big": "FOLLOWERS_TOO_HIGH", "photo_guy": "WRONG_PROFILE_TYPE"}
        assert session.get(ScoutProcessedPost, "C1").status == "processed"
        # Tagged publications live in their own namespace.
        assert session.get(ScoutProcessedPost, "tagged:rapdaily:T1").kind == "tagged_post"
        assert session.get(ScoutProcessedStory, "story:rapdaily:3001").status == "processed"
        source = session.get(ScoutSource, SOURCE)
        assert (source.status, source.leads_found) == ("done", 5) and source.last_scanned_at
    events = service.call("scout.events", {"job_id": job})
    types = [event["type"] for event in events]
    for expected in (
        "scout:run-started",
        "scout:source-started",
        "scout:candidate-found",
        "scout:profile-resolving",
        "scout:profile-resolved",
        "scout:classification-started",
        "scout:classification-completed",
        "scout:profile-skipped",
        "scout:lead-created",
        "scout:source-completed",
        "scout:completed",
    ):
        assert expected in types
    log = next(
        e["payload"]["log"]
        for e in events
        if e["type"] == "scout:lead-created" and e["payload"]["username"] == "artist_one"
    )
    assert "[Scout][@rapdaily][@artist_one]" in log and "RESULT:\nLEAD CREATED" in log
    assert "Filters:\n" in log and "profile type: pass (artist)" in log
    assert "[Classifier][@artist_one]" in log
    assert "+5 Bio contains strong artist term: rapper" in log
    skip_log = next(
        e["payload"]["log"]
        for e in events
        if e["payload"].get("username") == "too_big" and "log" in e["payload"]
    )
    assert "RESULT:\nSKIPPED\n\nReason:\nFOLLOWERS_TOO_HIGH\n2000000 > max 1000000" in skip_log

    # Next run: the source is in cooldown, so nothing to start.
    with pytest.raises(ValueError, match="кулдауна"):
        service.call("scout.start_internal", {"profile_id": ACCOUNT})


def test_ai_uncertain_mode_and_contacts_filter(scout):
    service, sessions = scout
    calls = []

    class FakeAI:
        def classify(self, profile, local=None, *, model, timeout):
            calls.append(profile.username)
            return AIClassificationResult("artist", 81, model)

    service.scout.ai = FakeAI()
    post = "https://www.instagram.com/p/A1/"
    profiles = {
        # One weak signal: uncertain locally, AI decides.
        "maybe_artist": profile_page("maybe_artist", "dj · contact: maybe@mail.com"),
        "clear_artist": profile_page(
            "clear_artist",
            "Rapper, singer. new single out now · clear@mail.com",
            links=["https://open.spotify.com/c"],
        ),
        "no_contact": profile_page("no_contact", "Rapper and singer, new album out now"),
    }

    def pages(state):
        if state["kind"] == "source":
            return dict(url=state["url"], ready=True, posts=[post])
        if state["kind"] == "post":
            return dict(
                url=post,
                ready=True,
                author="rapdaily",
                comments=[
                    dict(profile_url=f"https://www.instagram.com/{n}/", text="hi") for n in profiles
                ],
            )
        return profiles[state["url"].rstrip("/").split("/")[-1]]

    job, state, _ = run_pages(
        service,
        {
            "scout_methods": ["posts", "comments"],
            "scout_only_contacts": True,
            "scout_ai_mode": "uncertain",
        },
        pages,
    )
    assert calls == [
        "maybe_artist"
    ]  # AI only for the uncertain profile, never for the filtered one
    with sessions() as session:
        leads = {row.username: row for row in session.scalars(select(Lead))}
        assert set(leads) == {"maybe_artist", "clear_artist"}
        ai_row = session.get(LeadScoutProfile, leads["maybe_artist"].id)
        assert (ai_row.ai_model, ai_row.ai_confidence, ai_row.profile_confidence) == (
            "anthropic/claude-haiku-4.5",
            81,
            81,
        )
        assert session.get(ScoutProcessedProfile, "no_contact").reason == "NO_CONTACT"


def test_transient_ai_failure_is_retried_and_the_profile_comes_back(scout):
    service, sessions = scout
    clock = [utcnow()]
    service.scout.classification.pending.now = lambda: clock[0]

    class FlakyAI:
        calls = []

        def classify(self, profile, local=None, *, model, timeout):
            self.calls.append(profile.username)
            if len(self.calls) == 1:
                raise AITransientError("OpenRouter недоступен.")
            return AIClassificationResult("artist", 88, model)

    service.scout.ai = FlakyAI()
    post = "https://www.instagram.com/p/A2/"
    profiles = {
        "maybe_artist": profile_page("maybe_artist", "new music soon"),
        "clear_artist": profile_page(
            "clear_artist", "Rapper. My new single out now", links=["https://open.spotify.com/c"]
        ),
    }

    def pages(state):
        if state["kind"] == "source":
            return dict(url=state["url"], ready=True, posts=[post])
        if state["kind"] == "post":
            comments = [
                dict(profile_url=f"https://www.instagram.com/{n}/", text="hi") for n in profiles
            ]
            return dict(url=post, ready=True, author="rapdaily", comments=comments)
        name = state["url"].rstrip("/").split("/")[-1]
        if FlakyAI.calls:
            clock[0] += timedelta(minutes=10)  # the pending retry is due at the next step
        return profiles[name]

    job, state, visited = run_pages(
        service, {"scout_methods": ["posts", "comments"], "scout_ai_mode": "uncertain"}, pages
    )
    # AI down: local result stands (skipped), the retry answers, the profile is re-checked
    # from the AI cache without another AI call.
    assert FlakyAI.calls == ["maybe_artist", "maybe_artist"]
    assert [url for kind, url in visited if kind == "profile"].count(
        "https://www.instagram.com/maybe_artist/"
    ) == 2
    assert any("AI ответил после повтора" in notice for notice in state["notices"])
    with sessions() as session:
        lead = session.scalar(select(Lead).where(Lead.username == "maybe_artist"))
        row = session.get(LeadScoutProfile, lead.id)
        assert (row.profile_type, row.profile_decided_by, row.ai_confidence) == (
            "artist",
            "local+ai",
            88,
        )
    detail = service.call("leads.detail", {"id": lead.id})
    assert detail["classification"]["decided_by"] == "local+ai"


def test_already_processed_profiles_are_skipped_and_sources_cycle(scout):
    service, sessions = scout
    other = "https://www.instagram.com/beatsdaily/"
    with sessions.begin() as session:
        memory.mark_profile(session, "known_artist", "rapdaily", "comment", "lead")

    def pages(state):
        if state["kind"] == "source":
            return dict(
                url=state["url"],
                ready=True,
                posts=[f"https://www.instagram.com/p/{state['url'][-5:-1]}/"],
            )
        if state["kind"] == "post":
            source = "rapdaily" if "ily" in state["url"] else "beatsdaily"
            return dict(
                url=state["url"],
                ready=True,
                author=source,
                comments=[dict(profile_url="https://www.instagram.com/known_artist/", text="hi")],
            )
        raise AssertionError("no profile should be opened")

    service.call(
        "settings.save",
        {
            "scout_sources_per_run": 1,
            "scout_methods": ["posts", "comments"],
            "profiles_per_hour": 0,
        },
    )
    service.call("scout.source_add", {"values": [SOURCE, other]})
    first = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    assert service.call("capture.state", {"id": first})["url"] == SOURCE
    while (state := service.call("capture.state", {"id": first}))["status"] == "running":
        service.call("scout.commit_internal", {"id": first, "snapshot": pages(state)})
    events = service.call("scout.events", {"job_id": first})
    assert any(
        e["type"] == "scout:profile-skipped" and e["payload"]["reason"] == "ALREADY_PROCESSED"
        for e in events
    )
    second = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    assert service.call("capture.state", {"id": second})["url"] == other
    rows = {row["username"]: row for row in service.call("scout.source_list", {})}
    assert rows["rapdaily"]["status"] == "done" and rows["rapdaily"]["last_scanned_at"]
    service.call("scout.source_update", {"url": other, "enabled": False})
    assert not {row["username"]: row for row in service.call("scout.source_list", {})}[
        "beatsdaily"
    ]["enabled"]
    service.call("scout.source_remove", {"url": other})
    assert [row["username"] for row in service.call("scout.source_list", {})] == ["rapdaily"]


def test_pause_stop_and_rate_limit_events(scout):
    service, _ = scout
    service.call("settings.save", {"scout_methods": ["posts"]})
    service.call("scout.source_add", {"values": [SOURCE]})
    job = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    service.call("jobs.control", {"id": job, "action": "pause"})
    service.call("jobs.control", {"id": job, "action": "resume"})
    service.call("capture.error_internal", {"id": job, "reason": "rate_limited"})
    service.call("jobs.control", {"id": job, "action": "cancel"})
    types = [event["type"] for event in service.call("scout.events", {"job_id": job})]
    assert types == [
        "scout:run-started",
        "scout:paused",
        "scout:resumed",
        "scout:error",
        "scout:cancelled",
    ]
    error = service.call("scout.events", {"job_id": job})[3]["payload"]
    assert (error["reason"], error["kind"], error["run_id"]) == ("RATE_LIMITED", "rate_limit", job)
    assert {row["username"]: row for row in service.call("scout.source_list", {})}["rapdaily"][
        "status"
    ] == "stopped"


API_FIXTURES = Path(__file__).parent / "fixtures" / "instagram" / "api"
POST = "https://www.instagram.com/p/P1/"


def api_answer(state, fixture=None, status=200):
    body = json.loads((API_FIXTURES / f"{fixture}.json").read_text("utf-8")) if fixture else None
    return dict(
        url=state["url"],
        ready=True,
        blocked=False,
        api={"status": status, "body": body, "redirect": None},
    )


def start_api_run(service, author="artist123", profile_id=ACCOUNT):
    service.call(
        "settings.save",
        {"profiles_per_hour": 0, "scout_methods": ["posts"], "scout_profile_type": "everyone"},
    )
    service.call("scout.source_add", {"values": [SOURCE]})
    job = service.call("scout.start_internal", {"profile_id": profile_id})["id"]
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=SOURCE, ready=True, posts=[POST])}
    )
    service.call(
        "scout.commit_internal",
        {"id": job, "snapshot": dict(url=POST, ready=True, author=author, collaborators=[])},
    )
    return job


def test_profile_resolver_uses_the_api_without_opening_the_profile(scout):
    service, sessions = scout
    job = start_api_run(service)
    state = service.call("capture.state", {"id": job})
    assert state["kind"] == "profile" and state["access"] == "in_place"
    assert state["args"]["path"] == "/api/v1/users/web_profile_info/?username=artist123"
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": api_answer(state, "profile-normal")}
    )
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed" and state["stats"]["resolver"]["api"] == 1
    with sessions() as session:
        lead = session.scalar(select(Lead).where(Lead.username == "artist123"))
        details = session.get(LeadScoutProfile, lead.id)
        assert (lead.followers, lead.external_url) == (2431, "https://linktr.ee/artist123")
        assert details.emails == ["mgmt@artist123.com"] and details.instagram_id == "4242"
        assert details.posts_count == 57
    log = next(
        e["payload"]["log"]
        for e in service.call("scout.events", {"job_id": job})
        if e["type"] == "scout:lead-created"
    )
    assert "[ProfileResolver][@artist123]" in log and "Browser fallback:\nnot required" in log


def test_partial_api_answer_opens_the_page_and_merges(scout):
    service, sessions = scout
    job = start_api_run(service)
    state = service.call("capture.state", {"id": job})
    result = service.call(
        "scout.commit_internal", {"id": job, "snapshot": api_answer(state, "profile-partial")}
    )
    assert result == {"saved": False, "pending": True}
    state = service.call("capture.state", {"id": job})
    assert state["kind"] == "profile" and state["access"] == "navigate" and state["args"] is None
    page = profile_page("artist123", "Rapper. new single out now", followers=2600)
    service.call("scout.commit_internal", {"id": job, "snapshot": page})
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed" and state["stats"]["resolver"]["merged"] == 1
    events = service.call("scout.events", {"job_id": job})
    assert [e["type"] for e in events].count("scout:profile-resolving") == 1
    with sessions() as session:
        lead = session.scalar(select(Lead).where(Lead.username == "artist123"))
        assert lead.followers == 2600 and lead.bio == "Rapper. new single out now"


def test_api_rate_limit_reads_the_page_and_turns_the_api_off(scout):
    service, _ = scout
    job = start_api_run(service)
    state = service.call("capture.state", {"id": job})
    result = service.call(
        "scout.commit_internal", {"id": job, "snapshot": api_answer(state, status=429)}
    )
    assert result == {"saved": False, "pending": True}
    # The run keeps going: the same profile is opened as a page, the API stays off.
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "running" and state["access"] == "navigate"
    assert state["stats"]["resolver"]["api_disabled"] is True
    assert any("web API (429)" in notice for notice in state["notices"])
    page = profile_page("artist123", "Rapper. new single out now", followers=2600)
    service.call("scout.commit_internal", {"id": job, "snapshot": page})
    assert service.call("capture.state", {"id": job})["status"] == "completed"
    events = service.call("scout.events", {"job_id": job})
    assert not [e for e in events if e["type"] == "scout:error"]


def test_not_found_is_skipped_with_its_reason(scout):
    service, sessions = scout
    job = start_api_run(service, author="gone_user")
    state = service.call("capture.state", {"id": job})
    service.call("scout.commit_internal", {"id": job, "snapshot": api_answer(state, status=404)})
    assert service.call("capture.state", {"id": job})["status"] == "completed"
    with sessions() as session:
        assert session.get(ScoutProcessedProfile, "gone_user").reason == "PROFILE_NOT_FOUND"


def test_cached_profile_needs_no_page_and_guest_reads_the_page(scout):
    service, sessions = scout
    job = start_api_run(service)
    state = service.call("capture.state", {"id": job})
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": api_answer(state, "profile-normal")}
    )
    with sessions.begin() as session:
        session.delete(session.get(ScoutProcessedProfile, "artist123"))
        session.get(ScoutSource, SOURCE).last_scanned_at = None
    with sessions.begin() as session:
        for row in session.scalars(select(ScoutProcessedPost)):
            session.delete(row)
    job = start_api_run(service)
    state = service.call("capture.state", {"id": job})
    assert state["access"] == "none" and state["wait_seconds"] == 0
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=state["url"], ready=True)}
    )
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed" and state["stats"]["resolver"]["cache"] == 1
    # A guest session has no API access: the profile page is read directly.
    service.call("settings.save", {"scout_profile_cache_hours": 0, "scout_methods": ["posts"]})
    with sessions.begin() as session:
        for row in session.scalars(select(ScoutProcessedPost)):
            session.delete(row)
        session.get(ScoutSource, SOURCE).last_scanned_at = None
    job = service.call("scout.start_internal", {"profile_id": GUEST_ID, "sources": [SOURCE]})["id"]
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=SOURCE, ready=True, posts=[POST])}
    )
    service.call(
        "scout.commit_internal",
        {"id": job, "snapshot": dict(url=POST, ready=True, author="new_face", collaborators=[])},
    )
    state = service.call("capture.state", {"id": job})
    assert state["kind"] == "profile" and state["access"] == "navigate"
