import sqlite3

import pytest
from sqlalchemy import select

from artist_lead_finder.database import open_database
from artist_lead_finder.models import ScoutAccount, ScoutRun
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


def post_snapshot(url, commenters, author="music_news"):
    return dict(
        url=url,
        ready=True,
        author=author,
        comments=[dict(profile_url=c, text="New single out now") for c in commenters],
    )


@pytest.fixture
def service(tmp_path):
    engine, sessions = open_database(tmp_path / "accounts.db")
    service = ApplicationService(sessions, tmp_path)
    service.call("settings.save", {"profiles_per_hour": 0, "scout_methods": ["posts", "comments"]})
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


def test_backlog_is_queued_in_batches_until_publications_run_out(service):
    service.call("scout.account_target", {"profile_id": ACCOUNT, "target": 50})
    job = service.call("scout.start_internal", {"sources": [SOURCE, OTHER], "profile_id": ACCOUNT})[
        "id"
    ]
    grid = {SOURCE: [post(n) for n in range(20)], OTHER: [post(100 + n) for n in range(10)]}
    seen = []

    def pages(url):
        if url in grid:
            return dict(url=url, ready=True, posts=grid[url])
        seen.append(url)
        author = "music_news" if int(url.split("post")[1].strip("/")) < 100 else "beats_daily"
        return post_snapshot(url, [], author=author)

    service.call("scout.commit_internal", {"id": job, "snapshot": pages(SOURCE)})
    service.call("scout.commit_internal", {"id": job, "snapshot": pages(OTHER)})
    state = service.call("capture.state", {"id": job})
    # Only the first batch is queued; it alternates between sources.
    assert state["kind"] == "post" and state["backlog"] == 30 - 12
    assert state["url"] == post(0)
    with service.scout.sessions() as session:
        queued = [t["url"] for t in session.get(ScoutRun, job).tasks if t["kind"] == "post"]
    assert queued[:4] == [post(0), post(100), post(1), post(101)]
    state = drive(service, job, pages)
    assert state["status"] == "completed" and len(seen) == 30
    assert any("Доступные публикации источников закончились" in n for n in state["notices"])


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
