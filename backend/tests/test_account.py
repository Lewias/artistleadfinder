"""Sign-in, access keys, the lock without a server and per-account data (no network:
a loopback fake of the Supabase project)."""

import pytest
from fake_supabase import FakeSupabase

from artist_lead_finder.account.client import AuthError, SupabaseClient
from artist_lead_finder.account.runtime import Runtime
from artist_lead_finder.account.session import CHECK_SECONDS, AccountService
from artist_lead_finder.errors import UserError

KEY = "ALF-AAAAA-BBBBB-CCCCC-DDDDD"


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


class FakeImessage:
    def __init__(self):
        self.restored = self.stopped = 0

    def restore(self):
        self.restored += 1

    def shutdown(self):
        self.stopped += 1


class FakeService:
    def __init__(self, folder):
        self.folder = folder
        self.imessage = FakeImessage()
        self.closed = False

    def call(self, method, params):
        return {"method": method, "folder": self.folder}


@pytest.fixture
def server():
    fake = FakeSupabase()
    yield fake
    fake.close()


def identity(data, decrypt=False):
    return data


def make(server, tmp_path, clock=None):
    account = AccountService(
        tmp_path, SupabaseClient(server.url, "anon"), clock=clock or Clock(), protect=identity
    )
    opened = []

    def open_service(folder):
        service = FakeService(folder)
        opened.append(service)
        return service, lambda: setattr(service, "closed", True)

    runtime = Runtime(tmp_path, account, open_service, lambda: {"version": "test"})
    return account, runtime, opened


def test_without_a_project_the_app_works_as_before(tmp_path):
    account = AccountService(tmp_path, None, protect=identity)
    opened = []
    runtime = Runtime(
        tmp_path,
        account,
        lambda folder: (opened.append(folder) or FakeService(folder), lambda: None),
        lambda: {},
    )
    runtime.start()
    assert account.ready and opened == [tmp_path]
    assert runtime.call("dashboard.get", {})["folder"] == tmp_path


def test_wrong_password_and_nothing_before_sign_in(server, tmp_path):
    server.add_user("artist@example.com")
    account, runtime, opened = make(server, tmp_path)
    runtime.start()
    with pytest.raises(AuthError, match="Неверный email или пароль"):
        runtime.call("account.login", {"email": "artist@example.com", "password": "nope"})
    with pytest.raises(UserError, match="Войдите в аккаунт"):
        runtime.call("dashboard.get", {})
    assert opened == []
    assert runtime.call("system.info", {})["account"]["signed_in"] is False


def test_key_opens_the_account_on_any_computer(server, tmp_path):
    user = server.add_user("artist@example.com", name="Артист")
    server.add_key(KEY)
    account, runtime, opened = make(server, tmp_path)
    runtime.start()
    state = runtime.call(
        "account.login", {"email": "Artist@Example.com", "password": "secret-pass"}
    )
    assert state["signed_in"] and not state["licensed"] and not state["ready"]
    with pytest.raises(UserError, match="ключ"):
        runtime.call("dashboard.get", {})
    state = runtime.call("account.activate", {"key": KEY})
    assert state["ready"] and state["user"] == {
        "id": user,
        "email": "artist@example.com",
        "display_name": "Артист",
        "role": "user",
    }
    assert runtime.call("dashboard.get", {})["folder"] == tmp_path / "accounts" / user
    # Another computer: the account signs in and works without the key.
    _, other_runtime, _ = make(server, tmp_path / "other")
    other_runtime.start()
    state = other_runtime.call(
        "account.login", {"email": "artist@example.com", "password": "secret-pass"}
    )
    assert state["ready"]
    # Another account cannot take the same key.
    server.add_user("friend@example.com")
    _, friend_runtime, _ = make(server, tmp_path / "friend")
    friend_runtime.start()
    friend_runtime.call("account.login", {"email": "friend@example.com", "password": "secret-pass"})
    with pytest.raises(UserError, match="другим аккаунтом"):
        friend_runtime.call("account.activate", {"key": KEY})


def test_sign_up_then_a_key_opens_the_app(server, tmp_path):
    server.add_key(KEY)
    account, runtime, opened = make(server, tmp_path)
    runtime.start()
    with pytest.raises(UserError, match="короче 8"):
        runtime.call(
            "account.register", {"name": "Новый", "email": "new@example.com", "password": "short"}
        )
    with pytest.raises(UserError, match="имя"):
        runtime.call(
            "account.register", {"name": " ", "email": "new@example.com", "password": "long-pass"}
        )
    state = runtime.call(
        "account.register",
        {"name": "  Новый  артист ", "email": "New@Example.com", "password": "long-password"},
    )
    assert state["signed_in"] and not state["ready"]
    assert state["user"]["display_name"] == "Новый артист"
    assert state["user"]["email"] == "new@example.com"
    with pytest.raises(UserError, match="ключ"):
        runtime.call("dashboard.get", {})
    assert runtime.call("account.activate", {"key": KEY})["ready"]
    assert opened and opened[0].folder == tmp_path / "accounts" / state["user"]["id"]
    # The same email again is refused with a hint to sign in.
    _, other_runtime, _ = make(server, tmp_path / "other")
    other_runtime.start()
    with pytest.raises(UserError, match="уже есть"):
        other_runtime.call(
            "account.register",
            {"name": "Ещё", "email": "new@example.com", "password": "long-password"},
        )


def signed_in(server, tmp_path, clock=None, role="user"):
    user = server.add_user(f"{role}@example.com", role=role)
    server.add_key(KEY, user)
    account, runtime, opened = make(server, tmp_path, clock)
    runtime.start()
    runtime.call("account.login", {"email": f"{role}@example.com", "password": "secret-pass"})
    runtime.call("account.activate", {"key": KEY})
    return user, account, runtime, opened


def test_first_account_takes_the_data_of_the_old_version(server, tmp_path):
    (tmp_path / "artist-leads.sqlite3").write_text("old base")
    (tmp_path / "browser-sessions").mkdir()
    (tmp_path / "browser-sessions" / "a.session").write_text("cookies")
    user, _, _, opened = signed_in(server, tmp_path)
    folder = tmp_path / "accounts" / user
    assert opened[0].folder == folder
    assert (folder / "artist-leads.sqlite3").read_text() == "old base"
    assert (folder / "browser-sessions" / "a.session").exists()
    assert not (tmp_path / "artist-leads.sqlite3").exists()


def test_no_server_locks_the_app_until_it_answers(server, tmp_path):
    clock = Clock()
    _, account, runtime, opened = signed_in(server, tmp_path, clock)
    server.down = True
    clock.value += CHECK_SECONDS + 1
    runtime.call("dashboard.get", {})  # one failed check is tolerated
    clock.value += 31
    with pytest.raises(UserError, match="Нет связи с сервером"):
        runtime.call("dashboard.get", {})
    assert opened[0].imessage.stopped == 1
    assert runtime.call("account.state", {})["online"] is False
    server.down = False
    clock.value += 31
    assert runtime.call("account.state", {})["ready"] is True
    assert opened[0].imessage.restored == 1
    assert runtime.call("dashboard.get", {})["method"] == "dashboard.get"


def test_blocked_or_revoked_ends_the_session(server, tmp_path):
    clock = Clock()
    user, account, runtime, opened = signed_in(server, tmp_path, clock)
    server.keys[KEY]["revoked"] = True
    clock.value += CHECK_SECONDS + 1
    with pytest.raises(UserError, match="ключ"):
        runtime.call("dashboard.get", {})
    server.keys[KEY]["revoked"] = False
    server.users[user]["blocked"] = True
    clock.value += CHECK_SECONDS + 1
    with pytest.raises(UserError, match="Войдите"):
        runtime.call("dashboard.get", {})
    state = runtime.call("account.state", {})
    assert state["signed_in"] is False and "заблокирован" in state["reason"]
    assert opened[0].closed is True


def test_session_survives_a_restart_and_logout_closes_the_data(server, tmp_path):
    user, account, runtime, opened = signed_in(server, tmp_path)
    runtime.shutdown()
    assert opened[0].closed
    again, runtime2, opened2 = make(server, tmp_path)
    runtime2.start()
    assert again.ready and again.device_id == account.device_id
    assert opened2[0].folder == tmp_path / "accounts" / user
    state = runtime2.call("account.logout", {})
    assert state["signed_in"] is False and opened2[0].closed
    assert not (tmp_path / "account.session").exists()


# ---------- admin ----------


def test_admin_creates_a_user_with_a_key_who_can_then_sign_in(server, tmp_path):
    _, account, runtime, _ = signed_in(server, tmp_path / "admin", role="admin")
    created = runtime.call(
        "admin.create_user",
        {
            "email": "New@Example.com",
            "password": "long-password",
            "display_name": "Новый",
            "issue_key": True,
        },
    )
    assert created["user"]["email"] == "new@example.com" and created["key"].startswith("ALF-")
    overview = runtime.call("admin.overview", {})
    new = next(user for user in overview["users"] if user["email"] == "new@example.com")
    assert any(key["user_id"] == new["id"] and not key["bound"] for key in overview["keys"])
    # The new user signs in on their own computer with that key.
    _, user_runtime, _ = make(server, tmp_path / "new")
    user_runtime.start()
    user_runtime.call("account.login", {"email": "new@example.com", "password": "long-password"})
    assert user_runtime.call("account.activate", {"key": created["key"]})["ready"]
    # Unbind takes the access away from the account; revoke ends the key.
    key_id = next(
        key["id"]
        for key in runtime.call("admin.overview", {})["keys"]
        if key["user_id"] == new["id"]
    )
    assert not next(
        k for k in runtime.call("admin.unbind_key", {"id": key_id})["keys"] if k["id"] == key_id
    )["bound"]
    runtime.call("admin.revoke_key", {"id": key_id})
    assert next(k for k in runtime.call("admin.overview", {})["keys"] if k["id"] == key_id)[
        "revoked_at"
    ]


def test_users_cannot_use_admin_actions_and_admin_cannot_demote_self(server, tmp_path):
    me, account, runtime, _ = signed_in(server, tmp_path / "admin", role="admin")
    with pytest.raises(UserError, match="Свою роль"):
        runtime.call("admin.set_role", {"user_id": me, "role": "user"})
    other = server.add_user("plain@example.com")
    runtime.call("admin.set_blocked", {"user_id": other, "blocked": True})
    assert server.users[other]["blocked"] is True
    _, _, user_runtime, _ = signed_in(server, tmp_path / "user")
    with pytest.raises(UserError, match="только админу"):
        user_runtime.call("admin.overview", {})


def test_admin_makes_moderators_but_never_another_admin(server, tmp_path):
    me, account, runtime, _ = signed_in(server, tmp_path / "admin", role="admin")
    plain = server.add_user("plain@example.com")
    runtime.call("admin.set_role", {"user_id": plain, "role": "moderator"})
    assert server.users[plain]["role"] == "moderator"
    with pytest.raises(UserError, match="пользователя или модератора"):
        runtime.call("admin.set_role", {"user_id": plain, "role": "admin"})
    _, moderator, moderator_runtime, _ = signed_in(server, tmp_path / "mod", role="moderator")
    assert moderator.sees_all_crm and not moderator.is_admin
    with pytest.raises(UserError, match="только админу"):
        moderator_runtime.call("admin.overview", {})


def test_key_grants_its_role_on_activation(server, tmp_path):
    _, _, admin_runtime, _ = signed_in(server, tmp_path / "admin", role="admin")
    key = admin_runtime.call("admin.issue_key", {"note": "модер", "role": "moderator"})["key"]
    with pytest.raises(UserError, match="пользователя или модератора"):
        admin_runtime.call("admin.issue_key", {"role": "admin"})
    server.add_user("mod@example.com")
    account, runtime, _ = make(server, tmp_path / "mod")
    runtime.start()
    runtime.call("account.login", {"email": "mod@example.com", "password": "secret-pass"})
    state = runtime.call("account.activate", {"key": key})
    assert state["ready"] and state["role"] == "moderator" and account.sees_all_crm
    overview = admin_runtime.call("admin.overview", {})
    assert {k["role"] for k in overview["keys"] if k["note"] == "модер"} == {"moderator"}
