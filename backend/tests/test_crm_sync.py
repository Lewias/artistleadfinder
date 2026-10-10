"""The shared CRM: local copies of a user and an admin sync through a loopback fake of
the Supabase project (no network)."""

import pytest
from fake_supabase import FakeSupabase
from sqlalchemy import select

from artist_lead_finder.account.client import SupabaseClient
from artist_lead_finder.account.session import AccountService
from artist_lead_finder.crm.service import CrmService
from artist_lead_finder.crm.sync import CrmSync
from artist_lead_finder.database import open_database
from artist_lead_finder.models import CrmContact, CrmTombstone


@pytest.fixture
def server():
    fake = FakeSupabase()
    yield fake
    fake.close()


class Copy:
    """One computer: its own database, account and sync."""

    def __init__(self, server, folder, email, key):
        folder.mkdir(parents=True, exist_ok=True)
        self.engine, self.sessions = open_database(folder / "db.sqlite3")
        self.account = AccountService(
            folder, SupabaseClient(server.url, "anon"), protect=lambda data, decrypt=False: data
        )
        self.account.login({"email": email, "password": "secret-pass"})
        self.account.activate({"key": key})
        self.crm = CrmService(self.sessions, lambda values: len(values), lambda values: len(values))
        self.crm.viewer = lambda: (self.account.user_id, self.account.sees_all_crm)
        self.sync = CrmSync(self.sessions, self.account)

    def contacts(self, **params):
        return self.crm.contacts({"crm": "instagram", **params})["items"]

    def close(self):
        self.engine.dispose()


@pytest.fixture
def people(server, tmp_path):
    ids = {
        "artist": server.add_user("artist@example.com", name="Артист"),
        "other": server.add_user("other@example.com", name="Другой"),
        "admin": server.add_user("admin@example.com", role="admin", name="Админ"),
        "moderator": server.add_user("moderator@example.com", role="moderator", name="Модер"),
    }
    copies = {}
    for name, user in ids.items():
        server.add_key(f"KEY-{name.upper()}-0000", user)
        copies[name] = Copy(
            server, tmp_path / name, f"{name}@example.com", f"KEY-{name.upper()}-0000"
        )
    yield ids, copies
    for copy in copies.values():
        copy.close()


def add(copy, name, username, **extra):
    return copy.crm.save(
        {
            "crm": "instagram",
            "name": name,
            "channels": [{"kind": "instagram", "value": username}],
            **extra,
        }
    )


def test_user_contacts_reach_the_admin_with_the_owner(server, people):
    ids, copies = people
    add(copies["artist"], "Jay", "jay_music", notes="позвонить")
    add(copies["other"], "Kid", "kid_beats")
    assert copies["artist"].sync.run_once() and copies["other"].sync.run_once()
    with copies["artist"].sessions() as session:
        row = session.scalar(select(CrmContact))
        assert row.remote_id and row.dirty is False and row.owner_id == ids["artist"]
    # Each user gets only their own CRM back.
    copies["artist"].sync.run_once()
    assert [c["name"] for c in copies["artist"].contacts()] == ["Jay"]
    # The admin gets everyone's, signed with the owner.
    copies["admin"].sync.run_once()
    items = copies["admin"].contacts(owner="all")
    assert sorted((c["name"], c["owner_name"], c["mine"]) for c in items) == [
        ("Jay", "Артист", False),
        ("Kid", "Другой", False),
    ]
    assert copies["admin"].contacts(owner=ids["artist"])[0]["name"] == "Jay"
    assert copies["admin"].contacts(owner="mine") == []
    # Without a choice even the admin sees only their own CRM.
    assert copies["admin"].contacts() == []
    owners = copies["admin"].crm.contacts({"crm": "instagram"})["owners"]
    assert {(o["name"], o["count"]) for o in owners} == {("Артист", 1), ("Другой", 1)}


def test_admin_edit_comes_back_to_the_owner(server, people):
    ids, copies = people
    add(copies["artist"], "Jay", "jay_music")
    copies["artist"].sync.run_once()
    copies["admin"].sync.run_once()
    [contact] = copies["admin"].contacts(owner="all")
    copies["admin"].crm.save(
        {**contact, "crm": "instagram", "notes": "Сделка закрыта", "statuses": ["Сделка"]}
    )
    copies["admin"].sync.run_once()
    row = server.tables["crm_contacts"][0]
    assert row["owner_id"] == ids["artist"] and row["updated_by"] == ids["admin"]
    copies["artist"].sync.run_once()
    [mine] = copies["artist"].contacts()
    assert mine["notes"] == "Сделка закрыта" and mine["statuses"] == ["Сделка"]


def test_moderator_sees_and_edits_every_crm(server, people):
    ids, copies = people
    add(copies["artist"], "Jay", "jay_music")
    add(copies["other"], "Kid", "kid_beats")
    copies["artist"].sync.run_once()
    copies["other"].sync.run_once()
    copies["moderator"].sync.run_once()
    items = copies["moderator"].contacts(owner="all")
    assert sorted((c["name"], c["owner_name"]) for c in items) == [
        ("Jay", "Артист"),
        ("Kid", "Другой"),
    ]
    jay = next(c for c in items if c["name"] == "Jay")
    copies["moderator"].crm.save({**jay, "crm": "instagram", "notes": "Проверено"})
    copies["moderator"].sync.run_once()
    copies["artist"].sync.run_once()
    assert copies["artist"].contacts()[0]["notes"] == "Проверено"
    # A plain user still gets only their own CRM.
    assert [c["name"] for c in copies["other"].contacts()] == ["Kid"]


def test_unsent_local_change_is_not_overwritten(server, people):
    _, copies = people
    add(copies["artist"], "Jay", "jay_music")
    copies["artist"].sync.run_once()
    copies["admin"].sync.run_once()
    [theirs] = copies["admin"].contacts(owner="all")
    copies["admin"].crm.save({**theirs, "crm": "instagram", "notes": "от админа"})
    copies["admin"].sync.run_once()
    [mine] = copies["artist"].contacts()
    copies["artist"].crm.save({**mine, "crm": "instagram", "notes": "моё, ещё не отправлено"})
    # Pull only: the waiting local edit stays and goes out on the next push.
    copies["artist"].sync._pull_contacts(
        copies["artist"].account.client, copies["artist"].account.token()
    )
    assert copies["artist"].contacts()[0]["notes"] == "моё, ещё не отправлено"
    copies["artist"].sync.run_once()
    assert server.tables["crm_contacts"][0]["notes"] == "моё, ещё не отправлено"


def test_trash_and_purge_reach_every_copy(server, people):
    _, copies = people
    first = add(copies["artist"], "Jay", "jay_music")
    add(copies["artist"], "Kid", "kid_beats")
    copies["artist"].sync.run_once()
    copies["admin"].sync.run_once()
    copies["artist"].crm.trash({"crm": "instagram", "ids": [first["id"]]})
    copies["artist"].sync.run_once()
    copies["admin"].sync.run_once()
    assert len(copies["admin"].contacts(tab="trash", owner="all")) == 1
    copies["artist"].crm.purge({"crm": "instagram"})
    with copies["artist"].sessions() as session:
        assert session.scalar(select(CrmTombstone)) is not None
    copies["artist"].sync.run_once()
    with copies["artist"].sessions() as session:
        assert session.scalar(select(CrmTombstone)) is None
    assert sum(row.get("purged", False) for row in server.tables["crm_contacts"]) == 1
    copies["admin"].sync.run_once()
    assert copies["admin"].contacts(tab="trash", owner="all") == []
    assert [c["name"] for c in copies["admin"].contacts(owner="all")] == ["Kid"]


def test_statuses_follow_the_user_to_a_new_computer(server, people, tmp_path):
    ids, copies = people
    copies["artist"].crm.statuses_save(
        {"crm": "instagram", "statuses": [{"label": "Горячий", "color": "red", "emoji": "🔥"}]}
    )
    copies["artist"].sync.run_once()
    sets = server.tables["crm_status_sets"]
    assert [(row["crm"], row["items"]) for row in sets] == [
        ("instagram", [{"label": "Горячий", "color": "red", "emoji": "🔥"}])
    ]
    # A new computer seeds the defaults, then takes the list from the server.
    server.keys["KEY-ARTIST-0000"]["device_id"] = None
    fresh = Copy(server, tmp_path / "laptop", "artist@example.com", "KEY-ARTIST-0000")
    try:
        fresh.crm.contacts({"crm": "instagram"})
        fresh.sync.run_once()
        statuses = fresh.crm.contacts({"crm": "instagram"})["statuses"]
        assert statuses == [{"label": "Горячий", "color": "red", "emoji": "🔥"}]
    finally:
        fresh.close()


def test_admin_writes_and_imports_only_with_own_crm(server, people):
    ids, copies = people
    add(copies["artist"], "Jay", "jay_music")
    copies["artist"].sync.run_once()
    copies["admin"].sync.run_once()
    [theirs] = copies["admin"].contacts(owner="all")
    with pytest.raises(Exception, match="нет Instagram"):
        copies["admin"].crm.write({"crm": "instagram", "ids": [theirs["id"]]})
    mine = add(copies["admin"], "Own", "own_artist")
    assert mine["mine"] is True
    assert copies["admin"].crm.write({"crm": "instagram", "ids": [mine["id"]]})["added"] == 1
    # The same Instagram as a user's contact is a new contact of the admin's own CRM.
    duplicate = add(copies["admin"], "Jay too", "jay_music")
    assert duplicate["mine"] is True


def test_cloud_parser_leads_arrive_with_what_it_found_and_are_never_sent_back(server, people):
    ids, copies = people
    artist = copies["artist"]
    # The cloud worker wrote this contact on the server.
    server.tables["crm_contacts"].append(
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "owner_id": ids["artist"],
            "crm": "instagram",
            "name": "Lil Wave",
            "statuses": ["Артист"],
            "channels": [{"kind": "instagram", "value": "lil.wave"}],
            "notes": "",
            "source": "Cloud Parser",
            "cloud": {"category": "ARTIST", "confidence": 88, "reason": "Выпускает треки."},
            "instagram_id": "123",
            "earned": 0,
            "potential": 0,
            "updated_at": "2030-01-01T00:00:00+00:00",
        }
    )
    assert artist.sync.run_once()
    [contact] = artist.contacts()
    assert contact["source"] == "Cloud Parser" and contact["cloud"]["confidence"] == 88
    # The user's edit goes to the server without the parser's fields.
    artist.crm.save({**contact, "crm": "instagram", "notes": "написать в пятницу"})
    assert artist.sync.run_once()
    row = server.tables["crm_contacts"][0]
    assert row["notes"] == "написать в пятницу"
    assert row["cloud"]["confidence"] == 88 and row["source"] == "Cloud Parser"
