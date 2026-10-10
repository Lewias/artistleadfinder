"""Cloud parser jobs on a real Postgres: sessions, the job lifecycle, the CRM, isolation."""

import psycopg
import pytest

from .conftest import PROFILE, AsUser, add_user, lead, make_runner, params

CHECKPOINT = (
    "Instagram просит подтвердить аккаунт (checkpoint). Пройдите проверку в окне браузера "
    "вручную и продолжите очередь."
)
RATE = "Instagram ограничил запросы. После продолжения очередь выдержит перерыв."


@pytest.fixture
def owner(db, dsn):
    user = AsUser(dsn, add_user(db, "a@example.com"))
    user.put_session(proxy={"scheme": "http", "host": "1.2.3.4", "port": 8000})
    return user


def job_row(db, job_id):
    return db.execute("select * from public.cloud_jobs where id = %s", (job_id,)).fetchone()


def contacts(db, owner_id):
    return db.execute(
        "select * from public.crm_contacts where owner_id = %s order by created_at", (owner_id,)
    ).fetchall()


def test_a_job_runs_the_parser_and_saves_its_leads_to_the_crm(db, dsn, owner):
    job_id = owner.start(params(target=3))
    engines = {}
    script = [
        {"state": {"found": 1}, "leads": [lead("jay.carter", emails=["jay@music.com"])]},
        {"state": {"found": 2}, "leads": [lead("beatz.max", "PRODUCER"), lead("promo", "MEDIA")]},
        {"state": {"found": 3}, "leads": [lead("kid.vibes")]},
    ]
    runner = make_runner(dsn, [script], engines)
    assert runner.tick() and not runner.tick()

    job = job_row(db, job_id)
    assert job["stage"] == "completed" and job["error"] is None and job["finished_at"]
    assert job["counters"] == {
        "found": 4,
        "passed": 3,
        "added": 3,
        "updated": 0,
        "filtered": 1,
        "errors": 0,
    }
    engine = engines[owner.user_id]
    # The parser got the user's session (with its proxy), settings and goal.
    assert engine.sessions[PROFILE]["proxy"]["host"] == "1.2.3.4"
    assert engine.settings == {"scout_methods": ["posts"]}
    assert engine.runs == [{"profile": PROFILE, "sources": ["rapgoat.tv"], "target": 3}]
    assert engine.closed

    saved = {row["channels"][0]["value"]: row for row in contacts(db, owner.user_id)}
    assert set(saved) == {"jay.carter", "beatz.max", "kid.vibes"}
    jay = saved["jay.carter"]
    assert jay["source"] == "Cloud Parser" and jay["statuses"] == ["Артист"]
    assert {"kind": "email", "value": "jay@music.com"} in jay["channels"]
    assert jay["cloud"]["category"] == "ARTIST" and jay["cloud"]["job_id"] == job_id
    # Instagram renewed the cookies while the parser worked: the server keeps the new ones.
    session = db.execute("select record from public.cloud_sessions").fetchone()["record"]
    assert session["cookies"] == [{"name": "sessionid", "value": "renewed"}]
    # The owner reads the results; the cookies are never readable.
    rows = owner.run("select username, outcome from public.cloud_candidates order by id")
    assert [row["outcome"] for row in rows] == ["added", "added", "filtered", "added"]
    with pytest.raises(psycopg.Error):
        owner.run("select record from public.cloud_sessions")
    listed = owner.run("select * from public.cloud_sessions_list()")
    assert listed[0]["profile_id"] == PROFILE and listed[0]["has_proxy"] is True
    assert "record" not in listed[0]


def test_the_same_request_makes_one_job_a_session_is_needed_and_jobs_are_limited(db, dsn, owner):
    first = owner.start(params(), request_id="click-0001")
    assert owner.start(params(), request_id="click-0001") == first
    owner.start(params())
    with pytest.raises(psycopg.Error, match="Уже идёт"):
        owner.start(params())
    other = AsUser(dsn, add_user(db, "b@example.com"))
    with pytest.raises(psycopg.Error, match="Сессия"):
        other.start(params())


@pytest.mark.parametrize(
    "bad",
    [{"sources": []}, {"categories": []}, {"target": 0}, {"target": 5000}, {"profile_id": "x"}],
)
def test_bad_jobs_are_refused(owner, bad):
    with pytest.raises(psycopg.Error):
        owner.start(params(**bad))


def test_up_to_500_sources_are_taken(owner):
    many = [f"source{index}" for index in range(500)]
    owner.start(params(sources=many))
    with pytest.raises(psycopg.Error, match="500"):
        owner.start(params(sources=[*many, "extra"]))


def test_cancel_stops_the_parser_and_keeps_what_was_saved(db, dsn, owner):
    job_id = owner.start(params(target=10))
    engines = {}
    script = [
        {
            "state": {"found": 1},
            "leads": [lead("jay.carter")],
            "then": lambda: owner.cancel(job_id),
        },
        # Found before the cancel was seen: already done work is kept.
        {"state": {"found": 2}, "leads": [lead("kid.vibes")]},
        {"state": {"found": 3}, "leads": [lead("never.reached")]},
    ]
    make_runner(dsn, [script], engines).tick()
    job = job_row(db, job_id)
    assert job["stage"] == "cancelled" and job["counters"]["added"] == 2
    assert ("jobs.control", {"id": 1, "action": "cancel"}) in engines[owner.user_id].calls
    # A queued job is cancelled at once, without the parser.
    queued = owner.start(params())
    owner.cancel(queued)
    assert job_row(db, queued)["stage"] == "cancelled"


def test_a_checkpoint_fails_the_job_and_asks_for_a_fresh_session(db, dsn, owner):
    job_id = owner.start(params())
    script = [
        {"state": {"found": 0}, "leads": [lead("jay.carter")]},
        {"state": {"status": "paused", "error": CHECKPOINT}},
    ]
    make_runner(dsn, [script]).tick()
    job = job_row(db, job_id)
    assert job["stage"] == "failed" and "войти или подтвердить" in job["error"]
    assert job["counters"]["added"] == 1


def test_a_rate_limit_is_waited_out_and_the_parser_goes_on(db, dsn, owner):
    job_id = owner.start(params(target=2))
    engines = {}
    script = [
        {"state": {"status": "paused", "error": RATE}},
        {"state": {"found": 2}, "leads": [lead("jay.carter"), lead("kid.vibes")]},
    ]
    make_runner(dsn, [script], engines).tick()
    assert job_row(db, job_id)["stage"] == "completed"
    assert ("jobs.control", {"id": 1, "action": "resume"}) in engines[owner.user_id].calls


class Crash(BaseException):
    """The process dies: nothing after it runs."""


def test_after_a_restart_the_job_goes_on_for_the_missing_leads_only(db, dsn, owner):
    job_id = owner.start(params(target=3))

    def crash():
        raise Crash()

    first = [{"state": {"found": 1}, "leads": [lead("jay.carter")], "then": crash}]
    with pytest.raises(Crash):
        make_runner(dsn, [first]).tick()
    job = job_row(db, job_id)
    assert job["stage"] == "collecting" and job["locked_until"] is not None
    # Nobody takes it while the lease lives; once it runs out, a new process does.
    engines = {}
    second = [{"state": {"found": 2}, "leads": [lead("jay.carter"), lead("kid.vibes")]}]
    restarted = make_runner(dsn, [second], engines, worker_id="cloud-restarted")
    assert not restarted.tick()
    db.execute("update public.cloud_jobs set locked_until = now() - interval '1 second'")
    assert restarted.tick()
    job = job_row(db, job_id)
    assert job["stage"] == "completed" and job["attempts"] == 1
    # The new run looks for the 2 still missing; the lead found again is not saved twice.
    assert engines[owner.user_id].runs[0]["target"] == 2
    assert engines[owner.user_id].cancelled_open == 1
    assert len(contacts(db, owner.user_id)) == 2


def test_found_again_updates_the_contact_and_keeps_the_users_work(db, dsn, owner):
    owner.run(
        """
        insert into public.crm_contacts (crm, name, statuses, channels, notes, earned)
        values ('instagram', 'Jay', '["Сделка"]',
                '[{"kind": "instagram", "value": "https://www.instagram.com/Jay.Carter/"}]',
                'звонили во вторник', 500)
        """
    )
    for _ in range(2):
        job_id = owner.start(params(target=1))
        make_runner(dsn, [[{"leads": [lead("jay.carter", emails=["jay@music.com"])]}]]).tick()
        assert job_row(db, job_id)["counters"]["updated"] == 1
    [jay] = contacts(db, owner.user_id)
    assert jay["notes"] == "звонили во вторник" and float(jay["earned"]) == 500
    assert jay["statuses"] == ["Сделка"] and jay["name"] == "Jay"
    assert jay["instagram_id"] == "id-jay.carter"
    assert [c for c in jay["channels"] if c["kind"] == "email"] == [
        {"kind": "email", "value": "jay@music.com"}
    ]


def test_users_see_and_change_only_their_own_jobs_and_crm(db, dsn, owner):
    other = AsUser(dsn, add_user(db, "b@example.com"))
    other.run(
        "insert into public.crm_contacts (crm, name, notes, channels) values "
        "('instagram', 'Jay', 'чужая заметка', "
        '\'[{"kind": "instagram", "value": "jay.carter"}]\')'
    )
    job_id = owner.start(params(target=1))
    make_runner(dsn, [[{"leads": [lead("jay.carter")]}]]).tick()

    assert other.run("select id from public.cloud_jobs") == []
    assert other.run("select id from public.cloud_candidates") == []
    assert other.run("select * from public.cloud_sessions_list()") == []
    with pytest.raises(psycopg.Error, match="не найдена"):
        other.cancel(job_id)
    assert len(contacts(db, owner.user_id)) == 1
    [theirs] = contacts(db, other.user_id)
    assert theirs["notes"] == "чужая заметка" and theirs["cloud"] is None
    with pytest.raises(psycopg.Error):
        owner.run("select locked_by from public.cloud_jobs")


def test_a_hung_call_is_caught_by_the_watchdog(dsn, caplog):
    runner = make_runner(dsn, [])
    assert not runner.stuck()  # no job in work

    class Engine:
        calling = "browser.runtime.eval"

    runner.active, runner.engine, runner.alive = "job-1", Engine(), runner.clock()
    assert not runner.stuck()
    runner.alive -= 1000
    exits = []
    with caplog.at_level("ERROR"):
        assert runner.stuck()
        with pytest.raises(_Stop):
            runner.watch(exit=lambda code: (exits.append(code), _stop()), every=0)
    assert exits == [70]
    assert "cloud_parser_stuck" in caplog.text
    record = next(item for item in caplog.records if item.message == "cloud_parser_stuck")
    assert record.job == "job-1" and record.error_type == "browser.runtime.eval"


class _Stop(Exception):
    pass


def _stop():
    raise _Stop
