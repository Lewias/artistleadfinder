"""The app's core for one user on the server: its own folder with the scout database,
memory and browser session, the way the desktop app keeps a signed-in account.

The folder outlives the job, so the scout remembers processed posts, known leads and the
source rotation of that user from run to run.
"""

import logging
import re
from pathlib import Path

from artist_lead_finder.cloud import source_url
from artist_lead_finder.database import open_database
from artist_lead_finder.models import Lead, LeadScoutProfile, ScoutDecision
from artist_lead_finder.service import ApplicationService
from sqlalchemy import select

log = logging.getLogger(__name__)

PROFILE_ID = re.compile(r"^[0-9a-f]{32}$")
OWNER_ID = re.compile(r"^[0-9a-f-]{36}$")
CATEGORY = {"artist": "ARTIST", "producer": "PRODUCER", "media": "MEDIA", "other": "OTHER"}
# Scout settings a job may set; pacing and limits that protect the account stay as the
# user set them in the app, everything else of the app is not touched.
SETTING = re.compile(r"^scout_[a-z_]+$")
EXCLUDED_SETTINGS = {"scout_ai_mode", "scout_debug"}
OUTREACH_SETTING = re.compile(r"^outreach_[a-z_]+$")


class Engine:
    def __init__(self, data_root: Path, owner_id: str):
        if not OWNER_ID.match(owner_id):
            raise ValueError("bad owner")
        self.folder = data_root / owner_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.engine, self.sessions = open_database(self.folder / "artist-leads.sqlite3")
        self.service = ApplicationService(self.sessions, self.folder)
        self.seen = 0
        # The core method in progress, for the watchdog's log when a call hangs.
        self.calling: str | None = None

    def call(self, method: str, params: dict):
        self.calling = method
        try:
            return self.service.call(method, params)
        finally:
            self.calling = None

    def close(self) -> None:
        try:
            self.service.shutdown()
        finally:
            self.engine.dispose()

    # ---------- preparing ----------

    def put_session(self, profile_id: str, name: str, record: dict) -> None:
        if not PROFILE_ID.match(profile_id):
            raise ValueError("bad profile")
        self.service.browser_sessions.write(
            profile_id,
            {
                "name": (name or "Instagram")[:80],
                "cookies": record.get("cookies") or [],
                "proxy": record.get("proxy") or None,
            },
        )

    def session(self, profile_id: str) -> dict:
        record = self.service.browser_sessions.read(profile_id)
        return {"cookies": record.get("cookies") or [], "proxy": record.get("proxy")}

    def apply_settings(self, settings: dict) -> None:
        current = self.call("settings.get", {})
        changes = {
            key: value
            for key, value in (settings or {}).items()
            if SETTING.match(key) and key in current and key not in EXCLUDED_SETTINGS
        }
        # The cloud has no AI key: classification stays local, as in the app without a key.
        changes["scout_ai_mode"] = "off"
        if changes:
            self.call("settings.save", {**current, **changes})

    def apply_outreach_settings(self, settings: dict) -> None:
        """The app's outreach pace and limits (interval, daily limit, retries, breaks)."""
        current = self.call("settings.get", {})
        changes = {
            key: value
            for key, value in (settings or {}).items()
            if OUTREACH_SETTING.match(key) and key in current
        }
        if changes:
            self.call("settings.save", {**current, **changes})

    def start(self, profile_id: str, sources: list[str], target: int) -> int:
        # Bare usernames (an older app sent them as typed) become profile links; anything
        # that is not a profile is left out rather than failing the whole job.
        urls = []
        for value in sources:
            try:
                url = source_url(str(value))
            except ValueError:
                log.warning("cloud_source_skipped %r", str(value)[:60])
                continue
            if url not in urls:
                urls.append(url)
        if not urls:
            raise ValueError("Ни один источник не похож на профиль Instagram.")
        sources = urls
        self.call(
            "scout.account_target", {"profile_id": profile_id, "target": target, "reset": True}
        )
        self.call("browser.runtime.open", {"id": profile_id})
        created = self.call("scout.start_internal", {"profile_id": profile_id, "sources": sources})
        job_id = int(created["id"])
        with self.sessions() as session:
            self.seen = (
                session.scalar(select(ScoutDecision.id).order_by(ScoutDecision.id.desc()).limit(1))
                or 0
            )
        return job_id

    def cancel_open_runs(self) -> None:
        """Runs left by a stopped process: they are not continued, a new one starts."""
        for job in self.call("jobs.list", {}) or []:
            if job.get("status") in ("running", "paused", "queued"):
                try:
                    self.call("jobs.control", {"id": job["id"], "action": "cancel"})
                except Exception:
                    pass

    # ---------- results ----------

    def new_leads(self, job_id: int) -> list[dict]:
        """Leads this run created since the last call, with what the scout learned."""
        with self.sessions() as session:
            rows = session.execute(
                select(ScoutDecision)
                .where(
                    ScoutDecision.job_id == job_id,
                    ScoutDecision.decision == "lead_created",
                    ScoutDecision.id > self.seen,
                )
                .order_by(ScoutDecision.id)
            ).scalars()
            found = []
            for decision in rows:
                self.seen = max(self.seen, decision.id)
                lead = session.scalar(
                    select(Lead).where(
                        Lead.platform == "instagram", Lead.username == decision.username
                    )
                )
                if lead is None:
                    continue
                profile = session.scalar(
                    select(LeadScoutProfile).where(LeadScoutProfile.lead_id == lead.id)
                )
                found.append(candidate(lead, profile, decision))
        return found


def candidate(lead: Lead, profile: LeadScoutProfile | None, decision: ScoutDecision) -> dict:
    """A lead as the CRM save takes it (cloud_worker.crm.save)."""
    kind = (profile.profile_type if profile else None) or decision.category or ""
    links = []
    if profile and profile.bio_links:
        links = [link for link in profile.bio_links if isinstance(link, str)][:10]
    if lead.external_url and lead.external_url not in links:
        links.insert(0, lead.external_url)
    reasons = (profile.profile_reasons if profile else None) or []
    return {
        "username": lead.username.lower(),
        "instagram_id": (profile.instagram_id if profile else None) or lead.platform_user_id,
        "via": [profile.discovery_method] if profile and profile.discovery_method else [],
        "origins": [
            {
                "via": profile.discovery_method if profile else None,
                "source": profile.source_username if profile else decision.source_username,
                "post": profile.origin_url if profile else None,
            }
        ],
        "category": CATEGORY.get(kind, "UNKNOWN"),
        "confidence": (profile.profile_confidence if profile else None) or decision.confidence,
        "reason": "; ".join(str(item) for item in reasons[:4])[:300] or None,
        "ai_ok": True,
        "profile": {
            "instagram_id": profile.instagram_id if profile else None,
            "username": lead.username.lower(),
            "full_name": lead.display_name or None,
            "biography": lead.bio or None,
            "links": links,
            "emails": list(profile.emails or []) if profile else [],
            "phones": list(profile.phones or []) if profile else [],
            "followers": lead.followers,
            "following": lead.following,
            "posts": profile.posts_count if profile else None,
            "verified": lead.is_verified,
            "private": lead.is_private,
            "business": profile.is_business if profile else None,
            "category": profile.category_name if profile else None,
        },
    }
