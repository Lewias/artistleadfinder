"""«Ответы»: phones and emails from outreach threads.

The thread pages are local stand-ins in real Chromium (`page.route`): nothing reaches
Instagram. The queue is driven by hand the way the shell drives it.
"""

import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from artist_lead_finder.database import open_database
from artist_lead_finder.errors import UserError
from artist_lead_finder.inbox import reader
from artist_lead_finder.inbox.extract import find_contacts
from artist_lead_finder.inbox.reader import read_thread
from artist_lead_finder.models import Conversation, InboxScan, Lead, Message, utcnow
from artist_lead_finder.service import ApplicationService

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
sync_api = pytest.importorskip("playwright.sync_api")

OURS = "Hey Jay, checked your music out — wanted to reach out real quick."


# ---------- extraction ----------


def test_finds_phones_and_emails_with_the_words_around():
    found = find_contacts(
        "yo bro text me (212) 736-5000 or hit jay.beats@Gmail.com, uk line +44 7911 123456"
    )
    values = {(item.kind, item.value, item.guessed) for item in found}
    assert values == {
        ("phone", "+12127365000", True),
        ("phone", "+447911123456", False),
        ("email", "jay.beats@gmail.com", False),
    }
    phone = next(item for item in found if item.value == "+12127365000")
    assert "text me (212) 736-5000" in phone.snippet


def test_dates_sums_and_zips_are_not_phones():
    text = "born 2001-05-12, show on 12/05/2024, paid $1500, zip 90210, 30k streams"
    assert find_contacts(text) == []


def test_the_default_region_reads_local_numbers():
    assert find_contacts("8 916 123-45-67", "RU")[0].value == "+79161234567"
    assert find_contacts("same number twice 2127365000 and (212) 736-5000")[0].guessed
    assert len(find_contacts("same number twice 2127365000 and (212) 736-5000")) == 1


# ---------- reading a thread ----------

THREAD = """<!doctype html><meta charset="utf-8">
<style>
  body {{ margin: 0; display: flex; height: 100vh; font: 14px sans-serif; }}
  nav {{ width: 320px; }}
  section {{ flex: 1; display: flex; flex-direction: column; }}
  header {{ height: 60px; }}
  .list {{ flex: 1; overflow-y: auto; display: flex; flex-direction: column; padding: 0 16px; }}
  .in {{ align-self: flex-start; max-width: 60%; }}
  .out {{ align-self: flex-end; max-width: 60%; }}
  .mid {{ align-self: center; }}
  .row {{ min-height: {row}px; display: flex; flex-direction: column; justify-content: center; }}
</style>
<nav><div dir="auto">kid.jay</div><div dir="auto">call me 3125550199 later</div></nav>
<section><header><div dir="auto">shot.by_jae1</div></header>
<div class="list">
  <div class="row"><div class="out"><div dir="auto">{ours}</div></div></div>
  <div class="row"><div class="mid"><div dir="auto">Today 3:41 PM</div></div></div>
  <div class="row"><div class="in"><div dir="auto">yo whats good</div></div></div>
  <div class="row"><div class="in"><div dir="auto">text me (212) 736-5000</div></div></div>
  <div class="row"><div class="out"><div dir="auto">bet, sending beats tonight</div></div></div>
</div>
<div role="textbox" contenteditable="true" aria-label="Сообщение"></div></section>"""

PROFILE = """<!doctype html><meta charset="utf-8"><body><main>
<h2>shot.by_jae1</h2>{buttons}</main>
<script>
const button = [...document.querySelectorAll('main [role=button]')]
  .find(item => item.textContent === 'Отправить сообщение');
if (button) button.onclick = () => setTimeout(() => {{
  history.pushState(null, '', '/direct/t/340282366/');
  document.open(); document.write(window.thread); document.close();
}}, 300);
</script></body>"""


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_api.sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True, channel="chromium")
            yield chromium
            chromium.close()
    except Exception as error:  # Chromium is not installed in this environment.
        pytest.skip(f"Chromium unavailable: {type(error).__name__}")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(reader, "SETTLE", 0.2)
    monkeypatch.setattr(reader, "PROFILE_WAIT", 3.0)
    monkeypatch.setattr(reader, "DIALOG_WAIT", 3.0)
    monkeypatch.setattr(reader, "SCROLL_PAUSE_MS", 100)


def thread_html(row=40):
    return THREAD.format(ours=OURS, row=row)


def open_page(browser, url, html):
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(url)
    return page


def test_reads_the_other_side_of_the_thread_only(browser):
    page = open_page(browser, "https://www.instagram.com/direct/t/340282366/", thread_html())
    result = read_thread(page, "shot.by_jae1", [OURS])
    assert result["outcome"] == "read" and result["thread_id"] == "340282366"
    inbound = [m["text"] for m in result["messages"] if m["side"] == "in"]
    outbound = [m["text"] for m in result["messages"] if m["side"] == "out"]
    # The inbox list, the header and the date in the middle are not messages.
    assert inbound == ["yo whats good", "text me (212) 736-5000"]
    assert outbound == [OURS, "bet, sending beats tonight"]
    page.close()


def test_scrolls_back_to_our_first_message(browser):
    page = open_page(browser, "https://www.instagram.com/direct/t/340282366/", thread_html(row=400))
    page.evaluate("document.querySelector('.list').scrollTop = 1e6")
    result = read_thread(page, "shot.by_jae1", [OURS])
    texts = [m["text"] for m in result["messages"]]
    assert OURS in texts and "text me (212) 736-5000" in texts
    page.close()


def test_opens_the_thread_from_the_profile(browser):
    page = open_page(
        browser,
        "https://www.instagram.com/shot.by_jae1/",
        PROFILE.format(buttons='<div role="button" tabindex="0">Отправить сообщение</div>'),
    )
    page.evaluate("html => { window.thread = html; }", thread_html())
    result = read_thread(page, "shot.by_jae1", [OURS])
    assert result["outcome"] == "read" and result["thread_id"] == "340282366"
    assert "text me (212) 736-5000" in [m["text"] for m in result["messages"]]
    page.close()


def test_no_thread_and_wrong_pages_read_nothing(browser):
    page = open_page(
        browser,
        "https://www.instagram.com/shot.by_jae1/",
        PROFILE.format(buttons="<button>Подписаться</button>"),
    )
    assert read_thread(page, "shot.by_jae1")["error"] == "no_thread"
    assert read_thread(page, "someone_else")["error"] == "no_instagram_tab"
    assert read_thread(page, "../x")["error"] == "bad_request"
    page.close()
    page = open_page(browser, "https://www.instagram.com/accounts/login/", "<p>login</p>")
    assert read_thread(page, "shot.by_jae1")["error"] == "login"
    page.close()


# ---------- the scan and the review ----------


@pytest.fixture
def app(tmp_path):
    engine, sessions = open_database(tmp_path / "inbox.db")
    service = ApplicationService(sessions, tmp_path / "data")
    service.inbox.window_open = lambda profile_id: True
    service.inbox.jitter = lambda low, high: 0
    sender = service.call("browser.create", {"name": "Sender A"})["id"]
    yield service, sessions, sender
    service.shutdown()
    engine.dispose()


def written(sessions, sender, username, *, days_ago=1, thread="340282366"):
    with sessions.begin() as session:
        lead = Lead(
            platform="instagram",
            username=username,
            profile_url=f"https://www.instagram.com/{username}/",
        )
        session.add(lead)
        session.flush()
        at = utcnow() - timedelta(days=days_ago)
        conversation = Conversation(
            lead_id=lead.id,
            sender_account_id=sender,
            platform_thread_id=thread,
            first_outbound_at=at,
            last_outbound_at=at,
        )
        session.add(conversation)
        session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                type="initial",
                body=OURS,
                sender_account_id=sender,
                sent_at=at,
            )
        )
        return lead.id


def read(text):
    return {
        "outcome": "read",
        "thread_id": "",
        "messages": [{"text": OURS, "side": "out"}, {"text": text, "side": "in"}],
    }


def test_scan_reads_each_thread_and_the_picked_contacts_go_to_the_crm(app):
    service, sessions, sender = app
    written(sessions, sender, "shot.by_jae1")
    written(sessions, sender, "kid.jay", days_ago=2, thread=None)
    written(sessions, sender, "old.one", days_ago=60)
    state = service.call("inbox.start", {"sender": sender, "days": 30})
    assert state["scan"]["total"] == 2 and state["scan"]["status"] == "running"
    with pytest.raises(UserError):
        service.call("inbox.start", {"sender": sender, "days": 30})

    first = service.call("inbox.next_internal", {})
    assert first["url"] == "https://www.instagram.com/direct/t/340282366/"
    assert first["outbound"] == [OURS] and first["username"] == "shot.by_jae1"
    # One thread at a time.
    assert service.call("inbox.next_internal", {}) is None
    service.call(
        "inbox.commit_internal",
        {"item_id": first["item_id"], "result": read("text me (212) 736-5000 or jay@gmail.com")},
    )
    second = service.call("inbox.next_internal", {})
    # No known thread: the profile, where «Отправить сообщение» opens it.
    assert second["url"] == "https://www.instagram.com/kid.jay/"
    service.call(
        "inbox.commit_internal",
        {"item_id": second["item_id"], "result": read("who is this?")},
    )
    state = service.call("inbox.state", {})
    assert state["scan"]["status"] == "done"
    assert (state["scan"]["replied"], state["scan"]["found"]) == (2, 2)
    findings = {item["value"]: item for item in state["findings"]}
    assert set(findings) == {"+12127365000", "jay@gmail.com"}
    assert findings["+12127365000"]["guessed"] is True

    result = service.call("inbox.add_to_crm", {"ids": [item["id"] for item in state["findings"]]})
    assert result["added"] == 1 and result["state"]["findings"] == []
    contacts = service.call("crm.list", {"crm": "imessage"})["items"]
    contact = next(item for item in contacts if item["name"] == "shot.by_jae1")
    assert contact["channels"][0] == {"kind": "phone", "value": "+12127365000"}
    assert {"kind": "instagram", "value": "shot.by_jae1"} in contact["channels"]
    assert "Дал номер" in contact["statuses"]

    # A second scan finds the same number again and does not offer it twice.
    service.call("inbox.start", {"sender": sender, "days": 30})
    job = service.call("inbox.next_internal", {})
    service.call(
        "inbox.commit_internal",
        {"item_id": job["item_id"], "result": read("text me (212) 736-5000")},
    )
    assert service.call("inbox.state", {})["findings"] == []


def test_login_or_limits_stop_the_scan_and_other_failures_skip_a_thread(app):
    service, sessions, sender = app
    for name in ("a.one", "b.two", "c.three"):
        written(sessions, sender, name)
    service.call("inbox.start", {"sender": sender, "days": 30})
    job = service.call("inbox.next_internal", {})
    service.call(
        "inbox.commit_internal",
        {"item_id": job["item_id"], "result": {"outcome": "error", "error": "dialog"}},
    )
    job = service.call("inbox.next_internal", {})
    service.call(
        "inbox.commit_internal",
        {"item_id": job["item_id"], "result": {"outcome": "error", "error": "rate_limited"}},
    )
    scan = service.call("inbox.state", {})["scan"]
    assert scan["status"] == "stopped" and "ограничил" in scan["reason"]
    assert scan["errors"] == 2
    assert service.call("inbox.next_internal", {}) is None


def test_waits_for_the_window_and_a_closed_app_stops_the_scan(app):
    service, sessions, sender = app
    written(sessions, sender, "shot.by_jae1")
    service.inbox.window_open = lambda profile_id: False
    service.call("inbox.start", {"sender": sender, "days": 30})
    assert service.call("inbox.next_internal", {}) is None
    assert "Откройте окно" in service.call("inbox.state", {})["scan"]["waiting"]
    service.inbox.recover()
    with sessions() as session:
        assert session.scalar(select(InboxScan.status)) == "stopped"


def test_nothing_to_read_and_unknown_accounts_are_refused(app):
    service, sessions, sender = app
    with pytest.raises(UserError):
        service.call("inbox.start", {"sender": sender, "days": 30})
    with pytest.raises(UserError):
        service.call("inbox.start", {"sender": "f" * 32, "days": 30})
    with pytest.raises(UserError):
        service.call("inbox.start", {"sender": sender, "days": 5})
