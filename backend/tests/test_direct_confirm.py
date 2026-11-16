"""What counts as a sent message, the account's state, the routes to a thread and the
follow-up rules of a send, against local stand-in pages in real Chromium.

One stand-in page plays the profile, the chat (a page or a window over the profile), the
recipient picker and Direct's new message, chosen by the URL. After Enter it does what
the test asks: a new bubble, only a cleared box, an error mark, a faded bubble, a closed
window... Every request is answered by `page.route`: nothing reaches Instagram.
"""

import json
import os

import pytest

from artist_lead_finder import direct_message
from artist_lead_finder.direct_message import DirectSender, send_direct

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
sync_api = pytest.importorskip("playwright.sync_api")

PAGE = """<!doctype html><meta charset="utf-8"><body style="margin:0;font:14px sans-serif">
<script>
const cfg = __CFG__;
const user = cfg.user;
window.sent = [];
const bubble = (text, side, faded) => {
  const row = document.createElement('div');
  row.style.alignSelf = side === 'out' ? 'flex-end' : 'flex-start';
  if (faded) row.style.opacity = '0.4';
  const inner = document.createElement('div');
  inner.dir = 'auto';
  inner.innerText = text;
  row.appendChild(inner);
  return row;
};
function chat(name, where) {
  const pane = document.createElement('section');
  if (where === 'popup') {
    const open = document.querySelectorAll('section.popup').length;
    pane.className = 'popup';
    pane.style = 'position:fixed;bottom:0;width:340px;height:440px;display:flex;' +
      'flex-direction:column;background:#fff;right:' + (20 + 360 * open) + 'px';
    if (cfg.popupDialog) pane.setAttribute('role', 'dialog');
  } else {
    pane.style = 'height:100vh;display:flex;flex-direction:column';
  }
  const link = '<a href="/' + name + '/"><span>' + name + '</span></a>';
  pane.innerHTML = (cfg.noHeader ? '<div style="height:60px"></div>' :
      '<header style="height:60px">' + link + '</header>') +
    '<div class="list" style="flex:1;display:flex;flex-direction:column;' +
    'justify-content:flex-end;padding:0 16px"></div><div class="log"></div>' +
    '<div role="textbox" contenteditable="true" aria-label="Сообщение"></div>';
  const list = pane.querySelector('.list');
  if (name === user)
    for (const [text, side] of cfg.history || []) list.appendChild(bubble(text, side));
  const box = pane.querySelector('[role=textbox]');
  box.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    const text = box.innerText;
    window.sent.push([name, text]);
    if (cfg.limitOnEnter) window.limited = true;
    const mode = cfg.enter || 'bubble';
    box.innerHTML = '';
    if (mode === 'bubble') list.appendChild(bubble(text, 'out'));
    if (mode === 'late') setTimeout(() => list.appendChild(bubble(text, 'out')), 1200);
    if (mode === 'pending') list.appendChild(bubble(text, 'out', true));
    if (mode === 'fail') {
      list.appendChild(bubble(text, 'out'));
      pane.querySelector('.log').textContent = "Couldn't send";
    }
    if (mode === 'close') pane.remove();
  });
  (where === 'popup' ? document.body : document.querySelector('#app')).appendChild(pane);
}
function picker() {
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  dialog.style = 'position:fixed;top:40px;left:300px;width:400px;height:400px;background:#fff';
  dialog.innerHTML = '<div>Новое сообщение</div><input name="queryBox" placeholder="Поиск...">' +
    '<div class="rows"></div>';
  document.body.appendChild(dialog);
  const input = dialog.querySelector('input');
  let chosen = null;
  input.addEventListener('input', () => {
    const rows = dialog.querySelector('.rows');
    rows.innerHTML = '';
    for (const [name, full] of cfg.results || []) {
      if (!name.includes(input.value)) continue;
      const row = document.createElement('div');
      row.setAttribute('role', 'button');
      row.innerHTML = '<span>' + full + '</span><br><span>' + name + '</span>';
      row.onclick = () => { chosen = name; window.picked = name; };
      rows.appendChild(row);
    }
  });
  const go = document.createElement('div');
  go.setAttribute('role', 'button');
  go.textContent = 'Чат';
  go.onclick = () => {
    if (!chosen) return;
    dialog.remove();
    if (cfg.closed) {
      const note = document.createElement('div');
      note.textContent = "You can't message this account";
      document.body.appendChild(note);
      return;
    }
    history.pushState(null, '', '/direct/t/777/');
    document.body.innerHTML = '<div id="app"></div>';
    chat(chosen, 'page');
  };
  dialog.appendChild(go);
}
function profile() {
  document.body.innerHTML =
    '<main id="app"><h2>' + user + '</h2><div class="buttons"></div></main>';
  const buttons = document.querySelector('.buttons');
  if (cfg.follow) buttons.innerHTML += '<button type="button">Подписаться</button>';
  const addMessage = () => {
    const button = document.createElement('div');
    button.setAttribute('role', 'button');
    button.tabIndex = 0;
    button.textContent = 'Отправить сообщение';
    button.onclick = () => setTimeout(() => {
      if (cfg.picker) return picker();
      if (cfg.chat === 'popup') return chat(cfg.chatUser || user, 'popup');
      history.pushState(null, '', '/direct/t/340282366/');
      document.body.innerHTML = '<div id="app"></div>';
      chat(cfg.chatUser || user, 'page');
    }, 200);
    buttons.appendChild(button);
  };
  if (cfg.message !== false) {
    if (cfg.messageDelay) setTimeout(addMessage, cfg.messageDelay); else addMessage();
  }
  if (cfg.otherChat) chat('someoneelse', 'popup');
}
const path = location.pathname;
if (path.startsWith('/direct/new')) { document.body.innerHTML = ''; picker(); }
else if (path.startsWith('/direct/inbox')) { document.body.innerHTML = ''; }
else if (path.startsWith('/direct/t/')) {
  document.body.innerHTML = '<div id="app"></div>';
  chat(cfg.chatUser || user, 'page');
} else profile();
if (cfg.notice) {
  const notice = document.createElement('div');
  notice.setAttribute('role', 'dialog');
  notice.innerText = cfg.notice;
  document.body.appendChild(notice);
}
</script></body>"""

USER = "jaycarter"
TEXT = "Yo Jay\nlets work"
INITIAL = "Hey Jay, checked your music out"


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
    for name, value in {
        "PROFILE_WAIT": 3.0,
        "PROFILE_RETRY_WAIT": 1.0,
        "PICKER_WAIT": 2.0,
        "DIALOG_WAIT": 3.0,
        "CONFIRM_WAIT": 2.5,
        "SETTLE": 0.4,
        "HISTORY_WAIT": 0.6,
    }.items():
        monkeypatch.setattr(direct_message, name, value)


def stand_in(browser, url=f"https://www.instagram.com/{USER}/", **cfg):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    html = PAGE.replace("__CFG__", json.dumps({"user": USER, **cfg}))
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(url)
    return page


def sent(page):
    return page.evaluate("window.sent")


def limited(page):
    return lambda: bool(page.evaluate("!!window.limited"))


# ---------- what counts as sent ----------


def test_sent_only_with_a_new_message_in_the_chat(browser):
    page = stand_in(browser)
    result = send_direct(page, USER, TEXT)
    assert result == {"outcome": "sent", "thread_id": "340282366", "confirmed": "interface"}
    assert sent(page) == [[USER, TEXT]]
    page.close()


def test_a_cleared_box_without_a_new_message_is_unconfirmed(browser):
    page = stand_in(browser, enter="clear")
    result = send_direct(page, USER, TEXT)
    assert result["error"] == "unconfirmed" and result["evidence"] == "no_message"
    # Enter was pressed once; nothing is sent twice.
    assert sent(page) == [[USER, TEXT]]
    page.close()


def test_a_chat_window_over_the_profile(browser):
    page = stand_in(browser, chat="popup")
    assert send_direct(page, USER, TEXT)["outcome"] == "sent"
    page.close()
    page = stand_in(browser, chat="popup", enter="clear")
    assert send_direct(page, USER, TEXT)["error"] == "unconfirmed"
    page.close()


def test_a_late_message_is_waited_for(browser):
    page = stand_in(browser, enter="late")
    assert send_direct(page, USER, TEXT)["outcome"] == "sent"
    page.close()


def test_a_message_still_sending_is_unconfirmed(browser):
    page = stand_in(browser, enter="pending")
    result = send_direct(page, USER, TEXT)
    assert result["error"] == "unconfirmed" and result["evidence"] == "pending"
    page.close()


def test_an_earlier_identical_message_never_confirms(browser):
    """A follow-up with the same words as our previous message: only a new one counts."""
    history = [[TEXT, "out"]]
    page = stand_in(browser, history=history, enter="clear")
    result = send_direct(page, USER, TEXT, mode="followup", previous=[TEXT])
    assert result["error"] == "unconfirmed"
    page.close()
    page = stand_in(browser, history=history)
    assert send_direct(page, USER, TEXT, mode="followup", previous=[TEXT])["outcome"] == "sent"
    page.close()


def test_an_explicit_error_is_a_refusal(browser):
    page = stand_in(browser, enter="fail")
    assert send_direct(page, USER, TEXT)["error"] == "rejected"
    page.close()
    page = stand_in(browser, enter="fail", limitOnEnter=True)
    assert send_direct(page, USER, TEXT, rate_limited=limited(page))["error"] == "rate_limited"
    page.close()


def test_a_429_without_an_error_text_pauses_the_account_and_stays_unconfirmed(browser):
    page = stand_in(browser, enter="clear", limitOnEnter=True)
    result = send_direct(page, USER, TEXT, rate_limited=limited(page))
    assert result["error"] == "unconfirmed" and result["account"] == "rate_limited"
    page.close()
    # Shown as sent and a 429 on the side: sent, and the account is still reported.
    page = stand_in(browser, limitOnEnter=True)
    result = send_direct(page, USER, TEXT, rate_limited=limited(page))
    assert result["outcome"] == "sent" and result["account"] == "rate_limited"
    page.close()


def test_an_unreadable_page_after_enter_is_unconfirmed(browser, monkeypatch):
    original = DirectSender.state

    def unreadable(self, handle):
        if self.page.evaluate("window.sent.length"):
            raise RuntimeError("Execution context was destroyed")
        return original(self, handle)

    monkeypatch.setattr(DirectSender, "state", unreadable)
    page = stand_in(browser)
    result = send_direct(page, USER, TEXT)
    assert result["error"] == "unconfirmed" and result["evidence"] == "unreadable"
    assert sent(page) == [[USER, TEXT]]
    page.close()


def test_a_chat_closed_after_enter_is_unconfirmed(browser):
    page = stand_in(browser, chat="popup", enter="close")
    result = send_direct(page, USER, TEXT)
    assert result["error"] == "unconfirmed" and result["evidence"] == "composer_lost"
    page.close()


def test_a_browser_closed_after_enter_is_unconfirmed(browser, monkeypatch):
    original = DirectSender.state

    def closing(self, handle):
        if self.page.evaluate("window.sent.length"):
            self.page.close()
        return original(self, handle)

    monkeypatch.setattr(DirectSender, "state", closing)
    page = stand_in(browser)
    assert send_direct(page, USER, TEXT)["error"] == "unconfirmed"


# ---------- stops ----------


def test_a_stop_before_enter_sends_nothing(browser):
    """The core stops the job once the whole text is typed: Enter is never pressed."""
    page = stand_in(browser)
    words = "Yo Jay lets work"

    def guard():
        typed = page.evaluate("document.querySelector('[role=textbox]')?.innerText || ''")
        return "CAMPAIGN_CANCELLED" if typed.strip() == words else None

    result = send_direct(page, USER, words, guard=guard)
    assert result == {"outcome": "error", "error": "stopped", "reason": "CAMPAIGN_CANCELLED"}
    assert sent(page) == []
    # The typed draft does not stay in the box.
    assert page.evaluate("document.querySelector('[role=textbox]').innerText").strip() == ""
    page.close()


def test_a_stop_at_the_start_opens_nothing(browser):
    page = stand_in(browser)
    result = send_direct(page, USER, TEXT, guard=lambda: "DO_NOT_CONTACT")
    assert result["error"] == "stopped" and result["reason"] == "DO_NOT_CONTACT"
    assert page.url.endswith(f"/{USER}/") and sent(page) == []
    page.close()


def test_a_stop_after_enter_keeps_the_real_result(browser):
    page = stand_in(browser)
    result = send_direct(
        page,
        USER,
        TEXT,
        guard=lambda: "CAMPAIGN_CANCELLED" if page.evaluate("window.sent.length") else None,
    )
    assert result["outcome"] == "sent"
    page.close()


# ---------- the account's state ----------


@pytest.mark.parametrize(
    ("notice", "kind"),
    [
        (
            "Try Again Later\nWe restrict certain activity to protect our community.",
            "action_blocked",
        ),
        ("Enter the 6-digit code we sent to your email", "code_required"),
        ("Add phone number\nTo keep your account secure", "phone_required"),
        ("Change your password to keep your account safe", "password_reset"),
        ("Help us confirm it's you", "checkpoint"),
        ("Your session has expired. Please log in again.", "session_expired"),
    ],
)
def test_account_requests_stop_before_anything_is_typed(browser, notice, kind):
    page = stand_in(browser, notice=notice)
    assert send_direct(page, USER, TEXT)["error"] == kind
    assert sent(page) == []
    page.close()


def test_account_pages_and_limits_before_opening(browser):
    page = stand_in(browser, url="https://www.instagram.com/challenge/abc/")
    assert send_direct(page, USER, TEXT)["error"] == "checkpoint"
    page.close()
    page = stand_in(browser, url="https://www.instagram.com/accounts/password/reset/")
    assert send_direct(page, USER, TEXT)["error"] == "password_reset"
    page.close()
    page = stand_in(browser)
    assert send_direct(page, USER, TEXT, rate_limited=lambda: True)["error"] == "rate_limited"
    assert sent(page) == []
    page.close()


def test_words_in_a_chat_are_not_taken_for_a_warning(browser):
    """A chat window is a dialog on some accounts: its messages are not notices."""
    page = stand_in(
        browser, chat="popup", popupDialog=True, history=[["try again later bro", "in"]]
    )
    assert send_direct(page, USER, TEXT)["error"] == "existing_thread"
    page.close()


# ---------- opening the thread ----------


def test_a_slow_message_button_is_waited_for(browser):
    page = stand_in(browser, follow=True, messageDelay=2000)
    assert send_direct(page, USER, TEXT)["outcome"] == "sent"
    page.close()


def test_follow_only_is_closed_only_when_direct_refuses_too(browser):
    page = stand_in(
        browser, follow=True, message=False, results=[[USER, "Jay Carter"]], closed=True
    )
    assert send_direct(page, USER, TEXT)["error"] == "messages_closed"
    assert sent(page) == []
    page.close()


def test_direct_opens_the_exact_username_when_the_profile_has_no_button(browser):
    results = [["jaycarter.fan", USER], [USER, "Jay Carter"]]
    page = stand_in(browser, follow=True, message=False, results=results)
    result = send_direct(page, USER, TEXT)
    assert result["outcome"] == "sent" and result["thread_id"] == "777"
    assert page.evaluate("window.picked") == USER and sent(page) == [[USER, TEXT]]
    page.close()


def test_the_recipient_picker_after_the_message_button(browser):
    page = stand_in(browser, picker=True, results=[[USER, "Jay Carter"]])
    assert send_direct(page, USER, TEXT)["outcome"] == "sent"
    assert sent(page) == [[USER, TEXT]]
    page.close()


def test_a_picker_without_the_recipient_sends_nothing(browser):
    page = stand_in(browser, picker=True, results=[["someoneelse", "Some One"]])
    assert send_direct(page, USER, TEXT)["outcome"] == "error"
    assert sent(page) == []
    page.close()


def test_several_chat_windows_write_only_to_the_recipient(browser):
    page = stand_in(browser, chat="popup", otherChat=True)
    assert send_direct(page, USER, TEXT)["outcome"] == "sent"
    assert sent(page) == [[USER, TEXT]]
    page.close()


def test_a_chat_of_someone_else_sends_nothing(browser):
    page = stand_in(browser, chatUser="someoneelse")
    assert send_direct(page, USER, TEXT)["error"] == "wrong_recipient"
    assert sent(page) == []
    page.close()


def test_an_unverifiable_chat_next_to_another_one_sends_nothing(browser):
    page = stand_in(browser, chat="popup", otherChat=True, noHeader=True)
    assert send_direct(page, USER, TEXT)["outcome"] == "error"
    assert sent(page) == []
    page.close()


# ---------- initial outreach and follow-ups ----------


def test_initial_outreach_never_writes_into_an_existing_thread(browser):
    page = stand_in(browser, history=[[INITIAL, "out"]])
    assert send_direct(page, USER, TEXT)["error"] == "existing_thread"
    assert sent(page) == []
    page.close()


def test_a_followup_goes_into_its_own_thread(browser):
    page = stand_in(
        browser,
        url="https://www.instagram.com/direct/t/340282366/",
        history=[[INITIAL, "out"], ["Seen", "out"]],
    )
    result = send_direct(page, USER, TEXT, mode="followup", previous=[INITIAL])
    assert result["outcome"] == "sent" and result["thread_id"] == "340282366"
    assert sent(page) == [[USER, TEXT]]
    page.close()


def test_a_followup_from_the_profile_button(browser):
    page = stand_in(browser, history=[[INITIAL, "out"]])
    assert send_direct(page, USER, TEXT, mode="followup", previous=[INITIAL])["outcome"] == "sent"
    page.close()


def test_a_reply_seen_before_typing_stops_the_followup(browser):
    page = stand_in(browser, history=[[INITIAL, "out"], ["yo send it", "in"]])
    result = send_direct(page, USER, TEXT, mode="followup", previous=[INITIAL])
    assert result["error"] == "reply_detected" and result["replies"] == ["yo send it"]
    assert sent(page) == []
    page.close()


def test_an_old_recorded_reply_is_not_new(browser):
    page = stand_in(browser, history=[["ok", "in"], [INITIAL, "out"], ["ok", "in"]])
    result = send_direct(
        page, USER, TEXT, mode="followup", previous=[INITIAL], known_inbound=["ok"]
    )
    assert result["outcome"] == "sent"
    page.close()


def test_a_thread_without_our_previous_message_is_not_written_to(browser):
    page = stand_in(browser, history=[["something else entirely", "out"]])
    result = send_direct(page, USER, TEXT, mode="followup", previous=[INITIAL])
    assert result["error"] == "thread_mismatch"
    assert sent(page) == []
    page.close()
