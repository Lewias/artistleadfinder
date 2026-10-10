"""Cloud outreach on a real Postgres with the app's real core (its queue, pacing and
records); only the browser is a stand-in that answers the way Instagram's interface did."""

import psycopg
import pytest
from psycopg.rows import dict_row

from cloud_worker.engine import Engine
from cloud_worker.outreach import OutreachRunner

from .conftest import PROFILE, AsUser, Clock, add_user, lead, make_runner, params

PROXY = {"scheme": "http", "host": "1.2.3.4", "port": 8000}


class Browserless(Engine):
    """The user's core on the server; the Chromium calls answer from `replies`."""

    def __init__(self, data_root, owner, replies):
        super().__init__(data_root, owner)
        self.replies = replies
        self.sent: list[tuple[str, str]] = []
        self.opened: list[str] = []
        # The queue sends only through an open window; here the window is the stand-in.
        self.service.outreach.window_open = lambda profile: True

    def call(self, method, params):
        if method == "browser.runtime.navigate":
            self.opened.append(params["url"])
            return {"url": params["url"]}
        if method == "browser.runtime.send_message":
            self.sent.append((params["username"], params["text"]))
            return self.replies.get(params["username"], {"outcome": "sent", "thread_id": "1"})
        if method.startswith("browser.runtime."):
            return {"open": True} if method.endswith("is_open") else {}
        return super().call(method, params)


def outreach(**overrides) -> dict:
    base = {
        "kind": "outreach",
        "profile_id": PROFILE,
        "usernames": ["talking.already", "closed.dm", "jay.carter"],
        "messages": ["Привет, {{username}}! Послушал твой трек."],
        "settings": {"outreach_daily_limit_per_sender": 30},
    }
    base.update(overrides)
    return base


@pytest.fixture
def owner(db, dsn):
    user = AsUser(dsn, add_user(db, "a@example.com"))
    user.put_session(proxy=PROXY)
    return user


def runner_for(dsn, tmp_path, replies, engines):
    def engine_for(owner):
        engines[owner] = Browserless(tmp_path, owner, replies)
        return engines[owner]

    return OutreachRunner(
        lambda: psycopg.connect(dsn, autocommit=True, row_factory=dict_row),
        engine_for,
        lambda engine: None,
        worker_id="outreach-test",
        clock=Clock(),
        sleep=Idle(),
    )


class Idle:
    """The queue waits only for the pace here; a loop that waits forever is a failure."""

    def __init__(self):
        self.calls = 0

    def __call__(self, seconds):
        self.calls += 1
        if self.calls > 50:
            raise AssertionError("the outreach loop waits with nothing to wait for")


def job_row(db, job_id):
    return db.execute("select * from public.cloud_jobs where id = %s", (job_id,)).fetchone()


def results(db, job_id):
    rows = db.execute(
        "select username, outcome, note, contact_id from public.cloud_candidates "
        "where job_id = %s order by username",
        (job_id,),
    ).fetchall()
    return {row["username"]: row for row in rows}


REPLIES = {
    "talking.already": {"outcome": "error", "error": "existing_thread"},
    "closed.dm": {"outcome": "error", "error": "messages_closed"},
}


def test_a_job_sends_through_the_apps_queue_and_marks_the_crm(db, dsn, owner, tmp_path):
    owner.run(
        "insert into public.crm_contacts (crm, name, channels) values "
        '(\'instagram\', \'Jay\', \'[{"kind": "instagram", "value": "@Jay.Carter"}]\')'
    )
    job_id = owner.start(outreach())
    engines = {}
    runner = runner_for(dsn, tmp_path, REPLIES, engines)
    # The parser's process does not take outreach jobs, and this one not the parser's.
    assert not make_runner(dsn, []).tick()
    assert runner.tick() and not runner.tick()

    job = job_row(db, job_id)
    assert job["stage"] == "completed" and job["error"] is None, job["error"]
    assert job["counters"] == {"total": 3, "sent": 1, "failed": 1, "skipped": 1}
    engine = engines[owner.user_id]
    # Each profile opened first, then the message typed there (the app's own way).
    assert engine.opened == [
        "https://www.instagram.com/talking.already/",
        "https://www.instagram.com/closed.dm/",
        "https://www.instagram.com/jay.carter/",
    ]
    assert engine.sent[-1] == ("jay.carter", "Привет, jay.carter! Послушал твой трек.")
    found = results(db, job_id)
    assert found["jay.carter"]["outcome"] == "sent"
    assert found["talking.already"]["outcome"] == "skipped"
    assert "переписка" in found["talking.already"]["note"]
    assert found["closed.dm"]["outcome"] == "failed" and "закрыты" in found["closed.dm"]["note"]
    contact = db.execute("select id, last_contact_at from public.crm_contacts").fetchone()
    assert contact["last_contact_at"] is not None
    assert found["jay.carter"]["contact_id"] == contact["id"]
    # The owner reads the results; the app's pace came with the job.
    assert owner.run("select kind from public.cloud_jobs")[0]["kind"] == "outreach"
    assert engine.call("settings.get", {})["outreach_daily_limit_per_sender"] == 30

    # Written once: a new job to the same people sends nothing again.
    again = owner.start(outreach(usernames=["jay.carter"]))
    runner_for(dsn, tmp_path, REPLIES, engines).tick()
    job = job_row(db, again)
    assert job["stage"] == "failed" and "нет аккаунтов, которым ещё не писали" in job["error"]
    assert engines[owner.user_id].sent == []


def test_outreach_needs_the_accounts_proxy(db, dsn, tmp_path):
    user = AsUser(dsn, add_user(db, "b@example.com"))
    user.put_session(proxy=None)
    with pytest.raises(psycopg.Error, match="прокси"):
        user.start(outreach())


@pytest.mark.parametrize(
    "bad",
    [
        {"usernames": []},
        {"usernames": ["Not A Name"]},
        {"usernames": [f"user{index}" for index in range(501)]},
        {"messages": []},
        {"messages": ["x" * 1001]},
        {"kind": "spam"},
    ],
)
def test_bad_outreach_jobs_are_refused(owner, bad):
    with pytest.raises(psycopg.Error):
        owner.start(outreach(**bad))


def test_one_outreach_at_a_time_and_parser_jobs_are_counted_apart(owner):
    owner.start(outreach())
    with pytest.raises(psycopg.Error, match="рассылка уже идёт"):
        owner.start(outreach())
    owner.start(params())
    owner.start(params())


def test_an_account_works_in_one_place_at_a_time(db, dsn, owner, tmp_path):
    scout = owner.start(params())
    db.execute(
        "update public.cloud_jobs set stage = 'collecting', locked_by = 'parser', "
        "locked_until = now() + interval '3 minutes' where id = %s",
        (scout,),
    )
    job_id = owner.start(outreach())
    assert not runner_for(dsn, tmp_path, REPLIES, {}).tick()
    assert job_row(db, job_id)["stage"] == "queued"
    db.execute("update public.cloud_jobs set stage = 'completed' where id = %s", (scout,))
    assert runner_for(dsn, tmp_path, REPLIES, {}).tick()
    assert job_row(db, job_id)["stage"] == "completed"


def test_cancel_stops_the_rest_and_keeps_what_was_sent(db, dsn, owner, tmp_path):
    job_id = owner.start(outreach(usernames=["jay.carter", "kid.vibes", "beatz.max"]))
    engines = {}
    runner = runner_for(dsn, tmp_path, {}, engines)
    original = runner.send_one

    def send_then_cancel(engine, item):
        original(engine, item)
        owner.cancel(job_id)

    runner.send_one = send_then_cancel
    runner.tick()
    job = job_row(db, job_id)
    assert job["stage"] == "cancelled"
    # One went out before the cancel was seen; the 30 s pace kept the rest waiting.
    assert [name for name, _ in engines[owner.user_id].sent] == ["jay.carter"]
    assert {name: row["outcome"] for name, row in results(db, job_id).items()} == {
        "jay.carter": "sent"
    }


def test_a_sender_asked_to_log_in_fails_the_job(db, dsn, owner, tmp_path):
    job_id = owner.start(outreach(usernames=["jay.carter"]))
    runner_for(dsn, tmp_path, {"jay.carter": {"outcome": "error", "error": "login"}}, {}).tick()
    job = job_row(db, job_id)
    assert job["stage"] == "failed" and "войти" in job["error"]


def test_after_a_restart_the_same_campaign_goes_on(db, dsn, owner, tmp_path):
    job_id = owner.start(outreach())
    engines = {}

    class Crash(BaseException):
        pass

    first = runner_for(dsn, tmp_path, REPLIES, engines)

    def crash(engine, item):
        raise Crash()

    first.send_one = crash
    with pytest.raises(Crash):
        first.tick()
    engines[owner.user_id].close()
    campaign = job_row(db, job_id)["progress"]["campaign_id"]
    db.execute("update public.cloud_jobs set locked_until = now() - interval '1 second'")
    second = runner_for(dsn, tmp_path, REPLIES, engines)
    assert second.tick()
    job = job_row(db, job_id)
    assert job["stage"] == "completed" and job["progress"]["campaign_id"] == campaign
    # The send the crash cut off is never resent: it waits for the user to check.
    assert len(engines[owner.user_id].sent) == 2


def test_the_parsers_new_leads_nobody_wrote_to(db, dsn, owner, tmp_path):
    owner.start(params(target=3))
    script = [{"leads": [lead("jay.carter"), lead("kid.vibes"), lead("promo", "MEDIA")]}]
    make_runner(dsn, [script]).tick()
    unwritten = owner.run("select username from public.cloud_unwritten(10)")
    assert {row["username"] for row in unwritten} == {"jay.carter", "kid.vibes"}
    owner.start(outreach(usernames=["kid.vibes"]))
    unwritten = owner.run("select username from public.cloud_unwritten(10)")
    assert [row["username"] for row in unwritten] == ["jay.carter"]
    other = AsUser(dsn, add_user(db, "c@example.com"))
    assert other.run("select * from public.cloud_unwritten(10)") == []
