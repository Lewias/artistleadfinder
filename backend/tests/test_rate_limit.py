from artist_lead_finder.chromium_runtime import ChromiumRuntime, Window, watch_rate_limits

PROFILE = "a" * 32


class FakeResponse:
    def __init__(self, url, status):
        self.url, self.status = url, status


class FakeContext:
    def __init__(self):
        self.handlers = []
        self.pages = []

    def on(self, event, handler):
        assert event == "response"
        self.handlers.append(handler)

    def emit(self, url, status):
        for handler in self.handlers:
            handler(FakeResponse(url, status))

    def cookies(self, url):
        return []


class FakePage:
    def __init__(self, context):
        self.context = context

    def is_closed(self):
        return False

    def goto(self, url, **kwargs):
        self.context.emit(url, 429 if "limited" in url else 200)

    def evaluate(self, script):
        return {"url": "https://www.instagram.com/x/", "ready": True, "blocked": False}


class FakeBrowser:
    def is_connected(self):
        return True


def runtime_with_window():
    context = FakeContext()
    window = Window(FakeBrowser(), context, FakePage(context))
    watch_rate_limits(window)
    runtime = ChromiumRuntime(sessions=None)
    runtime.windows[PROFILE] = window
    return runtime, context


def test_instagram_429_marks_page_results_as_rate_limited_until_next_navigation():
    runtime, context = runtime_with_window()
    assert runtime.navigate(PROFILE, "https://www.instagram.com/limited/")["rate_limited"] is True
    assert runtime.evaluate(PROFILE, "() => ({})") == {
        "url": "https://www.instagram.com/x/",
        "ready": False,
        "blocked": True,
        "rate_limited": True,
    }
    assert runtime.navigate(PROFILE, "https://www.instagram.com/ok/")["rate_limited"] is False
    assert runtime.evaluate(PROFILE, "() => ({})")["ready"] is True
    # A throttled API call made by the loaded page counts too; other hosts do not.
    context.emit("https://cdn.example.com/x.js", 429)
    assert runtime.evaluate(PROFILE, "() => ({})")["ready"] is True
    context.emit("https://www.instagram.com/api/graphql", 429)
    assert runtime.evaluate(PROFILE, "() => ({})")["rate_limited"] is True
