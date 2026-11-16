"""Threads publishing: authorization, the queue, missed posts and no duplicate after a
failure. The API is a stand-in in memory; nothing reaches Threads."""

import socket
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.models import SchemaMigration, ThreadsAccount, ThreadsPost
from artist_lead_finder.threads import service as threads_service
from artist_lead_finder.threads.api import ThreadsError, text_errors, text_length
from artist_lead_finder.threads.service import ThreadsService

START = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
# The computer's clock in these tests: UTC+3, no daylight saving.
ZONE = timezone(timedelta(hours=3))


class Clock:
    def __init__(self):
        self.value = START

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


class FakeThreads:
    """The Threads side: tokens, containers, posts. `fail` holds errors for the next
    calls by name; `lose` publishes but loses the answer."""

    def __init__(self, clock):
        self.clock = clock
        self.tokens = {"long-1": "u1"}
        self.containers = {}
        self.posts = []
        self.fail = {}
        self.lose = 0
        self.calls = []
        self.exchanged = []

    def client(self, token):
        return FakeClient(self, token)


PROFILE = "a" * 32


class FakeBrowser:
    def __init__(self, clock):
        self.clock = clock
        self.opened = True
        self.username = "beats.daily"
        self.posts = []
        self.lose = False
        self.submissions = 0

    def is_open(self, profile_id):
        return self.opened

    def threads_identity(self, profile_id):
        return {"username": self.username, "name": "Beats"} if self.opened else {}

    def threads_recent(self, profile_id, username):
        if self.username != username:
            raise ThreadsError("auth", "В браузере другой аккаунт")
        return list(reversed(self.posts))

    def threads_publish(self, profile_id, username, text):
        if self.username != username:
            raise ThreadsError("auth", "В браузере другой аккаунт")
        self.submissions += 1
        item = {
            "id": f"web{self.submissions}",
            "text": text,
            "permalink": f"https://www.threads.com/@{username}/post/web{self.submissions}",
            "timestamp": self.clock().isoformat(),
        }
        self.posts.append(item)
        if self.lose:
            raise ThreadsError("network", "Ответ потерян")
        return item


def browser_connect(service, clock):
    browser = FakeBrowser(clock)
    service.browser = browser
    service.browser_profiles = lambda: [
        {"id": PROFILE, "name": "Instagram profile", "cookie_count": 1, "proxy": None}
    ]
    state = service.call("threads.browser_start", {"profile_id": PROFILE})
    return browser, state["accounts"][0]["id"]


def test_browser_login_reuses_profile_without_graph_token_and_preserves_queue(app):
    service, clock, server, sessions = app
    browser, account_id = browser_connect(service, clock)
    post_id = schedule(service, account_id, "Сохранить очередь", START + timedelta(hours=1))
    service.call("threads.browser_start", {"profile_id": PROFILE})
    with sessions() as session:
        account = session.get(ThreadsAccount, account_id)
        assert account.auth_mode == "browser" and account.browser_profile_id == PROFILE
        assert account.token_box is None
    assert post_row(sessions, post_id).account_id == account_id
    browser.username = "another.account"
    state = service.call("threads.browser_start", {"profile_id": PROFILE})
    assert state["browser_auth"]["status"] == "error"
    assert state["accounts"][0]["username"] == "beats.daily"
    assert server.calls == []


def test_browser_queue_opens_due_profile_and_publishes_once(app):
    service, clock, server, sessions = app
    browser, account_id = browser_connect(service, clock)
    post_id = schedule(service, account_id, "Браузерный пост", START + timedelta(minutes=1))
    browser.opened = False
    assert service.shell()["browser_profiles"] == [PROFILE]
    run(service, clock, minutes=1)
    assert not browser.posts
    assert post_row(sessions, post_id).publish_sent_at is None
    assert post_row(sessions, post_id).attempts == 0
    browser.opened = True
    run(service, clock, minutes=1)
    assert post_row(sessions, post_id).status == "published"
    assert browser.submissions == 1 and server.calls == []
    run(service, clock, minutes=1)
    assert browser.submissions == 1


def test_browser_lost_confirmation_recovers_after_restart_without_reclick(app):
    service, clock, server, sessions = app
    browser, account_id = browser_connect(service, clock)
    browser.lose = True
    post_id = schedule(service, account_id, "С потерей ответа", START + timedelta(minutes=1))
    run(service, clock, minutes=1)
    assert post_row(sessions, post_id).status == "publishing"
    service.restore()
    run(service, clock, minutes=1)
    assert post_row(sessions, post_id).status == "published"
    assert browser.submissions == 1


def test_browser_old_identical_post_without_time_cannot_confirm_send(app):
    service, clock, server, sessions = app
    browser, account_id = browser_connect(service, clock)
    browser.lose = True
    post_id = schedule(service, account_id, "Одинаковый текст", START + timedelta(minutes=1))
    run(service, clock, minutes=1)
    browser.posts[0]["timestamp"] = None
    run(service, clock, minutes=7 * 60)
    assert post_row(sessions, post_id).status == "review"
    assert browser.submissions == 1


class FakeClient:
    def __init__(self, server, token):
        self.server, self.token = server, token

    def _hit(self, name):
        self.server.calls.append(name)
        queue = self.server.fail.get(name)
        if queue:
            raise queue.pop(0)
        if name not in ("exchange_code",) and self.token not in self.server.tokens:
            raise ThreadsError(
                "auth", "Доступ к Threads истёк или отозван: переподключите аккаунт.", 190
            )

    def exchange_code(self, app_id, secret, code, redirect_uri):
        self._hit("exchange_code")
        self.server.exchanged.append((app_id, secret, code, redirect_uri))
        self.server.tokens["short-1"] = "u1"
        return {"access_token": "short-1", "user_id": "u1"}

    def long_lived(self, secret):
        self._hit("long_lived")
        if not self.token.startswith("short"):
            raise ThreadsError("invalid", "already long-lived")
        self.server.tokens["long-1"] = "u1"
        return {"access_token": "long-1", "expires_in": 60 * 86400}

    def refresh(self):
        self._hit("refresh")
        self.server.tokens["long-2"] = "u1"
        return {"access_token": "long-2", "expires_in": 60 * 86400}

    def me(self):
        self._hit("me")
        return {"id": "u1", "username": "beats.daily", "name": "Beats"}

    def create_text(self, user_id, text):
        self._hit("create_text")
        container = f"c{len(self.server.containers) + 1}"
        self.server.containers[container] = {"status": "FINISHED", "text": text}
        return container

    def container(self, container_id):
        self._hit("container")
        return {"id": container_id, "status": self.server.containers[container_id]["status"]}

    def publish(self, user_id, container_id):
        self._hit("publish")
        found = self.server.containers[container_id]
        if found["status"] == "PUBLISHED":
            raise ThreadsError("invalid", "already published")
        found["status"] = "PUBLISHED"
        media = f"m{len(self.server.posts) + 1}"
        self.server.posts.append(
            {
                "id": media,
                "text": found["text"],
                "permalink": f"https://www.threads.net/@beats.daily/post/{media}",
                "timestamp": self.server.clock().strftime("%Y-%m-%dT%H:%M:%S+0000"),
            }
        )
        if self.server.lose:
            self.server.lose -= 1
            raise ThreadsError("network", "Нет связи с Threads. Проверьте интернет.")
        return media

    def media(self, media_id):
        self._hit("media")
        return next(item for item in self.server.posts if item["id"] == media_id)

    def recent(self, user_id, limit=15):
        self._hit("recent")
        return list(reversed(self.server.posts))[:limit]


@pytest.fixture
def app(tmp_path, monkeypatch):
    engine, sessions = open_database(tmp_path / "db.sqlite3")
    clock = Clock()
    server = FakeThreads(clock)
    service = ThreadsService(
        sessions,
        now=clock,
        client=server.client,
        to_utc=lambda naive: naive.replace(tzinfo=ZONE).astimezone(timezone.utc),
        to_local=lambda moment: moment.astimezone(ZONE),
        sleep=lambda seconds: None,
    )
    # Tests drive the queue with tick(); no background thread.
    monkeypatch.setattr(service, "_ensure_thread", lambda: None)
    yield service, clock, server, sessions
    service.shutdown()
    engine.dispose()


def connect(service):
    return service.call("threads.token_add", {"token": "long-1" + "x" * 0 + "-" * 20})


@pytest.fixture
def connected(app):
    service, clock, server, sessions = app
    server.tokens["long-1" + "-" * 20] = "u1"
    connect(service)
    account = service.state()["accounts"][0]
    return service, clock, server, sessions, account["id"]


def post_row(sessions, post_id):
    with sessions() as session:
        return session.get(ThreadsPost, post_id)


def schedule(service, account_id, text, at):
    return service.call(
        "threads.post_schedule", {"account_id": account_id, "text": text, "at": at.isoformat()}
    )["saved"]


def run(service, clock, minutes=0, ticks=1):
    for _ in range(ticks):
        if minutes:
            clock.advance(minutes=minutes)
        while service.tick():
            pass


def test_text_rules_count_emoji_by_bytes_and_limit_links():
    assert text_length("привет") == 6
    assert text_length("🔥") == 4
    assert text_errors("а" * 500) == []
    assert text_errors("а" * 501)
    assert text_errors("🔥" * 126)  # 504 bytes
    assert text_errors("   ")
    links = " ".join(f"https://site{n}.com/x" for n in range(6))
    assert any("ссылок" in error for error in text_errors(links))


def test_a_pasted_token_is_kept_encrypted_and_the_account_shown(connected):
    service, clock, server, sessions, account_id = connected
    with sessions() as session:
        account = session.get(ThreadsAccount, account_id)
        assert account.token_box and b"long-1" not in account.token_box
        assert account.username == "beats.daily" and account.status == "active"


def test_code_flow_uses_the_users_own_app_and_gets_a_long_token(app):
    service, clock, server, sessions = app
    with pytest.raises(UserError):
        service.call("threads.auth_start", {})
    with pytest.raises(UserError, match="адрес возврата"):
        service.call("threads.app_save", {"app_id": "123456", "app_secret": "abcdef123"})
    service.call(
        "threads.app_save",
        {"app_id": "123456", "app_secret": "abcdef123", "redirect_uri": "https://localhost/cb"},
    )
    state = service.state()
    assert state["app"] == {
        "app_id": "123456",
        "has_secret": True,
        "redirect_uri": "https://localhost/cb",
    }
    started = service.call("threads.auth_start", {})
    query = parse_qs(urlparse(started["url"]).query)
    assert query["client_id"] == ["123456"] and query["redirect_uri"] == ["https://localhost/cb"]
    assert "threads_content_publish" in query["scope"][0] and not started["listening"]
    # A forged or stale state is refused.
    with pytest.raises(UserError):
        service.call("threads.auth_finish", {"url": "https://localhost/cb?code=abc&state=nope"})
    started = service.call("threads.auth_start", {})
    state_value = parse_qs(urlparse(started["url"]).query)["state"][0]
    service.call(
        "threads.auth_finish", {"url": f"https://localhost/cb?code=abc&state={state_value}#_"}
    )
    assert server.exchanged == [("123456", "abcdef123", "abc", "https://localhost/cb")]
    account = service.state()["accounts"][0]
    assert account["status"] == "active" and account["username"] == "beats.daily"
    assert account["expires_at"] == (START + timedelta(days=60)).isoformat()
    assert service.state()["auth"]["status"] == "done"


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_localhost_return_address_is_caught_by_the_local_listener(app):
    service, clock, server, sessions = app
    port = free_port()
    redirect = f"http://localhost:{port}/threads/callback"
    service.call(
        "threads.app_save", {"app_id": "123", "app_secret": "s3cret", "redirect_uri": redirect}
    )
    started = service.call("threads.auth_start", {})
    assert started["listening"]
    state_value = parse_qs(urlparse(started["url"]).query)["state"][0]
    url = f"http://127.0.0.1:{port}/threads/callback?code=xyz&state={state_value}"
    with urllib.request.urlopen(url, timeout=5) as response:
        page = response.read().decode()
    assert "подключён" in page
    assert service.state()["accounts"][0]["status"] == "active"
    assert server.exchanged[0][2] == "xyz"


def test_a_scheduled_post_goes_out_once_at_its_time_with_its_link(connected):
    service, clock, server, sessions, account_id = connected
    post_id = schedule(service, account_id, "Новый бит сегодня", START + timedelta(minutes=30))
    run(service, clock, minutes=10)
    assert server.posts == []
    run(service, clock, minutes=25)
    post = post_row(sessions, post_id)
    assert post.status == "published" and post.permalink.endswith("/post/m1")
    assert len(server.posts) == 1
    run(service, clock, minutes=60, ticks=3)
    assert len(server.posts) == 1


def test_a_lost_publish_answer_is_checked_and_never_published_twice(connected):
    service, clock, server, sessions, account_id = connected
    server.lose = 1
    post_id = schedule(service, account_id, "Один раз", START + timedelta(minutes=1))
    run(service, clock, minutes=2)
    post = post_row(sessions, post_id)
    assert post.status == "publishing" and post.publish_sent_at is not None
    # Before the wait nothing is tried again; afterwards the post is found on the profile.
    run(service, clock, minutes=0)
    run(service, clock, minutes=1)
    post = post_row(sessions, post_id)
    assert post.status == "published" and post.permalink.endswith("/m1")
    assert len(server.posts) == 1 and server.calls.count("publish") == 1


def test_a_publish_that_did_not_go_through_is_repeated_from_the_same_container(connected):
    service, clock, server, sessions, account_id = connected
    server.fail["publish"] = [ThreadsError("network", "Нет связи")]
    post_id = schedule(service, account_id, "Со второго раза", START + timedelta(minutes=1))
    run(service, clock, minutes=2)
    assert post_row(sessions, post_id).status == "publishing"
    run(service, clock, minutes=1)  # checked: not out, back to the queue
    run(service, clock, minutes=1)
    post = post_row(sessions, post_id)
    assert post.status == "published" and len(server.posts) == 1
    assert len(server.containers) == 1


def test_inconclusive_container_state_requires_review_without_republishing(connected):
    service, clock, server, sessions, account_id = connected
    server.fail["publish"] = [ThreadsError("network", "Нет связи")]
    post_id = schedule(service, account_id, "Нужна проверка", START + timedelta(minutes=1))
    run(service, clock, minutes=2)
    server.containers["c1"]["status"] = "IN_PROGRESS"
    run(service, clock, minutes=7 * 60)
    post = post_row(sessions, post_id)
    assert post.status == "review" and post.next_attempt_at is None
    assert server.calls.count("publish") == 1 and not server.posts
    service.call("threads.post_resolve", {"id": post_id, "published": False})
    assert post_row(sessions, post_id).status == "draft"
    run(service, clock, minutes=1)
    assert server.calls.count("publish") == 1


def test_a_restart_after_the_publish_request_checks_before_anything(connected, tmp_path):
    service, clock, server, sessions, account_id = connected
    post_id = schedule(service, account_id, "Перезапуск", START + timedelta(minutes=1))
    clock.advance(minutes=2)
    # The core died right after sending: Threads has the post, the answer never came.
    with sessions.begin() as session:
        post = session.get(ThreadsPost, post_id)
        post.status, post.attempts = "publishing", 1
        post.container_id, post.container_at = "c1", clock()
        post.publish_sent_at = clock()
    server.containers["c1"] = {"status": "FINISHED", "text": "Перезапуск"}
    FakeClient(server, "long-1" + "-" * 20).publish("u1", "c1")
    service.restore()
    run(service, clock, minutes=1)
    assert post_row(sessions, post_id).status == "published"
    assert len(server.posts) == 1
    # Cut off before the request: back in the queue, published once.
    other = schedule(service, account_id, "До запроса", clock() + timedelta(minutes=1))
    with sessions.begin() as session:
        post = session.get(ThreadsPost, other)
        post.status, post.attempts = "publishing", 1
    service.restore()
    assert post_row(sessions, other).status == "scheduled"
    run(service, clock, minutes=2)
    assert post_row(sessions, other).status == "published" and len(server.posts) == 2


def test_after_sleep_missed_posts_wait_instead_of_going_out_together(connected):
    service, clock, server, sessions, account_id = connected
    ids = [
        schedule(service, account_id, f"Пост {n}", START + timedelta(hours=n + 1)) for n in range(5)
    ]
    clock.advance(hours=8)  # the computer slept
    run(service, clock)
    assert server.posts == []
    assert {post_row(sessions, post_id).status for post_id in ids} == {"missed"}
    notices = service.shell()["notices"]
    assert notices and notices[0]["kind"] == "missed"
    # Into the next slots (10:00 local = 07:00 UTC), one a day, oldest first.
    service.call("threads.missed_reschedule", {})
    times = [post_row(sessions, post_id).scheduled_at for post_id in ids]
    assert times[0] == datetime(2026, 11, 17, 7, 0)
    assert times[1] == datetime(2026, 11, 18, 7, 0)
    clock.value = datetime(2026, 11, 17, 7, 1, tzinfo=timezone.utc)
    run(service, clock)
    assert [post["text"] for post in server.posts] == ["Пост 0"]


def test_slots_follow_the_computer_clock(connected):
    service, clock, server, sessions, account_id = connected
    service.call("threads.account_slots", {"id": account_id, "slots": ["18:00", "10:00"]})
    # 12:00 UTC is 15:00 on the computer: the next slot is 18:00 there, 15:00 UTC.
    first = service.call(
        "threads.post_schedule", {"account_id": account_id, "text": "A", "slot": True}
    )
    second = service.call(
        "threads.post_schedule", {"account_id": account_id, "text": "B", "slot": True}
    )
    assert post_row(sessions, first["saved"]).scheduled_at == datetime(2026, 11, 16, 15, 0)
    assert post_row(sessions, second["saved"]).scheduled_at == datetime(2026, 11, 17, 7, 0)
    with pytest.raises(UserError):
        service.call("threads.account_slots", {"id": account_id, "slots": ["25:00"]})


def test_network_errors_are_retried_with_growing_pauses_then_given_up(connected):
    service, clock, server, sessions, account_id = connected
    server.fail["create_text"] = [ThreadsError("network", "Нет связи")] * 5
    post_id = schedule(service, account_id, "Без сети", START + timedelta(minutes=1))
    run(service, clock, minutes=1)
    post = post_row(sessions, post_id)
    assert post.status == "scheduled" and post.attempts == 1
    assert post.next_attempt_at.replace(tzinfo=timezone.utc) == clock() + timedelta(seconds=60)
    run(service, clock, minutes=0.5)
    assert server.calls.count("create_text") == 1
    for minutes in (1, 5, 15, 60):
        run(service, clock, minutes=minutes)
    post = post_row(sessions, post_id)
    assert post.status == "failed" and post.attempts == 5 and server.posts == []
    assert any(notice["kind"] == "failed" for notice in service.shell()["notices"])


def test_an_expired_token_asks_to_reconnect_and_keeps_the_post(connected):
    service, clock, server, sessions, account_id = connected
    server.tokens.clear()
    post_id = schedule(service, account_id, "После входа", START + timedelta(minutes=1))
    run(service, clock, minutes=1)
    post = post_row(sessions, post_id)
    assert post.status == "scheduled" and post.attempts == 0
    account = service.state()["accounts"][0]
    assert account["status"] == "reauth"
    assert service.shell()["notices"][0]["kind"] == "reauth"
    run(service, clock, minutes=1)
    assert server.calls.count("create_text") == 1
    server.tokens["long-1" + "-" * 20] = "u1"
    connect(service)
    run(service, clock, minutes=1)
    assert post_row(sessions, post_id).status == "published"


def test_pauses_hold_the_queue_but_publish_now_goes_out(connected):
    service, clock, server, sessions, account_id = connected
    service.call("threads.queue_control", {"paused": True})
    assert service.shell()["paused"] is True
    post_id = schedule(service, account_id, "Ждёт", START + timedelta(minutes=1))
    run(service, clock, minutes=2)
    assert server.posts == []
    now_id = service.call("threads.post_publish_now", {"account_id": account_id, "text": "Сразу"})[
        "saved"
    ]
    run(service, clock)
    assert post_row(sessions, now_id).status == "published"
    assert post_row(sessions, post_id).status == "scheduled"
    service.call("threads.queue_control", {"paused": False})
    service.call("threads.account_control", {"id": account_id, "action": "pause"})
    run(service, clock, minutes=1)
    assert post_row(sessions, post_id).status == "scheduled"
    service.call("threads.account_control", {"id": account_id, "action": "resume"})
    run(service, clock, minutes=1)
    assert post_row(sessions, post_id).status == "published"


def test_tokens_are_refreshed_weekly_and_expiry_needs_reconnection(connected):
    service, clock, server, sessions, account_id = connected
    run(service, clock, minutes=0)
    assert "refresh" not in server.calls
    clock.advance(days=8)
    run(service, clock)
    assert server.calls.count("refresh") == 1
    with sessions() as session:
        account = session.get(ThreadsAccount, account_id)
        assert account.token_expires_at == (clock() + timedelta(days=60)).replace(tzinfo=None)
    clock.advance(days=61)
    run(service, clock)
    assert service.state()["accounts"][0]["status"] == "reauth"


def test_disconnect_forgets_the_token_and_keeps_history(connected):
    service, clock, server, sessions, account_id = connected
    post_id = schedule(service, account_id, "Было", START + timedelta(minutes=1))
    run(service, clock, minutes=1)
    service.call("threads.account_control", {"id": account_id, "action": "disconnect"})
    with sessions() as session:
        assert session.get(ThreadsAccount, account_id).token_box is None
    assert post_row(sessions, post_id).status == "published"
    with pytest.raises(UserError):
        schedule(service, account_id, "Нельзя", START + timedelta(hours=1))


def test_drafts_review_and_cancel(connected):
    service, clock, server, sessions, account_id = connected
    draft = service.call("threads.post_save", {"text": "Черновик"})["saved"]
    assert post_row(sessions, draft).status == "draft"
    with pytest.raises(UserError):
        service.call("threads.post_schedule", {"id": draft, "at": START.isoformat()})  # no account
    with pytest.raises(UserError):
        service.call(
            "threads.post_schedule",
            {"id": draft, "account_id": account_id, "at": (START - timedelta(hours=1)).isoformat()},
        )
    service.call("threads.post_cancel", {"id": draft})
    service.call("threads.post_delete", {"id": draft})
    with sessions() as session:
        assert session.get(ThreadsPost, draft) is None
    # An unverifiable publication goes to review after hours of failed checks.
    server.lose = 1
    post_id = schedule(service, account_id, "Не проверить", START + timedelta(minutes=1))
    run(service, clock, minutes=1)
    server.fail["recent"] = [ThreadsError("network", "Нет связи")] * 200
    for _ in range(80):
        run(service, clock, minutes=5)
    assert post_row(sessions, post_id).status == "review"
    service.call(
        "threads.post_resolve",
        {"id": post_id, "published": True, "permalink": "https://www.threads.net/@b/post/x"},
    )
    post = post_row(sessions, post_id)
    assert post.status == "published" and post.permalink.endswith("/x")
    assert len(server.posts) == 1


def test_schema_29_and_the_core_route(tmp_path):
    from artist_lead_finder.service import ApplicationService

    engine, sessions = open_database(tmp_path / "db.sqlite3")
    try:
        with sessions() as session:
            assert session.get(SchemaMigration, 29) is not None
        service = ApplicationService(sessions, tmp_path / "data")
        try:
            state = service.call("threads.state", {})
            assert state["accounts"] == [] and state["paused"] is False
            with pytest.raises(UserError):
                service.call("threads.nope", {})
        finally:
            service.shutdown()
    finally:
        engine.dispose()


def test_open_posts_survive_a_restart_of_the_core(tmp_path, monkeypatch):
    engine, sessions = open_database(tmp_path / "db.sqlite3")
    clock = Clock()
    server = FakeThreads(clock)
    server.tokens["tok" + "-" * 20] = "u1"
    make = lambda: ThreadsService(sessions, now=clock, client=server.client)  # noqa: E731
    first = make()
    monkeypatch.setattr(first, "_ensure_thread", lambda: None)
    first.call("threads.token_add", {"token": "tok" + "-" * 20})
    account_id = first.state()["accounts"][0]["id"]
    draft = first.call("threads.post_save", {"text": "Сохранится"})["saved"]
    planned = schedule(first, account_id, "По расписанию", START + timedelta(hours=1))
    first.shutdown()
    second = make()
    monkeypatch.setattr(second, "_ensure_thread", lambda: None)
    second.restore()
    posts = {post["id"]: post for post in second.state()["posts"]}
    assert posts[draft]["status"] == "draft" and posts[planned]["status"] == "scheduled"
    second.shutdown()
    engine.dispose()
    assert threads_service.GRACE == timedelta(minutes=10)
    assert select  # imported for the queries above
