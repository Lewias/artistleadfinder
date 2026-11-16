from artist_lead_finder.chromium_runtime import ChromiumRuntime, Window


class Page:
    def __init__(self, url, found=None):
        self.url, self.found = url, found or {}

    def is_closed(self):
        return False

    def evaluate(self, script):
        return self.found


class Browser:
    def is_connected(self):
        return True

    def close(self):
        pass


class Context:
    def __init__(self, pages):
        self.pages = pages

    def cookies(self, urls):
        return []


class Sessions:
    def __init__(self):
        self.saved = []

    def call(self, method, params):
        self.saved.append(params)
        return {"ok": True}


def test_login_follows_the_threads_popup_and_keeps_the_instagram_tab():
    sessions = Sessions()
    runtime = ChromiumRuntime(sessions)
    instagram = Page("https://www.instagram.com/")
    original = Page("https://www.threads.com/login")
    popup = Page("https://www.threads.com/", {"username": "artist"})
    window = Window(Browser(), Context([instagram, original, popup]), instagram)
    window.threads_page = original
    runtime.windows["a" * 32] = window
    try:
        assert runtime.threads_identity("a" * 32) == {"username": "artist"}
        assert window.threads_page is popup
        assert window.page is instagram
        assert "threads_cookies" in sessions.saved[-1]
    finally:
        runtime.shutdown()


def test_loading_threads_tab_still_saves_new_session_cookies():
    sessions = Sessions()
    runtime = ChromiumRuntime(sessions)
    instagram = Page("https://www.instagram.com/")
    threads = Page("https://www.threads.com/")
    runtime.windows["a" * 32] = Window(Browser(), Context([instagram, threads]), instagram)
    try:
        assert not runtime.threads_identity("a" * 32).get("username")
        assert "threads_cookies" in sessions.saved[-1]
    finally:
        runtime.shutdown()
