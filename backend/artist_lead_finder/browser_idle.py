"""Closing an account's browser window once the app has no more work for it.

One window serves the parser, the outreach send queue, reading the Direct threads and the
«Найти и написать» chain. The shell asks every few seconds (with the send queue); a window
the app worked in is closed, cookies saved, once nothing of its account is left: no parser
run (a paused one keeps it, it may need a login), no unfinished send, no Direct reading, no
autopilot chain holding it. A short grace covers the gap between a parser run and the
outreach that starts after it. A window that never ran app work (opened by hand to log in
or look around) is left alone.
"""

import logging
import time
from collections.abc import Callable

from sqlalchemy import select

from .autopilot import ACTIVE as AUTOPILOT_ACTIVE
from .autopilot import KEY as AUTOPILOT_KEY
from .autopilot import _profiles as autopilot_profiles
from .models import (
    BrowserQueue,
    InboxScan,
    OutboundMessageJob,
    OutreachCampaign,
    ScoutRun,
    SearchJob,
    Setting,
)
from .outreach.campaigns import UNFINISHED

log = logging.getLogger(__name__)

# Seconds a worked window stays open with nothing to do before it is closed.
IDLE_GRACE = 30.0
OPEN_JOBS = ("queued", "running", "paused")
OPEN_SENDS = ("pending", "ready", "sending")


def window_work(session, profile_id: str) -> tuple[bool, bool]:
    """(busy, worked): whether app work of the account is open now, and whether that work
    is of the kind that makes the window the app's (a parser run, a send, Direct reading;
    a one-off capture of the page open in the window is not)."""
    jobs = session.execute(
        select(SearchJob.id, ScoutRun.job_id)
        .join(BrowserQueue, BrowserQueue.job_id == SearchJob.id)
        .outerjoin(ScoutRun, ScoutRun.job_id == SearchJob.id)
        .where(BrowserQueue.profile_id == profile_id, SearchJob.status.in_(OPEN_JOBS))
    ).all()
    if any(scout is not None for _, scout in jobs):
        return True, True
    sends = session.scalar(
        select(OutboundMessageJob.id)
        .join(OutreachCampaign, OutreachCampaign.id == OutboundMessageJob.campaign_id)
        .where(
            OutboundMessageJob.sender_account_id == profile_id,
            OutboundMessageJob.status.in_(OPEN_SENDS),
            OutreachCampaign.status.in_(UNFINISHED),
        )
        .limit(1)
    )
    reading = session.scalar(
        select(InboxScan.id)
        .where(InboxScan.sender_account_id == profile_id, InboxScan.status == "running")
        .limit(1)
    )
    if sends or reading:
        return True, True
    row = session.get(Setting, AUTOPILOT_KEY)
    chain = row.value if row and isinstance(row.value, dict) else None
    if (
        chain
        and chain.get("status") in AUTOPILOT_ACTIVE
        and profile_id in autopilot_profiles(chain)
    ):
        return True, True
    return bool(jobs), False


class IdleCloser:
    def __init__(
        self,
        sessions,
        chromium,
        enabled: Callable[[], bool],
        now: Callable[[], float] = time.monotonic,
    ):
        self.sessions = sessions
        self.chromium = chromium
        self.enabled = enabled
        self.now = now

    def tick(self, params: dict | None = None) -> dict:
        closed = []
        if not self.chromium.windows:
            return {"closed": closed}
        on = self.enabled()
        with self.sessions() as session:
            work = {
                identifier: window_work(session, identifier)
                for identifier in list(self.chromium.windows)
            }
        for identifier, (busy, worked) in work.items():
            window = self.chromium.windows.get(identifier)
            if window is None:
                continue
            if busy:
                window.worked = window.worked or worked
                window.idle_since = None
                continue
            if not (on and window.worked):
                continue
            if window.idle_since is None:
                window.idle_since = self.now()
                continue
            if self.now() - window.idle_since >= IDLE_GRACE:
                self.chromium.close(identifier)
                closed.append(identifier)
                log.info("browser_closed_when_idle")
        return {"closed": closed}
