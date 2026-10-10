"""The page loop of a Lead Scout queue, as the desktop shell runs it (src-tauri/src/main.rs,
`run_browser_queue`), for a Chromium on the server.

The core decides everything (which page, when, what the result means); the loop only opens
the page, runs the page script and hands the snapshot back, pausing between pages the way
the core asks. `call(method, params)` reaches the core in-process.
"""

import json
import time
from collections.abc import Callable
from pathlib import Path

GRID_KINDS = ("source", "tagged_grid")
FOLLOW_KINDS = ("followers", "following")
STOP_REASONS = ("login", "checkpoint", "rate_limited", "unavailable")
MAX_SNAPSHOT = 1_000_000
NAVIGATE_SETTLE = 3.0
COOLDOWN = 3.0
PAGE_TIMEOUT = 25.0
TICK = 0.5


def load_scripts(folder: Path) -> dict[str, str]:
    """The page scripts the desktop shell embeds (capture, scout, grid, follow)."""
    return {
        name: (folder / f"{name}.js").read_text(encoding="utf-8")
        for name in ("capture", "scout", "grid", "follow")
    }


def page_script(state: dict) -> str:
    scout, kind = state.get("scout") is True, state.get("kind") or ""
    if scout and kind in GRID_KINDS:
        return "grid"
    if scout and kind in FOLLOW_KINDS:
        return "follow"
    if (scout and kind == "profile") or not scout:
        return "capture"
    return "scout"


def reads_feed(state: dict) -> bool:
    return state.get("scout") is True and state.get("kind") in GRID_KINDS


def block_reason(snapshot: dict) -> str:
    reason = snapshot.get("block_reason")
    if reason in STOP_REASONS:
        return reason
    if snapshot.get("rate_limited") is True:
        return "rate_limited"
    return "blocked"


def same_url(actual: str, wanted: str) -> bool:
    return actual.split("?")[0].split("#")[0].rstrip("/") == wanted.rstrip("/")


class Driver:
    def __init__(
        self,
        call: Callable[[str, dict], dict],
        scripts: dict[str, str],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.call = call
        self.scripts = scripts
        self.sleep = sleep
        self.clock = clock

    def _try(self, method: str, params: dict):
        try:
            return self.call(method, params)
        except Exception:
            return None

    def error(self, job_id: int, reason: str) -> None:
        self._try("capture.error_internal", {"id": job_id, "reason": reason})

    def run(
        self,
        job_id: int,
        tick: Callable[[dict], str | None],
    ) -> dict:
        """Drives the queue until it ends. `tick(state)` is called on every round and may
        answer "stop" (leave now), "skip" (wait this round) or None (go on)."""
        target = ""
        navigated = self.clock()
        cooldown = self.clock()
        attempt = 0
        while True:
            self.sleep(TICK)
            state = self.call("capture.state", {"id": job_id})
            decision = tick(state)
            if decision == "stop":
                return state
            if state.get("stage") == "interrupted" or state.get("status") not in (
                "running",
                "paused",
            ):
                return state
            if decision == "skip" or state["status"] == "paused":
                target = ""
                continue
            profile = state.get("profile_id") or ""
            opened = self._try("browser.runtime.is_open", {"id": profile}) or {}
            if opened.get("open") is not True:
                self.error(job_id, "closed")
                continue
            url = state.get("url")
            if not url:
                return state
            # The core retried a transient failure: open the page again.
            if int(state.get("attempt") or 0) != attempt:
                attempt = int(state.get("attempt") or 0)
                target = ""
            if state.get("access") == "none":
                # Served from the core's cache: no page, no pacing.
                snapshot = {"url": url, "ready": True, "blocked": False}
                if self._try("scout.commit_internal", {"id": job_id, "snapshot": snapshot}) is None:
                    self.error(job_id, "save")
                target = ""
                continue
            if target != url:
                # The core paces page opens and API requests (delays, hourly cap, 429 breaks).
                if self.clock() - cooldown < COOLDOWN or float(state.get("wait_seconds") or 0) > 0:
                    continue
                if self._try("browser.runtime.navigate", {"id": profile, "url": url}) is None:
                    self.error(job_id, "loading")
                    continue
                target = url
                navigated = self.clock()
                continue
            if self.clock() - navigated < NAVIGATE_SETTLE:
                continue
            snapshot = self._try(
                "browser.runtime.eval",
                {
                    "id": profile,
                    "script": self.scripts[page_script(state)],
                    "args": state.get("args"),
                    "feed": reads_feed(state),
                },
            )
            if snapshot is not None and len(json.dumps(snapshot, default=str)) > MAX_SNAPSHOT:
                snapshot = None
            if isinstance(snapshot, dict) and snapshot.get("blocked") is True:
                self.error(job_id, block_reason(snapshot))
            elif (
                isinstance(snapshot, dict)
                and snapshot.get("ready") is True
                and isinstance(snapshot.get("url"), str)
                and same_url(snapshot["url"], url)
            ):
                method = (
                    "scout.commit_internal" if state.get("scout") else "capture.commit_internal"
                )
                if self._try(method, {"id": job_id, "snapshot": snapshot}) is None:
                    self.error(job_id, "save")
                cooldown = self.clock()
                target = ""
            elif self.clock() - navigated > PAGE_TIMEOUT:
                self.error(job_id, "loading")
