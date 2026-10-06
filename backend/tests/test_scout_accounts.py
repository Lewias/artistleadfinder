import sqlite3

import pytest
from sqlalchemy import func, select

from artist_lead_finder.database import open_database
from artist_lead_finder.models import (
    ScoutAccount,
    ScoutProcessedPost,
    ScoutProcessedProfile,
    ScoutRun,
    ScoutSource,
)
from artist_lead_finder.scouting import take_batch
from artist_lead_finder.service import ApplicationService

SOURCE = "https://www.instagram.com/music_news/"
OTHER = "https://www.instagram.com/beats_daily/"
ACCOUNT = "a" * 32
SECOND = "b" * 32


def post(n):
    return f"https://www.instagram.com/p/post{n}/"


def artist(name):
    return f"https://www.instagram.com/{name}/"


def profile_snapshot(url, bio="Independent rapper. New single out now"):
    name = url.split("/")[-2]
    return dict(
        url=url,
        ready=True,
        title=f"Artist (@{name})",
        header="2500 followers 100 following",
        description=f'Artist on Instagram: "{bio}"',
    )


def post_snapshot(url, coauthors, author="music_news"):
    return dict(
        url=url,
        ready=True,
        author=author,
        collaborators=[c.rstrip("/").split("/")[-1] for c in coauthors],
    )


@pytest.fixture
def service(tmp_path):
    engine, sessions = open_database(tmp_path / "accounts.db")
    service = ApplicationService(sessions, tmp_path)
    service.call(
        "settings.save",
        {
            "profiles_per_hour": 0,
            "scout_methods": ["posts"],
        },
    )
    yield service
    service.shutdown()
    engine.dispose()


def drive(service, job, pages):
    """Answer each page the queue asks for from `pages` (url -> snapshot)."""
    while (state := service.call("capture.state", {"id": job}))["status"] == "running":
        service.call("scout.commit_internal", {"id": job, "snapshot": pages(state["url"])})
    return state


def test_account_goal_stops_run_and_continue_needs_a_higher_goal(service):
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 3})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": ACCOUNT})["id"]
    posts = [post(n) for n in range(30)]

    def pages(url):
        if url == SOURCE:
            return dict(url=SOURCE, ready=True, posts=posts)
        if "/p/" in url:
            return post_snapshot(url, [artist(f"artist_{url.split('/')[-2]}")])
        return profile_snapshot(url)

    state = drive(service, job, pages)
    assert state["status"] == "completed" and state["found"] == 3
    assert "Цель достигнута: найдено 3 из 3." in state["notices"]
    with pytest.raises(ValueError, match="Цель достигнута"):
        service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": ACCOUNT})

    # A higher goal continues: already checked artists are duplicates, new ones count.
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 5})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": ACCOUNT})["id"]
    state = drive(service, job, pages)
    assert state["found"] == 2
    [row] = service.scout.accounts([ACCOUNT]).values()
    assert (row["found"], row["target"]) == (5, 5)
    assert len({lead["username"] for lead in service.call("scout.results", {})}) == 5

    assert service.call(
        "scout.account_target", {"profile_id": ACCOUNT, "target": 5, "reset": True}
    ) == {
        "target": 5,
        "found": 0,
    }


def test_sources_go_one_at_a_time_with_publications_in_batches(service):
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 50})
    job = service.call("scout.start_internal", {"sources": [SOURCE, OTHER], "profile_id": ACCOUNT})[
        "id"
    ]
    grid = {SOURCE: [post(n) for n in range(20)], OTHER: [post(100 + n) for n in range(10)]}
    seen = []

    def pages(url):
        if url in grid:
            seen.append(url)
            return dict(url=url, ready=True, posts=grid[url])
        if "/p/" not in url:
            return profile_snapshot(url)
        seen.append(url)
        number = int(url.split("post")[1].strip("/"))
        author = "music_news" if number < 100 else "beats_daily"
        return post_snapshot(url, [artist(f"artist_{number}")], author=author)

    service.call("scout.commit_internal", {"id": job, "snapshot": pages(SOURCE)})
    state = service.call("capture.state", {"id": job})
    # The first source's publications come before the next source, one batch at a time.
    assert state["kind"] == "post" and state["url"] == post(0) and state["backlog"] == 20 - 12
    state = drive(service, job, pages)
    assert state["status"] == "completed"
    assert seen == [
        SOURCE,
        *[post(n) for n in range(20)],
        OTHER,
        *[post(100 + n) for n in range(10)],
    ]
    assert any("Доступные публикации источников закончились" in n for n in state["notices"])


def test_a_batch_is_checked_before_the_next_one_is_read(service):
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 50})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": ACCOUNT})["id"]
    order = []

    def pages(url):
        order.append("grid" if url == SOURCE else "post" if "/p/" in url else "profile")
        if url == SOURCE:
            return dict(url=SOURCE, ready=True, posts=[post(n) for n in range(15)])
        if "/p/" in url:
            return post_snapshot(url, [artist(f"artist_{url.split('/')[-2]}")])
        return profile_snapshot(url)

    drive(service, job, pages)
    # 12 publications, their 12 profiles, then the other 3 publications and their profiles.
    assert order == ["grid", *["post"] * 12, *["profile"] * 12, *["post"] * 3, *["profile"] * 3]


def test_accounts_run_in_parallel_without_checking_the_same_candidate(service):
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 10})
    service.call("scout.account_target", {"profile_id": SECOND, "target": 10})
    first = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": ACCOUNT})["id"]
    second = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": SECOND})["id"]
    with pytest.raises(ValueError, match="уже идёт поиск"):
        service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": ACCOUNT})
    # Processed publications are shared too, so each account reads a different post.
    for job, number in ((first, 1), (second, 2)):
        service.call(
            "scout.commit_internal",
            {"id": job, "snapshot": dict(url=SOURCE, ready=True, posts=[post(number)])},
        )
        service.call(
            "scout.commit_internal",
            {
                "id": job,
                "snapshot": post_snapshot(post(number), [artist("shared"), artist(f"own{job}")]),
            },
        )
    with service.scout.sessions() as session:
        profiles = {
            job: [t["url"] for t in session.get(ScoutRun, job).tasks if t["kind"] == "profile"]
            for job in (first, second)
        }
    assert profiles[first] == [artist("shared"), artist(f"own{first}")]
    assert profiles[second] == [artist(f"own{second}")]


def test_accounts_listing_for_the_scout_screen(service):
    created = service.call("browser.create", {"name": "Main account"})
    [row] = service.call("scout.accounts", {})
    assert row["profile"]["name"] == "Main account"
    assert (row["target"], row["found"], row["run"]) == (100, 0, None)
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": created["id"]})[
        "id"
    ]
    [row] = service.call("scout.accounts", {})
    assert row["run"]["id"] == job and row["run"]["status"] == "running"
    with service.scout.sessions() as session:
        assert session.scalar(select(ScoutAccount.target)) == 100


def test_schema_3_database_gains_scout_columns(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at DATETIME)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version) VALUES (?)", [(1,), (2,), (3,)]
        )
        connection.execute(
            "CREATE TABLE scout_runs (job_id INTEGER PRIMARY KEY, tasks JSON NOT NULL, "
            "observations JSON NOT NULL, notices JSON NOT NULL)"
        )
        connection.execute("INSERT INTO scout_runs VALUES (1, '[]', '{}', '[]')")
    engine, sessions = open_database(path)
    with sessions() as session:
        run = session.get(ScoutRun, 1)
        assert (run.backlog, run.found) == ([], 0)
    engine.dispose()
    with sqlite3.connect(path) as connection:
        assert {4} <= {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }


def test_take_batch_round_robin():
    backlog = [{"url": f"a{i}", "source": "a"} for i in range(3)] + [{"url": "b0", "source": "b"}]
    batch, rest = take_batch(backlog, 3)
    assert [item["url"] for item in batch] == ["a0", "b0", "a1"]
    assert [item["url"] for item in rest] == ["a2"]


def test_each_source_checks_its_candidates_before_the_next_source(service):
    service.call("settings.save", {"scout_methods": ["posts", "tagged"]})
    job = service.call("scout.start_internal", {"sources": [SOURCE, OTHER], "profile_id": ACCOUNT})[
        "id"
    ]
    order = []

    def pages(url):
        # Grid data names every author, so no publication is opened.
        source = OTHER if url.startswith(OTHER) else SOURCE
        if url in (SOURCE, OTHER, SOURCE + "tagged/", OTHER + "tagged/"):
            tab = "tagged" if url.endswith("tagged/") else "grid"
            order.append(f"{tab}:{source.split('/')[-2]}")
            code = f"{tab[0].upper()}{len(order)}"
            author = f"artist_{code.lower()}"
            return dict(
                url=url,
                ready=True,
                posts=[f"https://www.instagram.com/p/{code}/"],
                feed={code: {"author": author, "collaborators": []}},
            )
        order.append("profile")
        return profile_snapshot(url)

    drive(service, job, pages)
    assert order == [
        "grid:music_news",
        "tagged:music_news",
        "profile",
        "profile",
        "grid:beats_daily",
        "tagged:beats_daily",
        "profile",
        "profile",
    ]


def test_goal_reached_leaves_unscanned_sources_for_the_next_run(service):
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 1})
    job = service.call("scout.start_internal", {"sources": [SOURCE, OTHER], "profile_id": ACCOUNT})[
        "id"
    ]

    def pages(url):
        if url == SOURCE:
            return dict(url=SOURCE, ready=True, posts=[post(1)])
        if "/p/" in url:
            return post_snapshot(url, [artist("first_artist")])
        return profile_snapshot(url)

    state = drive(service, job, pages)
    assert state["status"] == "completed" and state["found"] == 1
    with service.scout.sessions() as session:
        other = session.get(ScoutSource, OTHER)
        assert other.last_scanned_at is None and other.status == "stopped"


def test_closed_window_pauses_instead_of_skipping_sources(service):
    job = service.call("scout.start_internal", {"sources": [SOURCE, OTHER], "profile_id": ACCOUNT})[
        "id"
    ]
    service.call("capture.error_internal", {"id": job, "reason": "closed"})
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "paused" and state["url"] == SOURCE
    assert "Окно браузера закрыто" in state["error"]
    with service.scout.sessions() as session:
        assert session.get(ScoutSource, SOURCE).last_scanned_at is None


def test_memory_reset_keeps_leads_and_forgets_the_rest(service):
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 1})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": ACCOUNT})["id"]

    def pages(url):
        if url == SOURCE:
            return dict(url=SOURCE, ready=True, posts=[post(1)])
        if "/p/" in url:
            return post_snapshot(url, [artist("kept_artist")])
        return profile_snapshot(url)

    drive(service, job, pages)
    empty = service.call("scout.start_internal", {"sources": [OTHER], "profile_id": SECOND})["id"]
    with pytest.raises(ValueError, match="Остановите парсинг"):
        service.call("scout.reset_memory", {})
    service.call("jobs.control", {"id": empty, "action": "cancel"})
    # The search without leads goes; the one that found the lead stays but is hidden.
    assert service.call("scout.reset_memory", {}) == {"searches_removed": 1}
    assert [lead["username"] for lead in service.call("scout.results", {})] == ["kept_artist"]
    assert service.call("jobs.list", {}) == []
    with service.scout.sessions() as session:
        for model in (ScoutRun, ScoutProcessedProfile, ScoutProcessedPost):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        source = session.get(ScoutSource, SOURCE)
        assert (source.last_scanned_at, source.status, source.leads_found) == (None, "new", 0)
        assert session.get(ScoutAccount, ACCOUNT).found == 0
    # The same publication is read again by the next run.
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 5})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": ACCOUNT})["id"]
    service.call("scout.commit_internal", {"id": job, "snapshot": pages(SOURCE)})
    assert service.call("capture.state", {"id": job})["url"] == post(1)
