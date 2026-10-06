"""iPhone bridge: a phone simulator talks HTTP to the bridge on loopback.

Nothing is sent anywhere: the simulator only records what a Shortcut would hand to
Messages, and the clock is fake so ACK deadlines pass instantly.
"""

import http.client
import json
import plistlib
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote, unquote, urlsplit

import pytest
from sqlalchemy import select

from artist_lead_finder import memory_reset
from artist_lead_finder.database import open_database
from artist_lead_finder.imessage import network, shortcut
from artist_lead_finder.imessage import service as imessage
from artist_lead_finder.imessage.chain import parts_from, plan, summary
from artist_lead_finder.imessage.network import deep_link, is_lan_address, is_local_client
from artist_lead_finder.imessage.phones import normalize_phone, normalize_recipient
from artist_lead_finder.models import (
    IMessageAttachment,
    IMessageEvent,
    IMessageJob,
    Lead,
    LeadScoutProfile,
)

PHONES = ["+15555550101", "+15555550102", "+15555550103"]


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 3, 12, 0, 0)

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


@pytest.fixture
def bridge(tmp_path):
    engine, sessions = open_database(tmp_path / "db.sqlite3")
    clock = Clock()
    service = imessage.IMessageService(sessions, tmp_path, clock=clock)
    service.bridge_start({"ip": "127.0.0.1", "port": 0, "loopback": True})
    yield service, sessions, clock, tmp_path
    service.shutdown()
    engine.dispose()


def get(url: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def get_json(url: str) -> tuple[int, dict]:
    status, _, body = get(url)
    return status, json.loads(body)


class Phone:
    """What the improved Shortcut does, step by step, without Messages."""

    def __init__(self, next_url: str):
        self.next_url = next_url
        self.sent: list[tuple[str, str]] = []

    def take(self) -> dict:
        return get_json(self.next_url)[1]

    def run_job(self, job: dict, *, text_ack=True, done_ack=True) -> None:
        self.sent.append((job["recipient"], job["message"]))
        if text_ack:
            assert get_json(job["textAckUrl"])[0] == 200
        for item in job["attachments"]:
            status, headers, body = get(item["downloadUrl"])
            assert status == 200 and body
            self.sent.append((job["recipient"], headers["Content-Type"]))
        if done_ack:
            assert get_json(job["ackUrl"])[0] == 200

    def run(self, limit: int = 20) -> str:
        for _ in range(limit):
            job = self.take()
            if job["state"] != "ready":
                return job["state"]
            self.run_job(job)
        raise AssertionError("the loop did not end")


def setup_list(service, tmp_path, phones=PHONES, protocol="v2", attachment=True):
    service.workspace_update(
        {
            "recipients": [{"phone": phone, "message": ""} for phone in phones[:-1]]
            + [{"phone": phones[-1], "message": "Личный текст"}],
            "messages": ["Общий текст"],
            "protocol": protocol,
            "delay_seconds": 5,
        }
    )
    if attachment:
        picture = tmp_path / "cover.png"
        picture.write_bytes(b"\x89PNG fake image")
        service.attachment_add({"path": str(picture)})


def jobs(sessions) -> list[IMessageJob]:
    with sessions() as session:
        return list(session.scalars(select(IMessageJob).order_by(IMessageJob.id)))


def events(sessions) -> list[IMessageEvent]:
    with sessions() as session:
        return list(session.scalars(select(IMessageEvent).order_by(IMessageEvent.id)))


# ---------- input ----------


def test_numbers_need_a_country_code():
    assert normalize_phone("+1 (555) 555-0123") == "+15555550123"
    assert normalize_phone("0015555550123") == "+15555550123"
    assert normalize_phone("89991234567") is None
    assert normalize_phone("+1") is None
    assert normalize_phone("+" + "1" * 40) is None
    with pytest.raises(ValueError, match="международном"):
        imessage.recipients_from([{"phone": "12345", "message": ""}])
    deduped = imessage.recipients_from(["+15555550123", "+1 555 555 0123"])
    assert deduped == [{"phone": "+15555550123", "message": ""}]


def test_only_private_networks():
    assert is_lan_address("192.168.1.20") and is_lan_address("10.0.0.5")
    assert not is_lan_address("127.0.0.1") and not is_lan_address("8.8.8.8")
    assert not is_lan_address("169.254.10.3")
    assert is_local_client("192.168.1.30") and is_local_client("::ffff:192.168.1.30")
    assert not is_local_client("8.8.8.8") and not is_local_client("2a00:1450::1")


def test_bridge_refuses_addresses_outside_the_lan(bridge):
    service, *_ = bridge
    with pytest.raises(ValueError, match="локальной сети"):
        service.bridge_start({"ip": "8.8.8.8", "port": 47615})
    with pytest.raises(ValueError, match="локальной сети"):
        service.bridge_start({"ip": "127.0.0.1", "port": 47615})


def test_deep_link_encodes_name_and_url():
    url = "http://192.168.1.5:47615/v2/next?token=a-b_c&x=1"
    link = deep_link("Verse Мост & Co", url)
    assert link.startswith("shortcuts://run-shortcut?name=")
    query = link.split("?", 1)[1]
    assert "&" not in query.split("&text=")[1]
    params = dict(part.split("=", 1) for part in query.split("&"))
    assert unquote(params["name"]) == "Verse Мост & Co"
    assert params["input"] == "text"
    assert unquote(params["text"]) == url


# ---------- token and requests ----------


def test_every_route_needs_a_valid_token(bridge):
    service, _, clock, tmp_path = bridge
    setup_list(service, tmp_path)
    base = service.base_url()
    file_id = service.state()["workspace"]["attachments"][0]["id"]
    for path in (
        "/task",
        "/ack?jobId=1-1-00000000",
        "/v2/next",
        "/v2/status",
        "/connect",
        f"/attachment/{file_id}",
    ):
        separator = "&" if "?" in path else "?"
        assert get(base + path)[0] == 401
        assert get(f"{base}{path}{separator}token=wrong")[0] == 401
    assert get_json(service._url("/v2/status"))[0] == 200
    clock.advance(hours=13)
    assert get_json(service._url("/v2/status"))[0] == 401
    rejected = [event for event in events(bridge[1]) if event.type == "request_rejected"]
    # Once a minute per address: the burst above, then the expired token.
    assert len(rejected) == 2


def test_token_never_reaches_logs_or_events(bridge, capsys, caplog):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path)
    service.start({})
    phone = Phone(service._url("/v2/next"))
    assert phone.run() == "finished"
    get(service.base_url() + "/v2/next?token=" + "x" * 30)
    token = service.token
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert token not in caplog.text
    assert all(token not in event.detail for event in events(sessions))


def test_requests_with_a_body_or_a_long_url_are_refused(bridge):
    service, *_ = bridge
    url = service._url("/v2/status")
    assert get(url, {"Content-Length": "10"})[0] == 413
    assert get(url + "&pad=" + "a" * 3000)[0] == 414


def test_rotated_token_invalidates_old_links(bridge):
    service, *_ = bridge
    old = service._url("/v2/status")
    service.rotate_token()
    assert get(old)[0] == 401
    assert get(service._url("/v2/status"))[0] == 200


# ---------- attachments ----------


def test_attachments_are_served_by_id_only(bridge):
    service, _, _, tmp_path = bridge
    setup_list(service, tmp_path)
    attachment = service.state()["workspace"]["attachments"][0]
    status, headers, body = get(service._url(f"/attachment/{attachment['id']}"))
    assert status == 200 and body == b"\x89PNG fake image"
    assert headers["Content-Type"] == "image/png"
    assert "filename*=UTF-8''cover.png" in headers["Content-Disposition"]
    secret = tmp_path / "db.sqlite3"
    for path in (
        "/attachment/../db.sqlite3",
        "/attachment/..%2Fdb.sqlite3",
        f"/attachment/{'0' * 32}",
        f"/attachment/{quote(str(secret), safe='')}",
    ):
        assert get(service._url(path))[0] == 404
    service.attachment_remove({"id": attachment["id"]})
    assert get(service._url(f"/attachment/{attachment['id']}"))[0] == 404
    assert not (tmp_path / "imessage-attachments" / attachment["id"]).exists()


def test_attachment_types_and_size_are_limited(bridge, monkeypatch):
    service, _, _, tmp_path = bridge
    program = tmp_path / "run.exe"
    program.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="Поддерживаются"):
        service.attachment_add({"path": str(program)})
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * 2048)
    monkeypatch.setattr(imessage, "MAX_ATTACHMENT_BYTES", 1024)
    with pytest.raises(ValueError, match="Размер"):
        service.attachment_add({"path": str(big)})


def test_attachment_in_use_cannot_be_removed(bridge):
    service, _, _, tmp_path = bridge
    setup_list(service, tmp_path)
    service.start({})
    file_id = service.state()["workspace"]["attachments"][0]["id"]
    with pytest.raises(ValueError, match="текущей рассылке"):
        service.attachment_remove({"id": file_id})


# ---------- improved protocol ----------


def test_phone_simulator_runs_a_campaign_one_job_at_a_time(bridge):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path)
    service.start({})
    phone = Phone(service._url("/v2/next"))
    first = phone.take()
    assert first["state"] == "ready"
    assert set(first) >= {
        "jobId",
        "recipient",
        "message",
        "attachments",
        "ackUrl",
        "statusUrl",
        "delaySeconds",
    }
    assert first["recipient"] == PHONES[0] and first["message"] == "Общий текст"
    assert first["delaySeconds"] == 5
    phone.run_job(first)
    assert phone.run() == "finished"
    assert [item[0] for item in phone.sent if item[1] != "image/png"] == PHONES
    assert ("+15555550103", "Личный текст") in phone.sent
    assert all(job.status == "execution_acknowledged" for job in jobs(sessions))
    assert all(job.ack_scope == "complete" and job.text_acked_at for job in jobs(sessions))
    state = service.state()
    assert state["campaign"]["status"] == "finished" and not state["active"]


def test_last_job_has_no_delay(bridge):
    service, _, _, tmp_path = bridge
    setup_list(service, tmp_path, phones=PHONES[:1], attachment=False)
    service.start({})
    assert Phone(service._url("/v2/next")).take()["delaySeconds"] == 0


def test_repeated_ack_changes_nothing(bridge):
    service, sessions, clock, tmp_path = bridge
    setup_list(service, tmp_path)
    service.start({})
    phone = Phone(service._url("/v2/next"))
    job = phone.take()
    phone.run_job(job)
    acked_at = jobs(sessions)[0].acked_at
    clock.advance(minutes=1)
    for _ in range(3):
        status, body = get_json(job["ackUrl"])
        assert status == 200 and body == {"ok": True, "duplicate": True}
        assert get_json(job["textAckUrl"])[1]["duplicate"] is True
    first = jobs(sessions)[0]
    assert first.status == "execution_acknowledged" and first.acked_at == acked_at
    assert first.ack_count == 4
    assert phone.take()["recipient"] == PHONES[1]


def test_pause_and_stop_act_between_jobs(bridge):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path)
    service.start({})
    phone = Phone(service._url("/v2/next"))
    job = phone.take()
    service.control({"action": "pause"})
    # The job the phone already has still finishes.
    phone.run_job(job)
    assert jobs(sessions)[0].status == "execution_acknowledged"
    paused = phone.take()
    assert paused["state"] == "paused" and paused["retrySeconds"] > 0
    assert get_json(paused["statusUrl"])[1]["state"] == "paused"
    assert [job.status for job in jobs(sessions)][1:] == ["pending", "pending"]
    service.control({"action": "resume"})
    second = phone.take()
    assert second["state"] == "ready" and second["recipient"] == PHONES[1]
    service.control({"action": "stop"})
    phone.run_job(second)
    assert phone.take()["state"] == "stopped"
    assert [job.status for job in jobs(sessions)] == [
        "execution_acknowledged",
        "execution_acknowledged",
        "pending",
    ]


def test_lost_ack_makes_the_job_uncertain_and_it_is_never_resent(bridge):
    service, sessions, clock, tmp_path = bridge
    setup_list(service, tmp_path)
    service.start({})
    phone = Phone(service._url("/v2/next"))
    first = phone.take()
    phone.run_job(first, done_ack=False)
    second = phone.take()
    assert second["recipient"] == PHONES[1]
    lost = jobs(sessions)[0]
    assert lost.status == "uncertain" and "вложения" in lost.note
    # Second job: no ACK at all and the phone goes quiet; the deadline settles it.
    phone.run_job(second, text_ack=False, done_ack=False)
    clock.advance(minutes=11)
    assert service.state()["jobs"][1]["status"] == "uncertain"
    third = phone.take()
    assert third["recipient"] == PHONES[2]
    phone.run_job(third)
    assert phone.take()["state"] == "finished"
    assert [item[0] for item in phone.sent if item[1] != "image/png"] == PHONES
    assert [job.status for job in jobs(sessions)] == [
        "uncertain",
        "uncertain",
        "execution_acknowledged",
    ]


def test_late_ack_settles_an_uncertain_job(bridge):
    service, sessions, clock, tmp_path = bridge
    setup_list(service, tmp_path, attachment=False)
    service.start({})
    phone = Phone(service._url("/v2/next"))
    job = phone.take()
    clock.advance(minutes=11)
    service.state()
    assert jobs(sessions)[0].status == "uncertain"
    assert get_json(job["ackUrl"])[1] == {"ok": True, "duplicate": False}
    first = jobs(sessions)[0]
    assert first.status == "execution_acknowledged" and "позже" in first.note
    assert any(event.type == "ack_late" for event in events(sessions))


def test_user_resolves_uncertain_jobs(bridge):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path, attachment=False)
    service.start({})
    phone = Phone(service._url("/v2/next"))
    for _ in range(3):
        phone.take()
    assert phone.take()["state"] == "finished"
    first, second, third = jobs(sessions)
    assert first.status == second.status == "uncertain"
    with pytest.raises(ValueError):
        service.resolve({"job_id": first.id, "resolution": "delete"})
    service.resolve({"job_id": first.id, "resolution": "sent"})
    service.resolve({"job_id": second.id, "resolution": "not_sent"})
    service.resolve({"job_id": second.id, "resolution": "resend"})
    # Back in the queue, but the campaign waits for the user to resume.
    assert phone.take()["state"] == "paused"
    service.control({"action": "resume"})
    again = phone.take()
    assert again["recipient"] == PHONES[1] and again["jobId"] == second.key
    phone.run_job(again)
    statuses = {job.phone: (job.status, job.ack_scope, job.attempts) for job in jobs(sessions)}
    assert statuses[PHONES[0]] == ("execution_acknowledged", "manual", 1)
    assert statuses[PHONES[1]] == ("execution_acknowledged", "complete", 2)
    with pytest.raises(ValueError, match="только для"):
        service.resolve({"job_id": first.id, "resolution": "resend"})


def test_unknown_or_unissued_jobs_are_not_acknowledged(bridge):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path, attachment=False)
    service.start({})
    key = jobs(sessions)[0].key
    assert get_json(service._url("/v2/ack", jobId=key, stage="done"))[0] == 409
    assert get_json(service._url("/v2/ack", jobId="9-9-deadbeef"))[0] == 404
    assert get_json(service._url("/v2/ack", jobId="../x"))[0] == 400
    assert jobs(sessions)[0].status == "pending"


def test_restart_keeps_the_queue_and_the_issued_job(bridge):
    service, sessions, clock, tmp_path = bridge
    setup_list(service, tmp_path, attachment=False)
    service.start({})
    phone = Phone(service._url("/v2/next"))
    job = phone.take()
    token = service.token
    service.shutdown()
    restarted = imessage.IMessageService(sessions, tmp_path, clock=clock)
    try:
        assert restarted.token == token
        restarted.bridge_start({"ip": "127.0.0.1", "port": 0, "loopback": True})
        assert [item.status for item in jobs(sessions)] == ["issued", "pending", "pending"]
        # The phone's ACK reaches the restarted bridge (new port, same token).
        ack = urlsplit(job["ackUrl"])
        assert get_json(restarted.base_url() + ack.path + "?" + ack.query)[0] == 200
        phone = Phone(restarted._url("/v2/next"))
        assert phone.run() == "finished"
        assert [item[0] for item in phone.sent] == PHONES[1:]
        assert all(item.attempts == 1 for item in jobs(sessions))
    finally:
        restarted.shutdown()


def test_restart_without_ack_ends_uncertain(bridge):
    service, sessions, clock, tmp_path = bridge
    setup_list(service, tmp_path, attachment=False)
    service.start({})
    Phone(service._url("/v2/next")).take()
    service.shutdown()
    clock.advance(minutes=30)
    restarted = imessage.IMessageService(sessions, tmp_path, clock=clock)
    try:
        assert restarted.state()["jobs"][0]["status"] == "uncertain"
    finally:
        restarted.shutdown()


# ---------- campaigns ----------


def test_only_one_campaign_and_sent_numbers_are_skipped(bridge):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path, attachment=False)
    service.start({})
    with pytest.raises(ValueError, match="уже идёт"):
        service.start({})
    phone = Phone(service._url("/v2/next"))
    phone.run_job(phone.take())
    service.control({"action": "stop"})
    result = service.start({})
    assert result["skipped"] == 1
    assert [job.phone for job in jobs(sessions)][3:] == PHONES[1:]
    statuses = {item["phone"]: item["status"] for item in result["workspace"]["recipients"]}
    assert statuses[PHONES[0]] == "execution_acknowledged"


def test_test_send_goes_to_one_number(bridge):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path, attachment=False)
    result = service.start({"test_phone": PHONES[2]})
    assert result["campaign"]["is_test"] and result["campaign"]["total"] == 1
    phone = Phone(service._url("/v2/next"))
    assert phone.run() == "finished"
    assert phone.sent == [(PHONES[2], "Личный текст")]
    # A test does not count as reaching the number.
    assert service.start({})["skipped"] == 0


def test_campaign_needs_text_for_every_number(bridge):
    service, *_ = bridge
    service.workspace_update({"recipients": [{"phone": PHONES[0], "message": ""}]})
    with pytest.raises(ValueError, match="Нет текста"):
        service.start({})


# ---------- legacy protocol ----------


def test_legacy_task_matches_the_original_shortcut(bridge):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path, protocol="legacy")
    service.start({})
    status, task = get_json(service._url("/task"))
    assert status == 200 and set(task) == {"message", "contacts", "attachments"}
    assert task["message"] == "Общий текст"
    assert [contact["phone"] for contact in task["contacts"]] == PHONES
    assert task["contacts"][2]["message"] == "Личный текст"
    assert set(task["contacts"][0]) == {"phone", "message", "ackUrl"}
    assert set(task["attachments"][0]) == {"downloadUrl"}
    ack = parse_qs(urlsplit(task["contacts"][0]["ackUrl"]).query)
    assert set(ack) == {"token", "jobId"}
    assert urlsplit(task["contacts"][0]["ackUrl"]).path == "/ack"
    # A second fetch hands out nothing again.
    assert get_json(service._url("/task"))[1]["contacts"] == []
    for contact in task["contacts"]:
        assert get_json(contact["ackUrl"])[0] == 200
    assert get_json(task["contacts"][0]["ackUrl"])[1]["duplicate"] is True
    assert {job.ack_scope for job in jobs(sessions)} == {"text"}
    assert service.state()["campaign"]["status"] == "finished"


def test_protocols_do_not_mix(bridge):
    service, _, _, tmp_path = bridge
    setup_list(service, tmp_path, protocol="legacy", attachment=False)
    service.start({})
    status, body = get_json(service._url("/v2/next"))
    assert status == 409 and body["state"] == "stopped"
    task = get_json(service._url("/task"))[1]
    key = parse_qs(urlsplit(task["contacts"][0]["ackUrl"]).query)["jobId"][0]
    assert get_json(service._url("/v2/ack", jobId=key))[0] == 409
    service.control({"action": "stop"})
    service.workspace_update({"protocol": "v2"})
    service.start({"test_phone": PHONES[0]})
    assert get_json(service._url("/task"))[1] == {"message": "", "contacts": [], "attachments": []}


def test_legacy_ack_deadline_follows_the_original_waits(bridge):
    service, sessions, clock, tmp_path = bridge
    setup_list(service, tmp_path, protocol="legacy", attachment=False)
    service.start({})
    get_json(service._url("/task"))
    clock.advance(minutes=6, seconds=30)
    statuses = [job["status"] for job in service.state()["jobs"]]
    assert statuses == ["uncertain", "issued", "issued"]


# ---------- connect page and Shortcut ----------


def test_connect_page_only_links_to_shortcuts(bridge):
    service, *_ = bridge
    status, headers, body = get(service._url("/connect"))
    page = body.decode()
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "shortcuts://run-shortcut?name=Verse%20iPhone%20Bridge&amp;input=text&amp;text=" in page
    assert "<script" not in page and "http-equiv" not in page


def test_unsigned_workflow_is_well_formed():
    data = plistlib.loads(shortcut.unsigned_plist())
    actions = data["WFWorkflowActions"]
    assert len(actions) == len(shortcut.guide()) == 31
    known = {
        "is.workflow.actions.setvariable",
        "is.workflow.actions.repeat.count",
        "is.workflow.actions.repeat.each",
        "is.workflow.actions.downloadurl",
        "is.workflow.actions.detect.dictionary",
        "is.workflow.actions.getvalueforkey",
        "is.workflow.actions.conditional",
        "is.workflow.actions.sendmessage",
        "is.workflow.actions.delay",
        "is.workflow.actions.exit",
    }
    assert {action["WFWorkflowActionIdentifier"] for action in actions} <= known
    # Every block opens once, closes once, in order; outputs refer to earlier actions.
    groups: dict[str, list[int]] = {}
    seen: set[str] = set()

    def references(value):
        if isinstance(value, dict):
            if "OutputUUID" in value:
                yield value["OutputUUID"]
            for item in value.values():
                yield from references(item)
        elif isinstance(value, list):
            for item in value:
                yield from references(item)

    for action in actions:
        parameters = action["WFWorkflowActionParameters"]
        for uuid in references(parameters):
            assert uuid in seen
        if "UUID" in parameters:
            seen.add(parameters["UUID"])
        if "GroupingIdentifier" in parameters:
            groups.setdefault(parameters["GroupingIdentifier"], []).append(
                parameters["WFControlFlowMode"]
            )
    assert sorted(groups.values()) == sorted([[0, 2], [0, 1, 2], [0, 1, 2], [0, 2]])
    sends = [
        action["WFWorkflowActionParameters"]
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.sendmessage"
    ]
    assert len(sends) == 2 and all(send["ShowWhenRun"] is True for send in sends)
    assert all(
        send["IntentAppDefinition"]["BundleIdentifier"] == "com.apple.MobileSMS" for send in sends
    )
    file_token = sends[1]["WFSendMessageContent"]["Value"]
    assert file_token["string"] == "￼"
    coercion = file_token["attachmentsByRange"]["{0, 1}"]["Aggrandizements"][0]
    assert coercion["CoercionItemClass"] == "WFGenericFileContentItem"
    assert "WFStringContentItem" in data["WFWorkflowInputContentItemClasses"]


def test_export_writes_only_plist(tmp_path):
    with pytest.raises(ValueError):
        shortcut.export(str(tmp_path / "Verse.shortcut"))
    result = shortcut.export(str(tmp_path / "Verse.plist"))
    assert result["actions"] == 31
    assert plistlib.loads((tmp_path / "Verse.plist").read_bytes())["WFWorkflowActions"]


def test_signed_shortcut_saves_and_switches_to_legacy(bridge, tmp_path):
    service, *_ = bridge
    with pytest.raises(ValueError):
        service.shortcut_save({"path": str(tmp_path / "Verse.plist")})
    assert service.state()["workspace"]["protocol"] == "legacy"
    service.workspace_update({"protocol": "v2"})
    target = tmp_path / "Verse iMessage.shortcut"
    result = service.shortcut_save({"path": str(target)})
    assert target.read_bytes() == shortcut.SIGNED.read_bytes()
    assert target.read_bytes().startswith(b"AEA1")
    assert result["switched"] is True
    assert result["state"]["workspace"]["protocol"] == "legacy"
    assert service.shortcut_save({"path": str(target)})["switched"] is False


def test_untouched_campaign_follows_the_installed_shortcut(bridge, tmp_path):
    service, *_ = bridge
    setup_list(service, tmp_path)
    service.start({})
    result = service.shortcut_save({"path": str(tmp_path / "Verse iMessage.shortcut")})
    assert result["switched"] is True
    assert result["state"]["campaign"]["protocol"] == "legacy"
    state = service.state()
    assert state["bridge"]["deep_links"]["legacy"]
    payload = get_json(service._url("/task"))[1]
    assert len(payload["contacts"]) == len(PHONES)


def test_protocol_is_fixed_once_the_phone_took_a_job(bridge, tmp_path):
    service, *_ = bridge
    setup_list(service, tmp_path)
    service.start({})
    Phone(service._url("/v2/next")).take()
    result = service.shortcut_save({"path": str(tmp_path / "Verse iMessage.shortcut")})
    assert result["switched"] is False
    assert result["state"]["campaign"]["protocol"] == "v2"
    with pytest.raises(ValueError):
        service.workspace_update({"protocol": "legacy"})


# ---------- addresses, emails, message variants, CRM ----------


def test_vpn_tunnel_is_not_the_default_address(bridge, monkeypatch):
    service, sessions, *_ = bridge
    # xray / WireGuard take the default route with a 172.16/12 address.
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda *a: [
            (2, 0, 0, "", (ip, 0)) for ip in ("172.18.0.1", "169.254.3.3", "192.168.0.107")
        ],
    )

    class Probe:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, *a):
            pass

        def getsockname(self):
            return ("172.18.0.1", 5000)

    monkeypatch.setattr(network.socket, "socket", Probe)
    assert network.lan_addresses() == ["192.168.0.107", "172.18.0.1"]
    monkeypatch.setattr(imessage, "lan_addresses", network.lan_addresses)
    # An address saved by the earlier version gives way to the LAN one.
    assert service._preferred_ip("172.18.0.1") == "192.168.0.107"
    assert service._preferred_ip("192.168.0.107") == "192.168.0.107"


def test_emails_are_recipients_too():
    assert normalize_recipient(" Name@Example.COM ") == "name@example.com"
    assert normalize_recipient("name@localhost") is None
    assert normalize_recipient("+44 20 7946 0958") == "+442079460958"
    assert imessage.recipients_from(["a@b.co", "+15555550123"]) == [
        {"phone": "a@b.co", "message": ""},
        {"phone": "+15555550123", "message": ""},
    ]


def test_message_variants_go_in_turn_with_the_recipient(bridge):
    service, sessions, *_ = bridge
    service.workspace_update(
        {
            "recipients": [*PHONES, "fan@example.com"],
            "messages": ["Привет, {Phone}!", "Второй вариант", "Привет, {Phone}!"],
        }
    )
    assert service.state()["workspace"]["messages"] == ["Привет, {Phone}!", "Второй вариант"]
    service.start({})
    assert [job.message for job in jobs(sessions)] == [
        f"Привет, {PHONES[0]}!",
        "Второй вариант",
        f"Привет, {PHONES[2]}!",
        "Второй вариант",
    ]
    assert jobs(sessions)[3].phone == "fan@example.com"


def test_campaign_needs_a_message(bridge):
    service, *_ = bridge
    service.workspace_update({"recipients": PHONES, "messages": []})
    with pytest.raises(ValueError, match="добавьте сообщение"):
        service.start({})


def test_crm_adds_the_first_contact_of_each_lead(bridge):
    service, sessions, *_ = bridge
    with sessions.begin() as session:
        for index, (status, phones, emails, blocked) in enumerate(
            [
                ("qualified", ["+15555550111"], ["one@example.com"], False),
                ("qualified", [], ["two@example.com"], False),
                ("qualified", ["+15555550113"], [], True),
                ("new", ["+15555550114"], [], False),
                ("qualified", [], [], False),
            ]
        ):
            lead = Lead(
                platform="instagram",
                username=f"artist{index}",
                status=status,
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
                    source_username="source",
                    discovery_method="post",
                )
            )
    service.workspace_update({"recipients": ["two@example.com"]})
    result = service.add_leads({"statuses": ["qualified"]})
    assert result["added"] == 1
    assert [item["phone"] for item in result["workspace"]["recipients"]] == [
        "two@example.com",
        "+15555550111",
    ]
    with pytest.raises(ValueError):
        service.add_leads({"statuses": []})


def test_restart_moves_an_untouched_campaign_to_the_signed_shortcut(bridge, tmp_path):
    service, *_ = bridge
    setup_list(service, tmp_path, protocol="v2")
    service.start({})
    service.restore()
    state = service.state()
    assert state["workspace"]["protocol"] == "legacy"
    assert state["campaign"]["protocol"] == "legacy"


# ---------- templates and chains ----------

A = "a" * 32
B = "b" * 32


def part(text="", *files):
    return {"text": text, "attachment_ids": list(files)}


def upload(service, tmp_path, name="cover.png") -> str:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG fake image " + name.encode())
    return service.attachment_add({"path": str(path), "target": "file"})["attachment"]["id"]


def stored(sessions) -> set[str]:
    with sessions() as session:
        return set(session.scalars(select(IMessageAttachment.id)))


# ---------- layout over launches ----------


def test_texts_go_in_one_launch():
    steps = plan([part("Привет"), part("Как дела?")])
    assert [(step["text"], step["run"]) for step in steps] == [("Привет", 0), ("Как дела?", 0)]


def test_files_follow_the_text_before_them():
    steps = plan([part("Привет"), part("", A), part("", B)])
    assert len(steps) == 1
    assert steps[0]["attachment_ids"] == [A, B] and steps[0]["parts"] == [1, 2, 3]


def test_message_with_files_is_a_launch_of_its_own():
    steps = plan([part("Привет"), part("Трек", A), part("Пока")])
    assert [step["run"] for step in steps] == [0, 1, 2]
    assert summary([part("Привет"), part("Трек", A), part("Пока")])["launches"] == 3
    assert [step["run"] for step in plan([part("Трек", A), part("Пока"), part("Ещё")])] == [0, 1, 1]


def test_files_cannot_open_the_chain():
    with pytest.raises(ValueError, match="начинает"):
        plan([part("", A), part("Текст")])
    assert summary([part("", A)])["error"]


def test_template_parts_are_checked():
    with pytest.raises(ValueError, match="пустое"):
        parts_from([part("Привет"), part()], require_content=True)
    assert parts_from([part()], require_content=False) == [part()]
    with pytest.raises(ValueError, match="вложение"):
        parts_from([{"text": "x", "attachment_ids": ["../etc"]}], require_content=True)
    with pytest.raises(ValueError, match="Не больше"):
        parts_from([part("x")] * 11, require_content=True)


# ---------- templates ----------


def test_templates_keep_their_files(bridge):
    service, sessions, _, tmp_path = bridge
    file_id = upload(service, tmp_path)
    saved = service.template_save(
        {
            "name": "Приветствие",
            "folder": "Холодные",
            "parts": [part("Привет, {Phone}!"), part("", file_id)],
        }
    )
    assert saved["plan"] == {"messages": 1, "launches": 1, "error": None}
    assert saved["parts"][1]["attachments"][0]["filename"] == "cover.png"
    assert [item["name"] for item in service.templates()] == ["Приветствие"]
    # A restart clears unsaved uploads, not the template's files.
    loose = upload(service, tmp_path, "loose.png")
    service.remove_orphan_files()
    assert stored(sessions) == {file_id}
    assert not (tmp_path / "imessage-attachments" / loose).exists()
    # Dropping the file from the template deletes it.
    service.template_save({"id": saved["id"], "name": "Приветствие", "parts": [part("Привет")]})
    assert stored(sessions) == set()


def test_shared_file_survives_until_the_last_user(bridge):
    service, sessions, _, tmp_path = bridge
    file_id = upload(service, tmp_path)
    first = service.template_save({"name": "Один", "parts": [part("Текст", file_id)]})
    service.template_use({"id": first["id"]})
    assert service.state()["workspace"]["attachments"][0]["id"] == file_id
    service.template_delete({"id": first["id"]})
    assert stored(sessions) == {file_id}
    service.attachment_remove({"id": file_id})
    assert stored(sessions) == set()


def test_one_message_template_joins_the_variants(bridge):
    service, _, _, tmp_path = bridge
    service.workspace_update({"messages": ["Старый"]})
    saved = service.template_save({"name": "Короткий", "parts": [part("Новый")]})
    state = service.template_use({"id": saved["id"]})
    assert state["workspace"]["messages"] == ["Старый", "Новый"]
    assert state["workspace"]["sequence"] == []


def test_chain_template_becomes_the_chain(bridge):
    service, _, _, tmp_path = bridge
    saved = service.template_save({"name": "Цепочка", "parts": [part("Привет"), part("Как дела?")]})
    state = service.template_use({"id": saved["id"]})
    assert [item["text"] for item in state["workspace"]["sequence"]] == ["Привет", "Как дела?"]
    assert state["workspace"]["plan"]["launches"] == 1


# ---------- chains over /task ----------


def chain(service, tmp_path, parts, phones=PHONES[:2]):
    service.workspace_update(
        {
            "recipients": [{"phone": phone, "message": ""} for phone in phones],
            "sequence": parts,
            "protocol": "legacy",
        }
    )


def ack_all(task):
    for contact in task["contacts"]:
        assert get_json(contact["ackUrl"])[0] == 200


def test_text_chain_goes_in_one_list(bridge):
    service, sessions, _, tmp_path = bridge
    chain(service, tmp_path, [part("Привет, {Phone}"), part("Как дела?")])
    state = service.start({})
    assert state["campaign"]["total"] == 4 and state["campaign"]["runs"] == 1
    task = get_json(service._url("/task"))[1]
    assert [(c["phone"], c["message"]) for c in task["contacts"]] == [
        (PHONES[0], f"Привет, {PHONES[0]}"),
        (PHONES[0], "Как дела?"),
        (PHONES[1], f"Привет, {PHONES[1]}"),
        (PHONES[1], "Как дела?"),
    ]
    assert task["attachments"] == []
    ack_all(task)
    assert service.state()["campaign"]["status"] == "finished"


def test_files_wait_for_their_own_launch(bridge):
    service, sessions, _, tmp_path = bridge
    file_id = upload(service, tmp_path)
    chain(service, tmp_path, [part("Привет"), part("Вот трек", file_id)])
    state = service.start({})
    assert state["campaign"]["runs"] == 2 and state["campaign"]["next_run"] == 1
    first = get_json(service._url("/task"))[1]
    assert [c["message"] for c in first["contacts"]] == ["Привет", "Привет"]
    assert first["attachments"] == []
    # The phone is still on the first list: nothing else is handed out.
    assert get_json(service._url("/task"))[1]["contacts"] == []
    ack_all(first)
    assert service.state()["campaign"]["next_run"] == 2
    second = get_json(service._url("/task"))[1]
    assert [c["message"] for c in second["contacts"]] == ["Вот трек", "Вот трек"]
    assert len(second["attachments"]) == 1
    status, headers, _ = get(second["attachments"][0]["downloadUrl"])
    assert status == 200 and headers["Content-Type"] == "image/png"
    ack_all(second)
    assert service.state()["campaign"]["status"] == "finished"


def test_unconfirmed_message_holds_the_rest_of_the_chain(bridge):
    service, sessions, clock, tmp_path = bridge
    file_id = upload(service, tmp_path)
    chain(service, tmp_path, [part("Привет"), part("Трек", file_id)])
    service.start({})
    first = get_json(service._url("/task"))[1]
    assert get_json(first["contacts"][0]["ackUrl"])[0] == 200
    clock.advance(hours=1)  # the second ACK never comes
    second = get_json(service._url("/task"))[1]
    assert [c["phone"] for c in second["contacts"]] == [PHONES[0]]
    blocked = [job for job in jobs(sessions) if job.resolution == "chain_blocked"]
    assert [(job.phone, job.step, job.status) for job in blocked] == [(PHONES[1], 1, "failed")]


def test_test_send_gets_the_whole_chain(bridge):
    service, _, _, tmp_path = bridge
    chain(service, tmp_path, [part("Раз"), part("Два")])
    service.start({"test_phone": PHONES[2]})
    task = get_json(service._url("/task"))[1]
    assert [(c["phone"], c["message"]) for c in task["contacts"]] == [
        (PHONES[2], "Раз"),
        (PHONES[2], "Два"),
    ]


def test_chain_starting_with_files_is_refused(bridge):
    service, _, _, tmp_path = bridge
    file_id = upload(service, tmp_path)
    chain(service, tmp_path, [part("", file_id), part("Текст")])
    with pytest.raises(ValueError, match="начинает"):
        service.start({})
    assert service.state()["workspace"]["plan"]["error"]


def test_preview_shows_every_message(bridge):
    service, _, _, tmp_path = bridge
    chain(service, tmp_path, [part("Раз"), part("Два")])
    preview = service.preview()
    assert [m["text"] for m in preview["items"][0]["messages"]] == ["Раз", "Два"]
    assert len(preview["payload"]["contacts"]) == 4


def test_memory_reset_forgets_sent_numbers(bridge):
    service, sessions, _, tmp_path = bridge
    setup_list(service, tmp_path, attachment=False)
    service.start({})

    def reset():
        with service.lock, sessions.begin() as session:
            return memory_reset.reset_imessage(session)

    with pytest.raises(ValueError, match="Остановите рассылку iMessage"):
        reset()
    phone = Phone(service._url("/v2/next"))
    phone.run_job(phone.take())
    service.control({"action": "stop"})
    reset()
    assert jobs(sessions) == [] and events(sessions) == []
    # The number reached before the reset is sent to again.
    assert service.start({})["skipped"] == 0
