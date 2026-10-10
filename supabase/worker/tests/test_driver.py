"""The page loop against a stand-in core: it does what the desktop shell's loop does."""

from cloud_worker.driver import Driver, block_reason, page_script

SCRIPTS = {name: f"/*{name}*/" for name in ("capture", "scout", "grid", "follow")}
GRID = "https://www.instagram.com/rapgoat.tv/"
PROFILE = "https://www.instagram.com/jay.carter/"


class Core:
    """A queue with steps; each answer of the page decides what happens next."""

    def __init__(self, steps, pages=None, open_=True):
        self.steps = list(steps)
        self.pages = pages or {}
        self.open = open_
        self.calls = []
        self.cursor = 0
        self.status = "running"

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "capture.state":
            if self.cursor >= len(self.steps):
                return {"status": "completed", "stage": "done"}
            step = self.steps[self.cursor]
            return {
                "status": self.status,
                "stage": "scout",
                "profile_id": "p1",
                "scout": True,
                "attempt": 0,
                "wait_seconds": 0,
                **step,
            }
        if method == "browser.runtime.is_open":
            return {"open": self.open}
        if method == "browser.runtime.navigate":
            self.at = params["url"]
            return {"ok": True}
        if method == "browser.runtime.eval":
            return self.pages.get(self.at, {"ready": True, "blocked": False, "url": self.at})
        if method == "scout.commit_internal":
            self.cursor += 1
            return {"saved": True}
        if method == "capture.error_internal":
            self.status = "paused"
            return {"ok": True}
        return {"ok": True}

    def methods(self):
        return [method for method, _ in self.calls if method != "capture.state"]


class Time:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def drive(core, ticks=None):
    time = Time()
    driver = Driver(core, SCRIPTS, sleep=time.sleep, clock=time.clock)
    rounds = [0]

    def tick(state):
        rounds[0] += 1
        if ticks is not None and rounds[0] > ticks:
            return "stop"
        return None

    return driver.run(7, tick), time


def test_each_page_is_opened_read_after_it_settles_and_committed():
    core = Core([{"kind": "source", "url": GRID}, {"kind": "profile", "url": PROFILE}])
    final, time = drive(core)
    assert final["status"] == "completed"
    assert [m for m in core.methods() if m != "browser.runtime.is_open"] == [
        "browser.runtime.navigate",
        "browser.runtime.eval",
        "scout.commit_internal",
        "browser.runtime.navigate",
        "browser.runtime.eval",
        "scout.commit_internal",
    ]
    evals = [params for method, params in core.calls if method == "browser.runtime.eval"]
    assert evals[0]["script"] == "/*grid*/" and evals[0]["feed"] is True
    assert evals[1]["script"] == "/*capture*/" and evals[1]["feed"] is False
    # The page settles 3 s before it is read, and pages are 3 s apart.
    assert time.now >= 9


def test_a_step_from_the_cache_opens_no_page():
    core = Core([{"kind": "profile", "url": PROFILE, "access": "none"}])
    drive(core)
    assert core.methods().count("browser.runtime.navigate") == 0
    commit = [p for m, p in core.calls if m == "scout.commit_internal"][0]
    assert commit["snapshot"] == {"url": PROFILE, "ready": True, "blocked": False}


def test_the_core_waits_are_kept():
    core = Core([{"kind": "profile", "url": PROFILE, "wait_seconds": 40}])
    drive(core, ticks=20)
    assert "browser.runtime.navigate" not in core.methods()


def test_a_blocked_page_reports_its_reason_and_a_dead_page_times_out():
    blocked = {PROFILE: {"ready": False, "blocked": True, "block_reason": "checkpoint"}}
    core = Core([{"kind": "profile", "url": PROFILE}], pages=blocked)
    drive(core, ticks=30)
    assert ("capture.error_internal", {"id": 7, "reason": "checkpoint"}) in core.calls

    stuck = {PROFILE: {"ready": True, "blocked": False, "url": "https://www.instagram.com/x/"}}
    core = Core([{"kind": "profile", "url": PROFILE}], pages=stuck)
    drive(core, ticks=100)
    assert ("capture.error_internal", {"id": 7, "reason": "loading"}) in core.calls
    assert "scout.commit_internal" not in core.methods()


def test_a_closed_window_is_reported():
    core = Core([{"kind": "profile", "url": PROFILE}], open_=False)
    drive(core, ticks=5)
    assert ("capture.error_internal", {"id": 7, "reason": "closed"}) in core.calls


def test_script_choice_and_block_reasons_match_the_shell():
    assert page_script({"scout": True, "kind": "tagged_grid"}) == "grid"
    assert page_script({"scout": True, "kind": "followers"}) == "follow"
    assert page_script({"scout": True, "kind": "post"}) == "scout"
    assert page_script({"scout": False, "kind": "post"}) == "capture"
    assert block_reason({"rate_limited": True}) == "rate_limited"
    assert block_reason({"block_reason": "weird"}) == "blocked"
