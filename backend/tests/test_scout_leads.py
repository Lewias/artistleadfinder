"""Scout final layer end to end: decision -> CRM lead, dedup, source history, run metrics,
pause / cancel / crash recovery and atomic saves. No real Instagram or AI is used."""

import pytest
from sqlalchemy import func, select
from test_lead_scout import ACCOUNT, SOURCE, profile_page, run_pages, scout  # noqa: F401

from artist_lead_finder.lead_scout import leads as scout_leads
from artist_lead_finder.lead_scout import memory
from artist_lead_finder.lead_scout.classification import AIClassificationResult
from artist_lead_finder.models import (
    Lead,
    LeadScoutProfile,
    ScoutDecision,
    ScoutLeadSource,
    ScoutProcessedProfile,
    ScoutRun,
    ScoutSource,
)
from artist_lead_finder.service import ApplicationService

OTHER = "https://www.instagram.com/beatsdaily/"


def name_of(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def coauthors(profiles: dict, authors: list[str]):
    """Source page -> one post per source, co-authored by `authors` -> profile pages."""

    def pages(state):
        kind, url = state["kind"], state["url"]
        if kind == "source":
            post = f"https://www.instagram.com/p/{name_of(url).upper()}1/"
            return dict(url=url, ready=True, posts=[post])
        if kind == "post":
            source = name_of(url)[:-1].lower()
            return dict(url=url, ready=True, author=source, collaborators=authors)
        return profiles[name_of(url)]

    return pages


BASE = {"scout_methods": ["posts"]}


def test_run_metrics_skip_reasons_and_decision_log(scout):  # noqa: F811
    service, sessions = scout
    profiles = {
        "artist_ok": profile_page("artist_ok", "Rapper · new single out now · ok@mail.com"),
        "producer_x": profile_page(
            "producer_x",
            "Music producer, beatmaker · x@mail.com",
            links=["https://beatstars.com/x"],
        ),
        "small_one": profile_page("small_one", "Rapper · s@mail.com", followers=10),
        "no_mail": profile_page("no_mail", "Rapper and singer, new album out now"),
    }
    job, state, _ = run_pages(
        service,
        {
            **BASE,
            "scout_min_followers": 100,
            "scout_only_contacts": True,
            "scout_lead_status": "reviewed",
        },
        coauthors(profiles, list(profiles)),
    )
    stats = state["stats"]
    assert state["status"] == "completed"
    assert {key: stats[key] for key in scout_leads.RUN_COUNTERS} == {
        "discovered": 4,
        "resolved": 4,
        # Filtered before classification: no AI would be asked for them.
        "classified": 2,
        "analyzed": 4,
        "leads": 1,
        "leads_updated": 0,
        "skipped": 3,
        "errors": 0,
    }
    assert stats["skips"] == {"WRONG_PROFILE_TYPE": 1, "FOLLOWERS_TOO_LOW": 1, "NO_CONTACT": 1}
    with sessions() as session:
        source = session.get(ScoutSource, SOURCE)
        assert (
            source.candidates_found,
            source.profiles_resolved,
            source.leads_found,
            source.profiles_skipped,
        ) == (4, 4, 1, 3)
        lead = session.scalar(select(Lead).where(Lead.username == "artist_ok"))
        assert lead.status == "reviewed"  # configurable CRM status of new leads
        processed = session.get(ScoutProcessedProfile, "artist_ok")
        assert (processed.result, processed.category, processed.instagram_user_id) == (
            "lead",
            "artist",
            str(1000 + sum(map(ord, "artist_ok"))),
        )
        assert session.get(ScoutProcessedProfile, "small_one").reason == "FOLLOWERS_TOO_LOW"
        decisions = {row.username: row for row in session.scalars(select(ScoutDecision))}
        assert decisions["artist_ok"].decision == "lead_created"
        assert decisions["small_one"].details == "10 < min 100"
        assert decisions["no_mail"].contacts_present is False
    skipped = next(
        e
        for e in service.call("scout.events", {"job_id": job})
        if e["type"] == "scout:profile-skipped" and e["payload"]["username"] == "small_one"
    )
    assert skipped["payload"]["details"] == "10 < min 100"
    assert skipped["payload"]["run_id"] == job
    created = next(
        e
        for e in service.call("scout.events", {"job_id": job})
        if e["type"] == "scout:lead-created"
    )
    assert {k: created["payload"][k] for k in ("username", "source", "category")} == {
        "username": "artist_ok",
        "source": "rapdaily",
        "category": "artist",
    }
    runs = service.call("scout.runs", {})
    assert runs[0]["id"] == job and runs[0]["status"] == "completed"
    assert (runs[0]["sources_total"], runs[0]["sources_processed"], runs[0]["leads"]) == (1, 1, 1)
    assert len(service.call("scout.decisions", {"job_id": job})) == 4
    row = next(
        item for item in service.call("leads.list", {})["items"] if item["username"] == "artist_ok"
    )
    summary = row["scout_profile"]
    assert (summary["profile_type"], summary["emails"], summary["source_username"]) == (
        "artist",
        ["ok@mail.com"],
        "rapdaily",
    )


def test_duplicate_lead_is_updated_with_its_source_history(scout):  # noqa: F811
    service, sessions = scout
    first = {"artist_one": profile_page("artist_one", "Rapper · new single out now", followers=900)}
    run_pages(service, BASE, coauthors(first, ["artist_one"]))
    with sessions.begin() as session:
        lead = session.scalar(select(Lead).where(Lead.username == "artist_one"))
        lead.status = "contacted"  # a manual CRM decision survives rediscovery
        lead_id = lead.id
    again = {
        "artist_one": profile_page("artist_one", "Rapper · new single out now", followers=1500)
    }
    job, state, _ = run_pages(
        service,
        {**BASE, "scout_skip_processed": False, "scout_profile_cache_hours": 0},
        coauthors(again, ["artist_one"]),
        (OTHER,),
    )
    assert (state["stats"]["leads"], state["stats"]["leads_updated"]) == (0, 1)
    assert state["stats"]["skips"] == {"DUPLICATE_LEAD": 1}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        lead = session.get(Lead, lead_id)
        assert (lead.followers, lead.status) == (1500, "contacted")
        sources = {row.source_username: row for row in session.scalars(select(ScoutLeadSource))}
        assert set(sources) == {"rapdaily", "beatsdaily"}
        assert sources["beatsdaily"].origin_url == "https://www.instagram.com/p/BEATSDAILY1/"
        # The first discovery stays on the lead; later sources live in the history.
        assert session.get(LeadScoutProfile, lead_id).source_username == "rapdaily"
        assert session.get(ScoutProcessedProfile, "artist_one").reason == "DUPLICATE_LEAD"
    updated = [
        e
        for e in service.call("scout.events", {"job_id": job})
        if e["type"] == "scout:lead-updated"
    ]
    assert updated[0]["payload"]["change"] == "new source"
    assert "RESULT:\nLEAD UPDATED" in updated[0]["payload"]["log"]
    detail = service.call("leads.detail", {"id": lead_id})
    assert [item["source_username"] for item in detail["found_via"]] == ["rapdaily", "beatsdaily"]


def test_new_leads_go_to_the_outreach_list_and_nothing_is_sent(scout):  # noqa: F811
    service, sessions = scout
    first = {"artist_one": profile_page("artist_one", "Rapper · new single out now")}
    run_pages(service, BASE, coauthors(first, ["artist_one"]))
    listed = service.call("outreach.workspace", {})["usernames"]
    assert [(row["username"], row["status"]) for row in listed] == [("artist_one", "new")]
    assert service.call("outreach.campaigns", {}) == []
    # Removed by hand: a rediscovered (not new) lead is not put back.
    service.call("outreach.workspace_update", {"usernames": []})
    run_pages(
        service,
        {**BASE, "scout_skip_processed": False, "scout_profile_cache_hours": 0},
        coauthors(first, ["artist_one"]),
        (OTHER,),
    )
    assert service.call("outreach.workspace", {})["usernames"] == []
    # Turned off: new leads stay only in the contact base.
    second = {"artist_two": profile_page("artist_two", "Rapper · new single out now")}
    run_pages(
        service,
        {**BASE, "scout_add_to_outreach": False},
        coauthors(second, ["artist_two"]),
        ("https://www.instagram.com/freshbeats/",),
    )
    assert service.call("outreach.workspace", {})["usernames"] == []
    with sessions() as session:
        assert session.scalar(select(Lead).where(Lead.username == "artist_two")) is not None


def test_profile_check_takes_each_source_as_the_lead_itself(scout):  # noqa: F811
    service, sessions = scout
    pages = {
        "rapdaily": profile_page("rapdaily", "Rapper · new single out now", followers=50),
        "beatsdaily": profile_page("beatsdaily", "Wedding photographer"),
    }
    _, state, visited = run_pages(
        service,
        # Posts stay on, but the profile check does not walk the source.
        {"scout_methods": ["profiles", "posts"], "scout_min_followers": 100},
        lambda state: pages[name_of(state["url"])],
        (SOURCE, OTHER),
    )
    assert [kind for kind, _ in visited] == ["profile", "profile"]
    assert state["stats"]["skips"] == {"FOLLOWERS_TOO_LOW": 1, "WRONG_PROFILE_TYPE": 1}
    # The followers range switched off: the small artist becomes a lead.
    run_pages(
        service,
        {
            "scout_methods": ["profiles"],
            "scout_min_followers": 100,
            "scout_use_followers_range": False,
            "scout_skip_processed": False,
            "scout_skip_recent_sources": False,
            "scout_profile_cache_hours": 0,
        },
        lambda state: pages[name_of(state["url"])],
        (SOURCE,),
    )
    with sessions() as session:
        lead = session.scalar(select(Lead).where(Lead.username == "rapdaily"))
        assert lead is not None
        source = session.scalar(select(ScoutLeadSource).where(ScoutLeadSource.lead_id == lead.id))
        assert source.discovery_method == "profile"


def test_known_lead_found_again_only_updates_the_history(scout):  # noqa: F811
    service, sessions = scout
    run_pages(
        service,
        BASE,
        coauthors({"artist_one": profile_page("artist_one", "Rapper")}, ["artist_one"]),
    )

    def no_profiles(state):
        if state["kind"] == "profile":
            raise AssertionError("a processed lead is not opened again")
        return coauthors({}, ["artist_one"])(state)

    job, state, _ = run_pages(service, BASE, no_profiles, (OTHER,))
    assert state["stats"]["leads_updated"] == 1
    assert state["stats"]["skips"] == {"ALREADY_PROCESSED": 1}
    with sessions() as session:
        rows = list(session.scalars(select(ScoutLeadSource).order_by(ScoutLeadSource.id)))
        assert [(row.source_username, row.discovery_method) for row in rows] == [
            ("rapdaily", "post"),
            ("beatsdaily", "post"),
        ]


def test_same_instagram_id_with_a_new_username_updates_the_lead(scout):  # noqa: F811
    service, sessions = scout
    old = {"old_name": profile_page("old_name", "Rapper", user_id="777")}
    run_pages(service, BASE, coauthors(old, ["old_name"]))
    renamed = {"new_name": profile_page("new_name", "Rapper and singer", user_id="777")}
    _, state, _ = run_pages(service, BASE, coauthors(renamed, ["new_name"]), (OTHER,))
    assert state["stats"]["leads_updated"] == 1 and state["stats"]["leads"] == 0
    with sessions() as session:
        leads = list(session.scalars(select(Lead)))
        assert [(lead.username, lead.platform_user_id) for lead in leads] == [("new_name", "777")]


def test_failure_inside_the_save_leaves_no_partial_data(scout, monkeypatch):  # noqa: F811
    service, sessions = scout

    def broken(*args, **kwargs):
        raise RuntimeError("disk hiccup in the middle of the save")

    monkeypatch.setattr(scout_leads, "record_source", broken)
    profiles = {"artist_one": profile_page("artist_one", "Rapper · new single out now")}
    job, state, _ = run_pages(service, BASE, coauthors(profiles, ["artist_one"]))
    # The profile error is logged and counted; the run itself goes on to the end.
    assert state["status"] == "completed"
    assert (state["stats"]["leads"], state["stats"]["errors"]) == (0, 1)
    with sessions() as session:
        for model in (Lead, LeadScoutProfile, ScoutLeadSource, ScoutProcessedProfile):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.get(ScoutSource, SOURCE).leads_found == 0
        assert session.get(ScoutRun, job).found == 0
    error = next(
        e for e in service.call("scout.events", {"job_id": job}) if e["type"] == "scout:error"
    )
    assert (error["payload"]["kind"], error["payload"]["profile"]) == ("profile", "artist_one")


def test_pause_during_classification_resumes_the_same_run(scout):  # noqa: F811
    service, sessions = scout
    calls = []

    class PausingAI:
        def classify(self, profile, local=None, *, model, timeout):
            calls.append(profile.username)
            service.call("jobs.control", {"id": job, "action": "pause"})
            return AIClassificationResult("artist", 90, model)

    service.scout.ai = PausingAI()
    service.call(
        "settings.save",
        {**BASE, "profiles_per_hour": 0, "scout_profile_api": False, "scout_ai_mode": "always"},
    )
    service.call("scout.source_add", {"values": [SOURCE]})
    job = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    pages = coauthors({"artist_one": profile_page("artist_one", "dj")}, ["artist_one"])
    while (state := service.call("capture.state", {"id": job}))["status"] == "running":
        service.call("scout.commit_internal", {"id": job, "snapshot": pages(state)})
    # Paused at the safe point after AI: nothing saved, the profile step is still current.
    assert state["status"] == "paused" and state["kind"] == "profile"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.get(ScoutProcessedProfile, "artist_one") is None
    service.call("jobs.control", {"id": job, "action": "resume"})
    while (state := service.call("capture.state", {"id": job}))["status"] == "running":
        service.call("scout.commit_internal", {"id": job, "snapshot": pages(state)})
    assert state["status"] == "completed" and state["stats"]["leads"] == 1
    assert calls == ["artist_one"]  # the AI answer came from the cache after resume
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ScoutRun)) == 1
    types = [e["type"] for e in service.call("scout.events", {"job_id": job})]
    assert (
        types.index("scout:paused")
        < types.index("scout:resumed")
        < types.index("scout:lead-created")
    )


def four_sources(service, sessions):
    names = ["a_src", "b_src", "c_src", "d_src"]
    urls = [f"https://www.instagram.com/{name}/" for name in names]
    service.call(
        "settings.save",
        {**BASE, "scout_methods": ["posts"], "scout_sources_per_run": 2, "profiles_per_hour": 0},
    )
    service.call("scout.source_add", {"values": urls})
    return urls


def test_cancel_keeps_progress_and_the_next_run_continues(scout):  # noqa: F811
    service, sessions = scout
    urls = four_sources(service, sessions)
    job = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    state = service.call("capture.state", {"id": job})
    assert state["url"] == urls[0]
    # a_src has nothing left after its page; b_src is still to do.
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=urls[0], ready=True, posts=[])}
    )
    service.call("jobs.control", {"id": job, "action": "cancel"})
    assert service.call("capture.state", {"id": job})["status"] == "cancelled"
    rows = {row["username"]: row for row in service.call("scout.source_list", {})}
    assert (rows["a_src"]["status"], rows["b_src"]["status"]) == ("done", "stopped")
    events = service.call("scout.events", {"job_id": job})
    assert events[-1]["type"] == "scout:cancelled"
    # The source cursor goes back to the first unfinished source: b, then c.
    second = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    with sessions() as session:
        assert session.get(ScoutRun, second).stats["sources"] == urls[1:3]


def test_crash_recovery_marks_the_run_interrupted(scout, tmp_path):  # noqa: F811
    service, sessions = scout
    urls = four_sources(service, sessions)
    job = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    with sessions() as session:
        assert memory.state_get(session, "source_cursor") == 2
    # The app closes while the run is running; a new start-up recovers it.
    restarted = ApplicationService(sessions, tmp_path)
    try:
        runs = restarted.call("scout.runs", {})
        assert runs[0]["id"] == job and runs[0]["status"] == "interrupted"
        rows = {row["username"]: row for row in restarted.call("scout.source_list", {})}
        assert rows["a_src"]["status"] == "interrupted"
        with sessions() as session:
            assert memory.state_get(session, "source_cursor") == 0
        error = restarted.call("scout.events", {"job_id": job})[-1]
        assert (error["type"], error["payload"]["reason"]) == ("scout:error", "INTERRUPTED")
        with pytest.raises(ValueError):
            restarted.call("jobs.control", {"id": job, "action": "resume"})
        # Recovery runs once per run.
        assert restarted.scout.recover_interrupted() == 0
        assert restarted.call("scout.start_internal", {"profile_id": ACCOUNT})["id"] != job
    finally:
        restarted.shutdown()
    assert urls


def test_ignore_list_from_the_interface(scout):  # noqa: F811
    service, _ = scout
    result = service.call("scout.ignore", {"username": "@Spotify"})
    assert result["scout_ignore_usernames"] == ["spotify"]
    service.call("scout.ignore", {"username": "https://www.instagram.com/spotify/"})
    assert service.call("settings.get", {})["scout_ignore_usernames"] == ["spotify"]
    with pytest.raises(ValueError):
        service.call("scout.ignore", {"username": ""})


def test_candidate_filters_run_before_the_profile_is_opened(scout):  # noqa: F811
    service, sessions = scout
    profiles = {"first_one": profile_page("first_one", "Rapper")}

    def pages(state):
        if state["kind"] == "profile" and name_of(state["url"]) == "first_one":
            # Another account's run decides on second_one meanwhile.
            with sessions.begin() as session:
                memory.mark_profile(session, "second_one", "beatsdaily", "post", "skipped")
        if state["kind"] == "profile" and name_of(state["url"]) == "second_one":
            # The desktop driver opens nothing for access "none" and sends this snapshot.
            assert state["access"] == "none" and state["wait_seconds"] == 0
            return dict(url=state["url"], ready=True, blocked=False)
        return coauthors(profiles, ["first_one", "second_one"])(state)

    job, state, visited = run_pages(service, BASE, pages)
    assert state["status"] == "completed"
    assert ("profile", "https://www.instagram.com/second_one/") in visited
    skipped = next(
        e
        for e in service.call("scout.events", {"job_id": job})
        if e["type"] == "scout:profile-skipped" and e["payload"]["username"] == "second_one"
    )
    assert skipped["payload"]["reason"] == "ALREADY_PROCESSED"
    with sessions() as session:
        # The earlier decision is kept, not overwritten by this skip.
        assert session.get(ScoutProcessedProfile, "second_one").source_username == "beatsdaily"
