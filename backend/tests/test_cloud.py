"""«Облачный парсер» in the app: the session and Scout settings go to the server with the
job; jobs are read and cancelled with the user's session (a loopback fake server here)."""

import pytest
from fake_supabase import FakeSupabase

from artist_lead_finder.account.client import SupabaseClient
from artist_lead_finder.account.runtime import Runtime
from artist_lead_finder.account.session import AccountService
from artist_lead_finder.cloud import job_params
from artist_lead_finder.errors import UserError

PROFILE = "a" * 32
NO_PROXY = "d" * 32
SESSION = {
    "name": "Рабочий",
    "cookies": [{"name": "sessionid", "value": "s1"}, {"name": "csrftoken", "value": "c"}],
    "proxy": {"scheme": "http", "host": "1.2.3.4", "port": 8000, "password": "p"},
}
FORM = {
    "profile_id": PROFILE,
    "sources": "rapgoat.tv\nhttps://www.instagram.com/topdailyrap/\n",
    "categories": ["ARTIST", "PRODUCER"],
    "target": 40,
}


class LocalCore:
    """The app's own core: browser profiles and Scout settings."""

    def __init__(self, folder):
        self.folder = folder
        self.records = {
            PROFILE: SESSION,
            "b" * 32: {"name": "Без входа", "cookies": []},
            NO_PROXY: {**SESSION, "name": "Без прокси", "proxy": None},
        }
        self.workspace = {
            "usernames": [
                {"username": "jay.carter", "status": "new"},
                {"username": "written.before", "status": "sent"},
                {"username": "kid.vibes", "status": "new"},
            ],
            "messages": ["Привет, {{username}}!"],
        }
        self.imessage = type(
            "I", (), {"restore": lambda self: None, "shutdown": lambda self: None}
        )()

    def call(self, method, params):
        if method == "browser.load_internal":
            return self.records[params["id"]]
        if method == "settings.get":
            return {"scout_methods": ["posts"], "scout_min_followers": 500, "outreach_x": 1}
        if method == "outreach.workspace":
            return self.workspace
        return {}


@pytest.fixture
def server():
    fake = FakeSupabase()
    yield fake
    fake.close()


def signed_in(server, tmp_path, email):
    user = server.add_user(email)
    server.add_key(f"KEY-{email}", user)
    account = AccountService(
        tmp_path / email, SupabaseClient(server.url, "anon"), protect=lambda d, decrypt=False: d
    )
    runtime = Runtime(
        tmp_path / email,
        account,
        lambda folder: (LocalCore(folder), lambda: None),
        lambda: {"version": "test"},
    )
    runtime.start()
    runtime.call("account.login", {"email": email, "password": "secret-pass"})
    runtime.call("account.activate", {"key": f"KEY-{email}"})
    return user, runtime


def test_a_job_takes_the_session_and_settings_and_is_read_back(server, tmp_path):
    user, runtime = signed_in(server, tmp_path, "a@example.com")
    started = runtime.call("cloud.start", {**FORM, "request_id": "click-0001"})
    job = started["job"]
    assert job["stage"] == "queued" and started["items"] == []
    assert job["params"]["sources"] == [
        "https://www.instagram.com/rapgoat.tv/",
        "https://www.instagram.com/topdailyrap/",
    ]
    # Only the Scout settings travel; the session with its proxy is on the server.
    assert job["params"]["settings"] == {"scout_methods": ["posts"], "scout_min_followers": 500}
    sent = server.cloud_sessions[(user, PROFILE)]
    assert sent["name"] == "Рабочий" and sent["record"]["proxy"]["host"] == "1.2.3.4"
    assert runtime.call("cloud.sessions", {})[0]["has_proxy"] is True
    # The same click sent again is the same job.
    assert (
        runtime.call("cloud.start", {**FORM, "request_id": "click-0001"})["job"]["id"] == job["id"]
    )

    _, other = signed_in(server, tmp_path, "b@example.com")
    assert other.call("cloud.jobs", {}) == []
    with pytest.raises(UserError, match="не найдена"):
        other.call("cloud.cancel", {"id": job["id"]})
    assert runtime.call("cloud.cancel", {"id": job["id"]})["job"]["stage"] == "cancelled"


def test_a_profile_without_instagram_login_is_not_sent(server, tmp_path):
    _, runtime = signed_in(server, tmp_path, "a@example.com")
    with pytest.raises(UserError, match="нет входа"):
        runtime.call("cloud.start", {**FORM, "profile_id": "b" * 32})
    assert server.cloud_sessions == {}


def test_the_server_limit_on_running_jobs_reaches_the_user(server, tmp_path):
    _, runtime = signed_in(server, tmp_path, "a@example.com")
    runtime.call("cloud.start", FORM)
    runtime.call("cloud.start", FORM)
    with pytest.raises(UserError, match="Уже идёт"):
        runtime.call("cloud.start", FORM)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"profile_id": "x"}, "аккаунт"),
        ({"sources": " \n"}, "источник"),
        ({"categories": ["SPAM"]}, "категории"),
        ({"target": 0}, "от 1 до 500"),
    ],
)
def test_the_form_is_checked_before_it_is_sent(change, message):
    with pytest.raises(UserError, match=message):
        job_params({**FORM, **change})


def test_sources_are_taken_as_typed_and_sent_as_profile_links():
    typed = "saycheesetv\n@XXL\ninstagram.com/hiphopdx\nhttp://www.instagram.com/vibe/\nxxl"
    assert job_params({**FORM, "sources": typed})["sources"] == [
        "https://www.instagram.com/saycheesetv/",
        "https://www.instagram.com/xxl/",
        "https://www.instagram.com/hiphopdx/",
        "https://www.instagram.com/vibe/",
    ]
    with pytest.raises(UserError, match="не профили Instagram: explore, a b"):
        job_params({**FORM, "sources": "rap\nexplore\na b"})


def test_up_to_500_sources_go_in_one_job():
    many = "\n".join(f"source{index}" for index in range(500))
    assert len(job_params({**FORM, "sources": many})["sources"]) == 500
    with pytest.raises(UserError, match="Не больше 500"):
        job_params({**FORM, "sources": many + "\nextra"})


def test_an_outreach_takes_the_list_the_messages_and_the_pace(server, tmp_path):
    user, runtime = signed_in(server, tmp_path, "a@example.com")
    assert runtime.call("cloud.outreach_sources", {}) == {"list": 2, "found": 0, "messages": 1}
    started = runtime.call(
        "cloud.outreach_start",
        {"profile_id": PROFILE, "source": "list", "limit": 10, "request_id": "click-0002"},
    )
    job = started["job"]
    assert job["kind"] == "outreach" and job["stage"] == "queued"
    # Only names nobody wrote to; the messages and the outreach pace of the app.
    assert job["params"]["usernames"] == ["jay.carter", "kid.vibes"]
    assert job["params"]["messages"] == ["Привет, {{username}}!"]
    assert job["params"]["settings"] == {"outreach_x": 1}
    assert server.cloud_sessions[(user, PROFILE)]["record"]["proxy"]["host"] == "1.2.3.4"
    with pytest.raises(UserError, match="рассылка уже идёт"):
        runtime.call("cloud.outreach_start", {"profile_id": PROFILE, "source": "list"})


def test_an_outreach_writes_to_the_cloud_parsers_new_leads(server, tmp_path):
    user, runtime = signed_in(server, tmp_path, "a@example.com")
    scout = runtime.call("cloud.start", FORM)["job"]["id"]
    for name, outcome in (("jay.carter", "added"), ("old.friend", "updated"), ("promo", "added")):
        server.cloud["cloud_candidates"].append(
            {
                "id": len(server.cloud["cloud_candidates"]) + 1,
                "job_id": scout,
                "owner_id": user,
                "username": name,
                "outcome": outcome,
            }
        )
    assert runtime.call("cloud.outreach_sources", {})["found"] == 2
    job = runtime.call(
        "cloud.outreach_start", {"profile_id": PROFILE, "source": "found", "limit": 1}
    )["job"]
    assert job["params"]["usernames"] == ["promo"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"profile_id": NO_PROXY}, "нет прокси"),
        ({"profile_id": "b" * 32}, "нет входа"),
        ({"source": "found"}, "которым ещё не писали"),
        ({"source": "elsewhere"}, "кому писать"),
        ({"limit": 501}, "от 1 до 500"),
    ],
)
def test_an_outreach_is_checked_before_it_is_sent(server, tmp_path, change, message):
    _, runtime = signed_in(server, tmp_path, "a@example.com")
    with pytest.raises(UserError, match=message):
        runtime.call("cloud.outreach_start", {"profile_id": PROFILE, "source": "list", **change})
    assert not server.cloud["cloud_jobs"]


def test_an_outreach_needs_messages(server, tmp_path):
    _, runtime = signed_in(server, tmp_path, "a@example.com")
    runtime.service.workspace["messages"] = []
    with pytest.raises(UserError, match="Нет сообщений"):
        runtime.call("cloud.outreach_start", {"profile_id": PROFILE, "source": "list"})


def test_telegram_is_linked_with_a_code_and_unlinked(server, tmp_path):
    user, runtime = signed_in(server, tmp_path, "a@example.com")
    assert runtime.call("cloud.telegram", {})["linked"] is False
    started = runtime.call("cloud.telegram_link", {})
    assert started["url"] == f"https://t.me/alf_test_bot?start={started['code']}"
    server.tg_links[user] = "artist_hunter"
    assert runtime.call("cloud.telegram", {})["username"] == "artist_hunter"
    assert runtime.call("cloud.telegram_unlink", {})["linked"] is False


def test_without_the_account_server_there_is_no_cloud(tmp_path):
    account = AccountService(tmp_path, None, protect=lambda data, decrypt=False: data)
    runtime = Runtime(tmp_path, account, lambda folder: (None, lambda: None), lambda: {})
    with pytest.raises(UserError, match="сервер аккаунтов"):
        runtime.call("cloud.jobs", {})
