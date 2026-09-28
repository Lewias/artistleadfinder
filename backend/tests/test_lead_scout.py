import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from artist_lead_finder.database import open_database
from artist_lead_finder.lead_scout import memory
from artist_lead_finder.lead_scout.ai import AIUnavailable, OpenRouterClassifier, parse_answer
from artist_lead_finder.lead_scout.candidates import ScoutCandidate, clean_username
from artist_lead_finder.lead_scout.classifier import ProfileText, classify
from artist_lead_finder.lead_scout.contacts import extract_emails, extract_phones
from artist_lead_finder.lead_scout.discovery import post_url
from artist_lead_finder.lead_scout.filters import apply_filters
from artist_lead_finder.lead_scout.settings import ScoutSettings
from artist_lead_finder.models import (
    Lead,
    LeadScoutProfile,
    ScoutProcessedPost,
    ScoutProcessedProfile,
    ScoutSource,
    utcnow,
)
from artist_lead_finder.service import ApplicationService

SOURCE = "https://www.instagram.com/rapdaily/"
ACCOUNT = "a" * 32


def test_classifier_returns_category_and_evidence():
    artist = classify(
        ProfileText(
            username="lil_nova",
            full_name="Lil Nova",
            biography="Independent rapper from ATL. My new single out now on all platforms",
            external_url="https://open.spotify.com/artist/x",
        )
    )
    assert artist.category == "artist" and not artist.uncertain
    assert "Bio contains rapper" in artist.reasons
    assert "Spotify link found" in artist.reasons
    assert any(reason.startswith("Release phrase") for reason in artist.reasons)
    producer = classify(
        ProfileText(
            username="prodbymike",
            biography="Type beat drops daily",
            bio_links=["https://beatstars.com/m"],
        )
    )
    assert producer.category == "producer"
    # Media and creative services need music context.
    media = classify(
        ProfileText(username="rapdaily", biography="Hip hop music news and playlist curator")
    )
    assert media.category == "media"
    plain = classify(ProfileText(username="shots", biography="Wedding photographer"))
    assert plain.category == "other"
    assert "Media/service words without music context" in plain.reasons
    spam = classify(
        ProfileText(username="x", biography="rapper | forex signals and casino giveaway")
    )
    assert spam.category == "other"
    assert any(reason.startswith("Negative signal") for reason in spam.reasons)


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


def test_filters_follow_the_documented_order():
    settings = ScoutSettings(
        scout_min_followers=100, scout_max_followers=5000, scout_only_contacts=True
    )
    assert apply_filters(50, "artist", ["a@b.co"], [], settings) == "FOLLOWERS_TOO_LOW"
    assert apply_filters(9000, "media", [], [], settings) == "FOLLOWERS_TOO_HIGH"
    assert apply_filters(500, "media", [], [], settings) == "WRONG_PROFILE_TYPE"
    assert apply_filters(500, "artist", [], [], settings) == "NO_CONTACT"
    assert apply_filters(500, "artist", [], ["+14045550199"], settings) is None
    both = settings.model_copy(update={"scout_profile_type": "artists_producers"})
    assert apply_filters(500, "producer", ["a@b.co"], [], both) is None
    everyone = settings.model_copy(update={"scout_profile_type": "everyone"})
    assert apply_filters(500, "other", ["a@b.co"], [], everyone) is None
    assert apply_filters(None, "artist", ["a@b.co"], [], settings) is None


def test_ai_answer_parsing_and_transport(tmp_path):
    assert parse_answer('```json\n{"category": "producer", "confidence": 140}\n```') == (
        "producer",
        100,
    )
    with pytest.raises(AIUnavailable):
        parse_answer('{"category": "chef", "confidence": 90}')

    class Keys:
        def configured(self):
            return True

        def load(self):
            return "sk-test"

    sent = {}

    def transport(url, headers, body):
        sent.update(url=url, auth=headers["Authorization"], body=body)
        content = json.dumps({"category": "artist", "confidence": 77})
        return json.dumps({"choices": [{"message": {"content": content}}]})

    ai = OpenRouterClassifier(Keys(), transport)
    assert ai.classify(
        ProfileText(username="x", biography="singer"), "anthropic/claude-haiku-4.5"
    ) == ("artist", 77)
    assert (
        sent["auth"] == "Bearer sk-test"
        and b"Classify this public Instagram profile" in sent["body"]
    )


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
    engine.dispose()


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
    service.call("settings.save", {"profiles_per_hour": 0, **settings})
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
    }

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
    assert kinds[:3] == ["source", "tagged_grid", "followers"]
    assert state["status"] == "completed"
    stats = state["stats"]
    assert (stats["leads"], stats["discovered"]) == (3, 5)
    assert any("Stories пока не поддерживаются" in notice for notice in state["notices"])
    with sessions() as session:
        leads = {row.username: row for row in session.scalars(select(Lead))}
        assert set(leads) == {"artist_one", "producer_two", "fan_one"}
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
        assert session.get(ScoutProcessedPost, "C1") and session.get(ScoutProcessedPost, "T1")
        source = session.get(ScoutSource, SOURCE)
        assert (source.status, source.leads_found) == ("done", 3) and source.last_scanned_at
    events = service.call("scout.events", {"job_id": job})
    types = [event["type"] for event in events]
    for expected in (
        "scout:start",
        "source:start",
        "candidate:found",
        "profile:analyzing",
        "profile:skipped",
        "lead:found",
        "source:done",
        "scout:done",
    ):
        assert expected in types
    log = next(
        e["payload"]["log"]
        for e in events
        if e["type"] == "lead:found" and e["payload"]["username"] == "artist_one"
    )
    assert (
        "[@artist_one]" in log and "RESULT:\nLEAD SAVED" in log and "+ Bio contains rapper" in log
    )
    skip_log = next(
        e["payload"]["log"]
        for e in events
        if e["payload"].get("username") == "too_big" and "log" in e["payload"]
    )
    assert "SKIPPED - FOLLOWERS_TOO_HIGH" in skip_log

    # Next run: the source is in cooldown, so nothing to start.
    with pytest.raises(ValueError, match="кулдауна"):
        service.call("scout.start_internal", {"profile_id": ACCOUNT})


def test_ai_uncertain_mode_and_contacts_filter(scout):
    service, sessions = scout
    calls = []

    class FakeAI:
        def classify(self, profile, model):
            calls.append(profile.username)
            return "artist", 81

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
        e["type"] == "profile:skipped" and e["payload"]["reason"] == "ALREADY_PROCESSED"
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
    assert types == ["scout:start", "scout:pause", "scout:error", "scout:stop"]
    error = service.call("scout.events", {"job_id": job})[2]["payload"]
    assert error["reason"] == "RATE_LIMITED"
    assert {row["username"]: row for row in service.call("scout.source_list", {})}["rapdaily"][
        "status"
    ] == "stopped"
