"""Warm-up orchestration with an isolated database; no real Instagram actions."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from artist_lead_finder.browser_idle import window_work
from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.instagram_warmup import (
    HISTORY_PREFIX,
    KEY,
    InstagramWarmup,
    WarmupSettings,
    active_profile,
    clear_history,
    russian_shop,
)
from artist_lead_finder.models import Lead, SearchJob, Setting

ACCOUNT = "a" * 32
SHOP = {
    "full_name": "Магазин подарков",
    "biography": "Доставка по России",
    "followers_count": 9999,
    "is_private": False,
}


class Browser:
    def __init__(self):
        self.sent, self.followed, self.likes = [], [], 0
        self.profiles = {}
        self.outcomes = {}
        self.candidates = [f"shop{i}" for i in range(20)]

    def open(self, identifier):
        pass

    def navigate(self, identifier, url):
        pass

    def warmup_action(self, identifier, action, value):
        if action == "search":
            return {"usernames": self.candidates}
        if action == "profile":
            return self.profiles.get(value, dict(SHOP))
        if action == "follow":
            self.followed.append(value)
        if action == "like":
            self.likes += 1
        return {"confirmed": True}

    def send_message(self, identifier, username, text):
        self.sent.append((identifier, username, text))
        result = self.outcomes.get(username, {"outcome": "sent"})
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def context(tmp_path):
    engine, sessions = open_database(tmp_path / "warmup.db")
    browser = Browser()
    clock = SimpleNamespace(value=1000)

    def profiles():
        return [{"id": ACCOUNT, "name": "Тест", "cookie_count": 1}]

    service = InstagramWarmup(sessions, browser, profiles, now=lambda: clock.value)
    service.settings({**WarmupSettings().model_dump(), "message": "Тестовый текст"})
    yield service, browser, sessions, clock
    engine.dispose()


def finish(context):
    service, _, _, clock = context
    for _ in range(500):
        if service.state()["run"]["status"] != "running":
            return service.state()["run"]
        clock.value += 200
        service.tick()
    pytest.fail("Run did not finish within its bounded budget")


@pytest.mark.parametrize(
    "patch,expected",
    [
        ({}, True),
        ({"followers_count": 10000}, True),
        ({"followers_count": 10001}, False),
        ({"followers_count": None}, False),
        ({"followers_count": "2k"}, False),
        ({"followers_count": True}, False),
        ({"is_private": True}, False),
        ({"is_private": None}, False),
        ({"full_name": "Clothes Shop", "biography": "Worldwide delivery"}, False),
        ({"full_name": "Музыкант", "biography": "Новый альбом"}, False),
    ],
)
def test_only_small_public_russian_shops_qualify(patch, expected):
    assert (russian_shop({**SHOP, **patch}) == "") is expected


def test_complete_session_obeys_all_targets_and_preserves_message(context):
    service, browser, sessions, _ = context
    service.start({"profile_id": ACCOUNT})
    with sessions() as session:
        assert active_profile(session) == ACCOUNT
        assert window_work(session, ACCOUNT) == (True, True)
    run = finish(context)
    assert run["status"] == "done"
    assert (run["sent"], run["liked"], run["followed"]) == (6, 3, 5)
    assert len(browser.sent) == len({name for _, name, _ in browser.sent}) == 6
    assert all(text == "Тестовый текст" for _, _, text in browser.sent)
    assert len(browser.followed) == 5 and browser.likes == 3
    with sessions() as session:
        assert active_profile(session) is None


def test_no_repeated_messages_in_a_second_run(context):
    service, browser, _, _ = context
    service.start({"profile_id": ACCOUNT})
    finish(context)
    service.start({"profile_id": ACCOUNT})
    finish(context)
    names = [name for _, name, _ in browser.sent]
    assert len(names) == len(set(names)) == 12


def test_history_keeps_each_run_and_its_original_settings(context):
    service, _, _, clock = context
    service.start({"profile_id": ACCOUNT})
    first = finish(context)
    service.settings({**WarmupSettings().model_dump(), "message": "Другой текст", "messages": 1})
    clock.value += 20
    service.start({"profile_id": ACCOUNT})
    second = service.state()["run"]
    assert second["id"] != first["id"]
    history = service.history()
    assert [row["id"] for row in history] == [second["id"], first["id"]]
    assert history[1]["sent"] == 6
    assert history[1]["status"] == "done"
    assert history[1]["completed_at"] >= first["started_at"]
    assert "events" not in history[1] and "settings" not in history[1]
    detail = service.detail({"id": first["id"]})
    assert detail["settings"]["message"] == "Тестовый текст"
    assert detail["targets"]["messages"] == 6
    assert detail["events"][-1]["text"].startswith("Завершено")


def test_restart_retains_history_and_records_interruption(context):
    service, browser, sessions, clock = context
    service.start({"profile_id": ACCOUNT})
    first = finish(context)
    clock.value += 20
    service.start({"profile_id": ACCOUNT})
    active = service.state()["run"]
    restarted = InstagramWarmup(sessions, browser, service.profiles, now=lambda: clock.value)
    assert restarted.detail({"id": active["id"]})["status"] == "interrupted"
    assert restarted.detail({"id": first["id"]})["status"] == "done"
    assert len(restarted.history()) == 2


def test_legacy_last_run_is_imported_once_without_invented_settings(context):
    service, browser, sessions, clock = context
    legacy = {
        "profile_id": ACCOUNT,
        "status": "done",
        "started_at": 500,
        "targets": {"messages": 1, "likes": 1, "follows": 1},
        "sent": 1,
        "liked": 1,
        "followed": 1,
        "checked": 4,
        "events": [{"at": 700, "text": "Завершено", "username": "", "outcome": "info"}],
    }
    with sessions.begin() as session:
        session.get(Setting, KEY).value = {"settings": WarmupSettings().model_dump(), "run": legacy}
    imported = InstagramWarmup(sessions, browser, service.profiles, now=lambda: clock.value)
    row = imported.history()[0]
    assert row["profile_name"] == "Тест" and row["completed_at"] == 700
    assert row["targets"]["messages"] == 1
    assert imported.detail({"id": row["id"]})["settings"] is None
    again = InstagramWarmup(sessions, browser, service.profiles, now=lambda: clock.value)
    assert len(again.history()) == 1 and again.history()[0]["id"] == row["id"]


def test_clear_history_preserves_active_runs_and_does_not_restore_hidden_ones(context):
    service, _, sessions, clock = context
    service.start({"profile_id": ACCOUNT})
    first = finish(context)
    clock.value += 20
    service.start({"profile_id": ACCOUNT})
    second = service.state()["run"]
    with sessions.begin() as session:
        clear_history(session)
    assert [row["id"] for row in service.history()] == [second["id"]]
    with pytest.raises(UserError):
        service.detail({"id": first["id"]})
    service.stop()
    with sessions.begin() as session:
        clear_history(session)
        assert session.get(Setting, HISTORY_PREFIX + first["id"]).value["hidden"]
    service.settings({**WarmupSettings().model_dump(), "message": "Новый текст"})
    assert service.history() == []
    service.start({"profile_id": ACCOUNT})
    finish(context)
    assert sum(name == "shop0" for _, name, _ in context[1].sent) == 1


def test_unknown_history_id_is_rejected(context):
    service = context[0]
    for identifier in ("../instagram_warmup", "f" * 32):
        with pytest.raises(UserError):
            service.detail({"id": identifier})


def test_existing_threads_do_not_use_up_the_message_target(context):
    service, browser, _, _ = context
    browser.outcomes["shop0"] = {"outcome": "error", "error": "existing_thread"}
    service.start({"profile_id": ACCOUNT})
    run = finish(context)
    assert run["status"] == "done" and run["sent"] == 6
    assert len(browser.sent) == 7


@pytest.mark.parametrize(
    "outcome",
    [
        {"outcome": "error", "error": "unconfirmed"},
        {"outcome": "error", "error": "checkpoint"},
        {"outcome": "error", "error": "rate_limited"},
        RuntimeError("network failure"),
    ],
)
def test_uncertain_sends_and_restrictions_stop_without_retry(context, outcome):
    service, browser, _, _ = context
    browser.outcomes["shop0"] = outcome
    service.start({"profile_id": ACCOUNT})
    assert finish(context)["status"] == "failed"
    assert len(browser.sent) == 1
    service.start({"profile_id": ACCOUNT})
    finish(context)
    assert sum(name == "shop0" for _, name, _ in browser.sent) == 1


def test_filters_and_exhausted_search_report_partial_results(context):
    service, browser, _, _ = context
    browser.candidates = ["big", "private", "english", "good"]
    browser.profiles = {
        "big": {**SHOP, "followers_count": 10001},
        "private": {**SHOP, "is_private": True},
        "english": {**SHOP, "full_name": "Shop", "biography": "Shop"},
    }
    service.start({"profile_id": ACCOUNT})
    run = finish(context)
    assert run["status"] == "done" and run["sent"] == 1
    assert [name for _, name, _ in browser.sent] == ["good"]
    assert browser.followed == ["good"]


def test_stop_and_delay_prevent_further_actions(context):
    service, browser, _, _ = context
    service.start({"profile_id": ACCOUNT})
    service.tick()
    assert service.state()["run"]["stage"] == "search"
    service.tick()  # not yet due
    assert service.state()["run"]["query_index"] == 0
    service.stop()
    assert finish(context)["status"] == "stopped"
    assert not browser.sent


def test_a_restriction_between_ticks_is_checked_before_send(context):
    service, browser, _, clock = context
    service.start({"profile_id": ACCOUNT})
    while service.state()["run"]["stage"] != "message":
        clock.value += 200
        service.tick()
    original = browser.warmup_action

    def restricted(identifier, action, value):
        if action == "check":
            raise UserError("Instagram ограничил запросы")
        return original(identifier, action, value)

    browser.warmup_action = restricted
    assert finish(context)["status"] == "failed"
    assert not browser.sent


def test_restart_does_not_resume_or_lose_send_reservations(context):
    service, browser, sessions, clock = context
    browser.outcomes["shop0"] = SystemExit("simulated process crash after send intent")
    service.start({"profile_id": ACCOUNT})
    with pytest.raises(SystemExit):
        finish(context)
    recreated = InstagramWarmup(sessions, browser, service.profiles, now=lambda: clock.value)
    assert recreated.state()["run"]["status"] == "interrupted"
    recreated.start({"profile_id": ACCOUNT})
    finish((recreated, browser, sessions, clock))
    assert sum(name == "shop0" for _, name, _ in browser.sent) == 1


def test_required_message_account_and_numeric_limits(context):
    service, _, _, _ = context
    with pytest.raises(UserError):
        service.start({"profile_id": "missing"})
    service.settings(WarmupSettings().model_dump())
    with pytest.raises(UserError):
        service.start({"profile_id": ACCOUNT})
    with pytest.raises(ValidationError):
        service.settings({"messages": 7})
    with pytest.raises(ValidationError):
        service.settings({"queries": [" "]})


def test_occupied_account_cannot_start_and_protected_lead_is_skipped(context):
    from artist_lead_finder.models import BrowserQueue

    service, browser, sessions, _ = context
    with sessions.begin() as session:
        job = SearchJob(name="Busy", status="running")
        session.add(job)
        session.flush()
        session.add(BrowserQueue(job_id=job.id, profile_id=ACCOUNT, urls=[], weights={}))
    with pytest.raises(UserError, match="занят"):
        service.start({"profile_id": ACCOUNT})
    with sessions.begin() as session:
        session.get(SearchJob, job.id).status = "completed"
        session.add(
            Lead(
                platform="instagram",
                username="shop0",
                profile_url="https://www.instagram.com/shop0/",
                do_not_contact=True,
            )
        )
    service.start({"profile_id": ACCOUNT})
    finish(context)
    assert "shop0" not in [name for _, name, _ in browser.sent]
