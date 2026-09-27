from artist_lead_finder.chromium_runtime import GUEST_ID, SEARCH_ID, ChromiumRuntime, Window

PROFILE = "a" * 32
COOKIES = [{"name": "sessionid", "value": "x", "domain": ".instagram.com", "path": "/"}]


class FakeBrowser:
    def __init__(self, connected=True):
        self.connected, self.closed = connected, False

    def is_connected(self):
        return self.connected

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, fail=False):
        self.fail = fail
        self.pages = []

    def cookies(self, url):
        if self.fail:
            raise RuntimeError("Target closed")
        return COOKIES


class FakePage:
    def __init__(self, closed):
        self.closed = closed

    def is_closed(self):
        return self.closed


class FakeSessions:
    def __init__(self):
        self.saved = []

    def call(self, method, params):
        assert method == "browser.save_internal"
        self.saved.append(params)
        return {"ok": True}


def runtime_with(identifier, **window):
    sessions = FakeSessions()
    runtime = ChromiumRuntime(sessions)
    browser = FakeBrowser(window.get("connected", True))
    runtime.windows[identifier] = Window(
        browser, FakeContext(window.get("fail", False)), FakePage(window.get("closed", True))
    )
    return runtime, sessions, browser


def test_closed_window_saves_session_then_closes_browser():
    runtime, sessions, browser = runtime_with(PROFILE)
    assert runtime.is_open(PROFILE) is False
    assert sessions.saved == [{"id": PROFILE, "cookies": COOKIES}]
    assert browser.closed and PROFILE not in runtime.windows


def test_open_window_is_not_saved_and_app_exit_saves():
    runtime, sessions, browser = runtime_with(PROFILE, closed=False)
    assert runtime.is_open(PROFILE) is True
    assert sessions.saved == []
    runtime.shutdown()
    assert sessions.saved == [{"id": PROFILE, "cookies": COOKIES}] and browser.closed


def test_guest_search_disconnected_and_failures_do_not_save():
    for identifier, window in [
        (GUEST_ID, {}),
        (SEARCH_ID, {}),
        (PROFILE, {"connected": False}),
        (PROFILE, {"fail": True}),
    ]:
        runtime, sessions, browser = runtime_with(identifier, **window)
        assert runtime.is_open(identifier) is False
        assert sessions.saved == [] and browser.closed


def test_closed_tab_switches_to_remaining_tab():
    runtime, sessions, browser = runtime_with(PROFILE)
    other = FakePage(closed=False)
    runtime.windows[PROFILE].context.pages = [runtime.windows[PROFILE].page, other]
    assert runtime.is_open(PROFILE) is True
    assert runtime.windows[PROFILE].page is other
    assert sessions.saved == [] and not browser.closed
