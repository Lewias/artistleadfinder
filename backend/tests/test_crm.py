"""The Instagram and iMessage CRMs: separate tables, imports between them, files."""

import csv
import zipfile
from datetime import date, datetime

import pytest

from artist_lead_finder.crm import sheets
from artist_lead_finder.crm.service import CrmService, channels_from
from artist_lead_finder.database import open_database
from artist_lead_finder.models import Conversation, Lead, LeadScoutProfile

TODAY = date(2026, 10, 5)


@pytest.fixture
def crm(tmp_path):
    engine, sessions = open_database(tmp_path / "db.sqlite3")
    written = {"instagram": [], "imessage": []}

    def add_usernames(names):
        written["instagram"].extend(names)
        return len(names)

    def add_recipients(values):
        written["imessage"].extend(values)
        return len(values)

    service = CrmService(sessions, add_usernames, add_recipients, today=lambda: TODAY)
    yield service, sessions, written
    engine.dispose()


def add_leads(sessions):
    with sessions.begin() as session:
        for index, (phones, emails, blocked) in enumerate(
            [
                (["+15555550111"], [], False),
                ([], ["Sinu@Example.com"], False),
                ([], [], False),
                ([], [], True),
            ]
        ):
            lead = Lead(
                platform="instagram",
                username=f"artist{index}",
                bio=f"Bio {index}",
                do_not_contact=blocked,
            )
            session.add(lead)
            session.flush()
            session.add(
                LeadScoutProfile(
                    lead_id=lead.id,
                    phones=phones,
                    emails=emails,
                    profile_type="artist",
                    source_username="streetgossipmedia",
                    discovery_method="post",
                )
            )
            if index == 1:
                session.add(
                    Conversation(lead_id=lead.id, sender_account_id="a" * 32, status="replied")
                )


def test_import_from_the_parsing_base_merges_on_repeat(crm):
    service, sessions, _ = crm
    add_leads(sessions)
    sources = {item["id"]: item["count"] for item in service.sources({"crm": "instagram"})}
    assert sources == {"leads": 4, "imessage": 0}
    assert service.import_verse({"crm": "instagram", "source": "leads"})["added"] == 4
    listed = service.contacts({"crm": "instagram", "sort": "name", "descending": False})
    assert listed["total"] == 4
    first, second = listed["items"][0], listed["items"][1]
    assert first["channels"][0] == {"kind": "instagram", "value": "artist0"}
    assert first["statuses"] == ["Артист", "streetgossipmedia"]
    assert first["notes"] == "Bio 0"
    assert {"kind": "email", "value": "sinu@example.com"} in second["channels"]
    assert "Ответил" in second["statuses"]
    assert "Не связываться" in listed["items"][3]["statuses"]
    again = service.import_verse({"crm": "instagram", "source": "leads"})
    assert again == {"added": 0, "merged": 4, "skipped": 0}
    assert service.contacts({"crm": "instagram"})["total"] == 4


def test_crms_are_separate_and_import_into_each_other(crm):
    service, sessions, _ = crm
    add_leads(sessions)
    service.import_verse({"crm": "instagram", "source": "leads"})
    assert service.contacts({"crm": "imessage"})["total"] == 0
    # The base is shared: everyone moves to iMessage, a phone or email leads there.
    assert {i["id"]: i["count"] for i in service.sources({"crm": "imessage"})} == {
        "instagram": 4,
        "leads": 4,
    }
    result = service.import_verse({"crm": "imessage", "source": "instagram"})
    assert result == {"added": 4, "merged": 0, "skipped": 0}
    items = service.contacts({"crm": "imessage", "sort": "name", "descending": False})["items"]
    assert items[0]["channels"][0] == {"kind": "phone", "value": "+15555550111"}
    assert items[1]["channels"][0] == {"kind": "email", "value": "sinu@example.com"}
    assert items[2]["channels"] == [{"kind": "instagram", "value": "artist2"}]
    # Edits in one CRM do not touch the other.
    service.save({"crm": "imessage", **items[0], "notes": "Только в iMessage"})
    instagram = service.contacts({"crm": "instagram", "search": "artist0"})["items"][0]
    assert instagram["notes"] == "Bio 0"
    # Back to Instagram: merged by the shared channel, the note is kept there.
    added = service.save(
        {
            "crm": "imessage",
            "name": "Новый",
            "channels": [{"kind": "phone", "value": "+1 555 555 0199"}],
        }
    )
    assert added["channels"] == [{"kind": "phone", "value": "+15555550199"}]
    back = service.import_verse({"crm": "instagram", "source": "imessage"})
    assert back == {"added": 1, "merged": 4, "skipped": 0}


def test_save_validates_and_rejects_duplicates(crm):
    service, *_ = crm
    with pytest.raises(ValueError):
        service.save({"crm": "instagram", "name": "", "channels": []})
    with pytest.raises(ValueError):
        service.save({"crm": "instagram", "channels": [{"kind": "phone", "value": "8 999"}]})
    contact = service.save(
        {
            "crm": "instagram",
            "channels": [{"kind": "instagram", "value": "https://instagram.com/Real.Sinu/"}],
            "earned": "1 200 $",
            "potential": "2,5",
            "next_action_at": "05.10.2026",
            "next_action": "Отправить бит",
        }
    )
    assert contact["name"] == "real.sinu"
    assert contact["earned"] == 1200 and contact["potential"] == 2.5
    assert contact["attention"] is True
    with pytest.raises(ValueError):
        service.save(
            {"crm": "instagram", "channels": [{"kind": "instagram", "value": "@real.sinu"}]}
        )
    with pytest.raises(ValueError):
        service.save({"crm": "imessage", "id": contact["id"], "name": "x"})


def test_tabs_filters_sort_and_totals(crm):
    service, *_ = crm
    for name, next_at, earned in [
        ("b", "2026-10-04", 100),
        ("a", "2026-10-20", 0),
        ("c", None, 50),
    ]:
        service.save(
            {
                "crm": "instagram",
                "name": name,
                "channels": [{"kind": "instagram", "value": name}],
                "next_action_at": next_at,
                "next_action": "Позвонить" if next_at else "",
                "earned": earned,
                "statuses": ["Сделка"] if earned else [],
            }
        )
    result = service.contacts({"crm": "instagram"})
    assert result["counts"] == {"all": 3, "attention": 1, "trash": 0}
    assert result["totals"] == {"earned": 150, "potential": 0}
    assert [
        i["name"] for i in service.contacts({"crm": "instagram", "tab": "attention"})["items"]
    ] == ["b"]
    by_next = service.contacts({"crm": "instagram", "sort": "next", "descending": False})["items"]
    assert [i["name"] for i in by_next] == ["b", "a", "c"]
    planned = service.contacts({"crm": "instagram", "filters": {"next": "planned"}})["items"]
    assert [i["name"] for i in planned] == ["a"]
    deals = service.contacts({"crm": "instagram", "filters": {"statuses": ["Сделка"]}})
    assert deals["total"] == 2
    by_name = {i["name"]: i["id"] for i in result["items"]}
    ids = [by_name["b"], by_name["a"]]
    service.trash({"crm": "instagram", "ids": ids})
    after = service.contacts({"crm": "instagram", "tab": "trash"})
    assert after["counts"] == {"all": 1, "attention": 0, "trash": 2}
    service.restore({"crm": "instagram", "ids": ids[:1]})
    assert service.purge({"crm": "instagram"}) == {"removed": 1}
    assert service.contacts({"crm": "instagram"})["counts"]["trash"] == 0


def test_statuses_are_per_crm_and_removed_from_contacts(crm):
    service, *_ = crm
    contact = service.save(
        {
            "crm": "instagram",
            "name": "x",
            "channels": [{"kind": "instagram", "value": "x"}],
            "statuses": ["Отказ", "Сделка"],
        }
    )
    statuses = service.contacts({"crm": "instagram"})["statuses"]
    assert statuses[0] == {"label": "Артист", "color": "violet"}
    kept = [item for item in statuses if item["label"] != "Отказ"] + [
        {"label": "Тёплый", "color": "pink"}
    ]
    result = service.statuses_save({"crm": "instagram", "statuses": kept})
    assert result["statuses"][-1] == {"label": "Тёплый", "color": "pink"}
    assert service.contacts({"crm": "instagram"})["items"][0]["statuses"] == ["Сделка"]
    # The iMessage CRM keeps its defaults.
    assert any(s["label"] == "Отказ" for s in service.contacts({"crm": "imessage"})["statuses"])
    service.label({"crm": "instagram", "ids": [contact["id"]], "label": "Тёплый"})
    assert service.contacts({"crm": "instagram"})["items"][0]["statuses"] == ["Сделка", "Тёплый"]
    with pytest.raises(ValueError):
        service.statuses_save({"crm": "instagram", "statuses": [{"label": "A"}, {"label": "a"}]})


def test_write_puts_contacts_into_the_channel_list(crm):
    service, sessions, written = crm
    add_leads(sessions)
    service.import_verse({"crm": "instagram", "source": "leads"})
    service.import_verse({"crm": "imessage", "source": "leads"})
    ids = [i["id"] for i in service.contacts({"crm": "instagram"})["items"]]
    result = service.write({"crm": "instagram", "ids": ids})
    # «Не связываться» is left out.
    assert result["added"] == 3
    assert sorted(written["instagram"]) == ["artist0", "artist1", "artist2"]
    ids = [i["id"] for i in service.contacts({"crm": "imessage"})["items"]]
    service.write({"crm": "imessage", "ids": ids})
    assert sorted(written["imessage"]) == ["+15555550111", "sinu@example.com"]
    with pytest.raises(ValueError):
        service.write({"crm": "imessage", "ids": [999]})


def test_export_and_import_files_round_trip(crm, tmp_path):
    service, *_ = crm
    service.save(
        {
            "crm": "instagram",
            "name": "Real Sinu",
            "channels": [
                {"kind": "instagram", "value": "real.sinu"},
                {"kind": "email", "value": "sinubookings@gmail.com"},
            ],
            "statuses": ["Артист", "Интересуется"],
            "notes": "888 ✨ Chicago | Atlanta",
            "next_action": "Написать снова",
            "next_action_at": "2026-10-10",
            "earned": 300,
        }
    )
    for suffix in ("xlsx", "csv"):
        path = tmp_path / f"crm.{suffix}"
        assert service.export({"crm": "instagram", "path": str(path)})["count"] == 1
        rows = sheets.read_table(path)
        assert rows[0][:4] == ["Контакт", "Instagram", "Email", "Телефон"]
        assert rows[1][0] == "Real Sinu" and rows[1][8] == "888 ✨ Chicago | Atlanta"
        result = service.import_file({"crm": "imessage", "path": str(path)})
        assert result["skipped"] == 0
    contact = service.contacts({"crm": "imessage"})["items"][0]
    assert contact["channels"][0] == {"kind": "email", "value": "sinubookings@gmail.com"}
    assert contact["statuses"] == ["Артист", "Интересуется"]
    assert contact["next_action_at"] == "2026-10-10T00:00:00"
    assert contact["earned"] == 300
    assert service.contacts({"crm": "imessage"})["total"] == 1


def test_import_reads_foreign_files(crm, tmp_path):
    service, *_ = crm
    path = tmp_path / "list.csv"
    with path.open("w", encoding="cp1251", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["Имя", "Телефон", "Метки", "Деньги"])
        writer.writerow(["Иван", "+7 999 123-45-67", "Сделка; Тёплый", "1 000"])
        writer.writerow(["Без канала", "", "", ""])
        writer.writerow(["Плохой номер", "12", "", ""])
    result = service.import_file({"crm": "imessage", "path": str(path)})
    assert result == {"added": 1, "merged": 0, "skipped": 2}
    contact = service.contacts({"crm": "imessage"})["items"][0]
    assert contact["channels"] == [{"kind": "phone", "value": "+79991234567"}]
    assert contact["statuses"] == ["Сделка", "Тёплый"] and contact["earned"] == 1000
    # An XLSX written by Excel: shared strings, a number and an Excel date.
    xlsx = tmp_path / "excel.xlsx"
    with zipfile.ZipFile(xlsx, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<si><t>Instagram</t></si><si><t>Дата действия</t></si>"
            "<si><r><t>wallo</t></r><r><t>4vr</t></r></si></sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2"><v>46300</v></c></row>'
            "</sheetData></worksheet>",
        )
    assert service.import_file({"crm": "instagram", "path": str(xlsx)})["added"] == 1
    contact = service.contacts({"crm": "instagram"})["items"][0]
    assert contact["name"] == "wallo4vr"
    assert contact["next_action_at"] == datetime(2026, 10, 5).isoformat()
    with pytest.raises(ValueError):
        service.import_file({"crm": "instagram", "path": str(tmp_path / "x.txt")})


def test_channels_validation():
    assert channels_from(
        [{"kind": "email", "value": "A@B.co"}, {"kind": "email", "value": "a@b.co"}]
    ) == [{"kind": "email", "value": "a@b.co"}]
    with pytest.raises(ValueError):
        channels_from([{"kind": "discord", "value": "x"}])
