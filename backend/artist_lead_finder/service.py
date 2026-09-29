"""Application API shared by local RPC and a future cloud transport."""

import csv
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .browser_capture import BrowserCaptureService
from .browser_sessions import BrowserSessions
from .chromium_runtime import ChromiumRuntime
from .discovery import DiscoveryEngine
from .jobs import DiscoveryManager
from .lead_scout import leads as scout_leads
from .lead_scout.ai import AIKeyStore, OpenRouterClassifier
from .lead_scout.decision import normalized_username
from .lead_scout.settings import ScoutSettings
from .models import (
    BrowserQueue,
    Lead,
    LeadAnalysis,
    LeadScoreBreakdown,
    LeadScoutProfile,
    LeadSource,
    ProviderHealth,
    ScoutAssessment,
    ScoutDecision,
    ScoutRun,
    SearchJob,
    Setting,
)
from .normalization import normalize
from .outreach import events as outreach_events
from .outreach.campaigns import CampaignService, iso
from .outreach.senders import current_status, sent_counts, set_status
from .outreach.settings import OutreachSettings, outreach_settings
from .outreach.worker import OutreachWorker
from .outreach.workspace import WorkspaceService
from .pacing import PacingSettings
from .pipeline import CandidatePipeline
from .providers import ImportedDatasetProvider, MockProvider
from .schemas import SearchConfiguration
from .scoring import ScoringWeights
from .scouting import ScoutService

log = logging.getLogger(__name__)


class LeadQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=30, ge=1, le=100)
    search: str = Field(default="", max_length=240)
    genre: str = Field(default="", max_length=80)
    status: Literal["", "new", "reviewed", "qualified", "rejected", "contacted"] = ""
    source: str = Field(default="", max_length=80)
    min_followers: int = Field(default=0, ge=0)
    max_followers: int = Field(default=1_000_000_000, ge=0)
    minimum_score: int = Field(default=0, ge=0, le=100)
    activity_days: int | None = Field(default=None, ge=1, le=3650)
    job_id: int | None = Field(default=None, ge=1)
    sort: Literal["score", "followers", "activity", "created"] = "score"
    descending: bool = True


def serialize(model) -> dict[str, Any]:
    result = {}
    for column in model.__table__.columns:
        value = getattr(model, column.name)
        if isinstance(value, datetime):
            value = value.replace(tzinfo=timezone.utc).isoformat()
        result[column.name] = value
    return result


def lead_statement(query: LeadQuery):
    stmt = select(Lead)
    if query.search:
        term = f"%{query.search.replace('/', '//').replace('%', '/%').replace('_', '/_')}%"
        stmt = stmt.where(
            or_(
                *(
                    field.ilike(term, escape="/")
                    for field in (Lead.username, Lead.display_name, Lead.bio)
                )
            )
        )
    if query.genre:
        stmt = stmt.where(Lead.primary_genre == query.genre)
    if query.status:
        stmt = stmt.where(Lead.status == query.status)
    stmt = stmt.where(
        Lead.followers.between(query.min_followers, query.max_followers),
        Lead.lead_score >= query.minimum_score,
    )
    if query.activity_days:
        stmt = stmt.where(
            Lead.last_activity_at
            >= datetime.now(timezone.utc) - timedelta(days=query.activity_days)
        )
    if query.source or query.job_id:
        sources = select(LeadSource.lead_id)
        if query.source:
            sources = sources.where(LeadSource.source_provider == query.source)
        if query.job_id:
            sources = sources.where(LeadSource.search_job_id == query.job_id)
        stmt = stmt.where(Lead.id.in_(sources))
    field = {
        "score": Lead.lead_score,
        "followers": Lead.followers,
        "activity": Lead.last_activity_at,
        "created": Lead.created_at,
    }[query.sort]
    return stmt.order_by(field.desc() if query.descending else field.asc(), Lead.id.desc())


def scout_summary(row: LeadScoutProfile | None) -> dict | None:
    """Lead Scout columns of the CRM table: type, confidence, contacts, first source."""
    if row is None:
        return None
    return {
        "profile_type": row.profile_type,
        "confidence": row.profile_confidence,
        "emails": row.emails,
        "phones": row.phones,
        "source_username": row.source_username,
        "discovery_method": row.discovery_method,
        "last_seen_at": serialize_time(row.last_seen_at),
    }


def serialize_time(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


DEFAULTS = {
    "min_followers": 1000,
    "max_followers": 50000,
    "activity_days": 30,
    "minimum_score": 70,
    "target_leads": 500,
    "enabled_providers": ["mock"],
    "weights": ScoringWeights().model_dump(),
    **PacingSettings().model_dump(),
    **ScoutSettings().model_dump(),
    **OutreachSettings().model_dump(),
}


class ApplicationService:
    def __init__(self, sessions: sessionmaker[Session], data_dir: Path) -> None:
        self.sessions = sessions
        self.data_dir = data_dir
        self.providers = [MockProvider()]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.browser_sessions = BrowserSessions(data_dir)
        self.chromium = ChromiumRuntime(self.browser_sessions)
        self.import_path = self.data_dir / "imported-profiles.json"
        if self.import_path.exists():
            try:
                self.providers.append(
                    ImportedDatasetProvider(self.import_path, max_bytes=100 * 1024 * 1024)
                )
            except (ValueError, OSError):
                log.warning("import_load_failed")
        self.pipeline = CandidatePipeline(sessions)
        self.discovery = DiscoveryEngine(self.providers)
        self.manager = DiscoveryManager(sessions, self.discovery, self.pipeline)
        self.browser_capture = BrowserCaptureService(sessions)
        self.ai_keys = AIKeyStore(data_dir)
        self.scout = ScoutService(
            sessions,
            self.browser_capture,
            self.settings,
            ai=OpenRouterClassifier(self.ai_keys),
            debug_sink=self._save_scout_debug,
        )
        # The job manager marked runs cut off by a crash as interrupted; finish that here.
        self.scout.recover_interrupted()
        # Outreach: campaigns and the send queue worked through the sender's browser.
        self.campaigns = CampaignService(sessions, self.settings, self._sender_names)
        self.outreach = OutreachWorker(sessions, self.settings, self.chromium.is_open)
        self.workspace = WorkspaceService(sessions, self.campaigns)
        # Sends cut off by a crash are checked against the message records, never resent.
        self.outreach.recover()
        with self.sessions.begin() as session:
            outreach_events.prune(session)
        self.handlers = self._handlers()

    def settings(self) -> dict:
        with self.sessions() as session:
            stored = {setting.key: setting.value for setting in session.scalars(select(Setting))}
        return {**DEFAULTS, **stored}

    def call(self, method: str, params: dict) -> Any:
        handler = self.handlers.get(method)
        if handler is None:
            if method.startswith("browser.runtime."):
                raise ValueError("Unknown browser runtime method")
            if method.startswith("browser."):
                return self.browser_sessions.call(method, params)
            raise ValueError("Неизвестный метод приложения.")
        return handler(params)

    def _handlers(self) -> dict[str, Callable[[dict], Any]]:
        chromium = self.chromium
        return {
            "browser.runtime.self_test": lambda p: chromium.self_test(),
            "browser.runtime.is_open": lambda p: {"open": chromium.is_open(p["id"])},
            "browser.runtime.open": lambda p: chromium.open(p["id"], p.get("proxy_override")),
            "browser.runtime.save": lambda p: chromium.save(p["id"]),
            "browser.runtime.navigate": lambda p: chromium.navigate(p["id"], p["url"]),
            "browser.runtime.eval": lambda p: chromium.evaluate(
                p["id"], p["script"], p.get("args"), fresh=bool(p.get("fresh"))
            ),
            "scout.sources": lambda p: self.scout.sources(p.get("sources")),
            "scout.start_internal": lambda p: self.scout.start(p, self.settings()),
            "scout.commit_internal": lambda p: self.scout.commit(int(p["id"]), p["snapshot"]),
            "scout.results": lambda p: self.scout.results(),
            "scout.skip": lambda p: self.scout.skip(int(p["id"])),
            "scout.accounts": self._scout_accounts,
            "scout.source_list": self.scout.source_rows,
            "scout.source_add": self.scout.add_sources,
            "scout.source_update": self.scout.update_source,
            "scout.source_remove": self.scout.remove_source,
            "scout.events": self.scout.event_list,
            "scout.runs": self._scout_runs,
            "scout.decisions": self._scout_decisions,
            "scout.ignore": self._scout_ignore,
            "outreach.workspace": self.workspace.state,
            "outreach.workspace_update": self.workspace.update,
            "outreach.workspace_add_leads": self.workspace.add_leads,
            "outreach.workspace_start": self.workspace.start,
            "outreach.workspace_stop": self.workspace.stop,
            "outreach.templates": self.campaigns.templates,
            "outreach.template_save": self.campaigns.save_template,
            "outreach.template_render": self.campaigns.render_template,
            "outreach.sequences": self.campaigns.sequences,
            "outreach.sequence_save": self.campaigns.save_sequence,
            "outreach.audience": self.campaigns.audience,
            "outreach.preview": self.campaigns.preview,
            "outreach.campaigns": self.campaigns.campaigns,
            "outreach.campaign": self.campaigns.campaign,
            "outreach.campaign_create": self.campaigns.create,
            "outreach.campaign_start": self.campaigns.start,
            "outreach.campaign_control": self.campaigns.control,
            "outreach.recipients": self.campaigns.recipients,
            "outreach.events": self.campaigns.event_list,
            "outreach.senders": self._outreach_senders,
            "outreach.sender_status": self._outreach_sender_status,
            "outreach.mark_replied": self.campaigns.mark_replied,
            "outreach.stop_conversation": self.campaigns.stop_conversation,
            "outreach.resolve_review": self.outreach.resolve_review,
            "outreach.next_internal": lambda p: self.outreach.next_job(),
            "outreach.commit_internal": self.outreach.commit,
            "leads.do_not_contact": self.campaigns.set_do_not_contact,
            "ai.status": lambda p: {"configured": self.ai_keys.configured()},
            "ai.set_key": self._set_ai_key,
            "scout.account_target": self.scout.set_target,
            "capture.start_internal": lambda p: self.browser_capture.start(p, self.settings()),
            "capture.commit_internal": lambda p: self.browser_capture.capture(
                int(p["id"]), p["snapshot"]
            ),
            "capture.error_internal": self._capture_error,
            "capture.state": lambda p: self.scout.state(int(p["id"])),
            "capture.latest": self._capture_latest,
            "system.info": self._system_info,
            "settings.get": lambda p: self.settings(),
            "settings.save": self._save_settings,
            "jobs.start": self._start_job,
            "jobs.control": self._control_job,
            "jobs.list": self._list_jobs,
            "jobs.detail": self._job_detail,
            "leads.list": self._list_leads,
            "leads.detail": self._lead_detail,
            "leads.status": self._set_lead_status,
            "providers.import": self._import_dataset,
            "providers.health": self._provider_health,
            "dashboard.get": lambda p: self.dashboard(),
            "leads.export": self.export,
        }

    def _sender_names(self) -> dict[str, str]:
        return {
            profile["id"]: profile["name"]
            for profile in self.browser_sessions.call("browser.list", {})
        }

    def _outreach_senders(self, params: dict) -> list[dict]:
        """Browser profiles as sender accounts, with health, window and 24 h volume."""
        profiles = self.browser_sessions.call("browser.list", {})
        settings = outreach_settings(self.settings())
        with self.sessions.begin() as session:
            counts = sent_counts(session, [profile["id"] for profile in profiles])
            result = []
            for profile in profiles:
                row = current_status(session, profile["id"])
                result.append(
                    {
                        "id": profile["id"],
                        "name": profile["name"],
                        "has_session": profile["cookie_count"] > 0,
                        "open": self.chromium.is_open(profile["id"]),
                        "status": row.status,
                        "reason": row.reason,
                        "until": iso(row.until),
                        "last_sent_at": iso(row.last_sent_at),
                        "sent_24h": counts.get(profile["id"], 0),
                        "daily_limit": settings.outreach_daily_limit_per_sender,
                    }
                )
            return result

    def _outreach_sender_status(self, params: dict) -> dict:
        """Manual sender state: resume after the user fixed a login / checkpoint, or
        pause / disable an account for outreach."""
        status = params.get("status")
        if status not in {"active", "paused", "disabled"}:
            raise ValueError("Неизвестное состояние аккаунта.")
        if params.get("id") not in self._sender_names():
            raise ValueError("Аккаунт не найден.")
        with self.sessions.begin() as session:
            set_status(session, params["id"], status, None if status == "active" else "Вручную")
        self.outreach.notified.pop(params["id"], None)
        return {"ok": True}

    def _capture_error(self, params: dict) -> dict:
        job_id, reason = int(params["id"]), params["reason"]
        # Scout decides first: retry a transient failure or skip an unavailable page.
        if self.scout.halt(job_id, reason):
            return {"ok": True, "continued": True}
        return {"ok": True}

    def _save_scout_debug(self, job_id: int, step: dict, payload: dict) -> None:
        """Scout debug artifact: URL, parser reason, sanitised fragment and a screenshot.

        Written only when Scout debug mode is on; never contains cookies or credentials.
        """
        folder = self.data_dir / "scout-debug"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        name = f"{stamp}-job{job_id}-{step.get('kind', 'page')}"
        artifact = {
            "url": step.get("url"),
            "kind": step.get("kind"),
            "source": step.get("source"),
            "reason": str(payload.get("reason", ""))[:200],
            "message": str(payload.get("message", ""))[:300],
            "html": str(payload.get("html", ""))[:20000],
        }
        (folder / f"{name}.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            profile_id = self.browser_capture.state(job_id)["profile_id"]
            self.chromium.screenshot(profile_id, folder / f"{name}.png")
        except Exception:
            log.warning("scout_debug_screenshot_failed")

    def _set_ai_key(self, params: dict) -> dict:
        self.ai_keys.save(str(params.get("key", "")))
        return {"configured": self.ai_keys.configured()}

    def _capture_latest(self, params: dict) -> dict | None:
        """Latest manual link queue; scout runs are shown per account."""
        with self.sessions() as session:
            latest = session.scalar(
                select(BrowserQueue.job_id)
                .where(BrowserQueue.job_id.not_in(select(ScoutRun.job_id)))
                .order_by(BrowserQueue.job_id.desc())
                .limit(1)
            )
        return self.browser_capture.state(latest) if latest else None

    def _scout_accounts(self, params: dict) -> list[dict]:
        profiles = self.browser_sessions.call("browser.list", {})
        rows = self.scout.accounts([profile["id"] for profile in profiles])
        return [{"profile": profile, **rows[profile["id"]]} for profile in profiles]

    def _scout_runs(self, params: dict) -> list[dict]:
        """Scout run history with metrics, newest first."""
        limit = min(int(params.get("limit", 20)), 100)
        with self.sessions() as session:
            rows = session.execute(
                select(ScoutRun, SearchJob)
                .join(SearchJob, SearchJob.id == ScoutRun.job_id)
                .order_by(ScoutRun.started_at.desc(), ScoutRun.job_id.desc())
                .limit(limit)
            )
            result = []
            for run, job in rows:
                stats = run.stats or {}
                status = "interrupted" if job.stage == "interrupted" else job.status
                result.append(
                    {
                        "id": run.job_id,
                        "status": status,
                        "started_at": serialize(job)["started_at"],
                        "finished_at": serialize(job)["completed_at"],
                        "sources_total": len(stats.get("sources") or []),
                        "sources_processed": len(stats.get("sources_done") or []),
                        **{key: int(stats.get(key) or 0) for key in scout_leads.RUN_COUNTERS},
                        "skips": stats.get("skips") or {},
                        "current_source": stats.get("current_source"),
                        "current_profile": stats.get("current_profile"),
                    }
                )
            return result

    def _scout_decisions(self, params: dict) -> list[dict]:
        """Scout decision log (kept 30 days) for debugging a run."""
        limit = min(int(params.get("limit", 200)), 500)
        with self.sessions() as session:
            query = select(ScoutDecision).order_by(ScoutDecision.id.desc()).limit(limit)
            if params.get("job_id"):
                query = query.where(ScoutDecision.job_id == int(params["job_id"]))
            return [serialize(row) for row in session.scalars(query)]

    def _scout_ignore(self, params: dict) -> dict:
        """Add a username to the Scout ignore list (settings system)."""
        username = normalized_username(str(params.get("username", "")))
        if not username or len(username) > 30:
            raise ValueError("Некорректное имя профиля.")
        ignored = list(self.settings()["scout_ignore_usernames"])
        if username not in {normalized_username(name) for name in ignored}:
            ignored.append(username)
            current = {key: value for key, value in self.settings().items() if key in DEFAULTS}
            self._save_settings({**current, "scout_ignore_usernames": ignored})
        return {"scout_ignore_usernames": ignored}

    def _system_info(self, params: dict) -> dict:
        return {
            "version": "0.1.1",
            "data_dir": str(self.data_dir),
            "log_dir": str(self.data_dir / "logs"),
            "transport": "stdio",
        }

    def _save_settings(self, params: dict) -> Any:
        settings = {**DEFAULTS, **params}
        if set(params) - set(DEFAULTS):
            raise ValueError("Неизвестные настройки; секреты в конфигурации запрещены.")
        SearchConfiguration(
            name="Defaults",
            **{
                key: settings[key]
                for key in (
                    "min_followers",
                    "max_followers",
                    "activity_days",
                    "minimum_score",
                    "target_leads",
                )
            },
        )
        ScoringWeights.model_validate(settings["weights"])
        PacingSettings.model_validate(settings)
        ScoutSettings.model_validate(settings)
        OutreachSettings.model_validate(settings)
        enabled = settings["enabled_providers"]
        if not isinstance(enabled, list) or set(enabled) - {"mock", "imported"}:
            raise ValueError("Источник недоступен.")
        with self.sessions.begin() as session:
            for key, value in settings.items():
                setting = session.get(Setting, key) or Setting(key=key)
                setting.value = value
                session.add(setting)
        return settings

    def _start_job(self, params: dict) -> Any:
        if any(state in {"running", "paused"} for state in self.manager.states.values()):
            raise ValueError("Сначала завершите активный поиск.")
        settings = self.settings()
        self.discovery.providers = [
            provider
            for provider in self.providers
            if provider.name in settings["enabled_providers"]
        ]
        if not self.discovery.providers:
            raise ValueError("Нет включённых источников. Проверьте настройки.")
        from .scoring import LeadScorer

        self.pipeline.scorer = LeadScorer(ScoringWeights.model_validate(settings["weights"]))
        return {"id": self.manager.start(SearchConfiguration.model_validate(params))}

    def _control_job(self, params: dict) -> Any:
        with self.sessions() as session:
            browser_job = session.get(BrowserQueue, int(params["id"])) is not None
        if browser_job:
            self.browser_capture.control(int(params["id"]), params["action"])
            self.scout.on_control(int(params["id"]), params["action"])
            return {"ok": True}
        self.manager.control(int(params["id"]), params["action"])
        return {"ok": True}

    def _list_jobs(self, params: dict) -> Any:
        with self.sessions() as session:
            return [
                serialize(job)
                for job in session.scalars(
                    select(SearchJob).order_by(SearchJob.created_at.desc()).limit(200)
                )
            ]

    def _job_detail(self, params: dict) -> Any:
        with self.sessions() as session:
            job = session.get(SearchJob, int(params["id"]))
            if not job:
                raise ValueError("Поиск не найден.")
            return serialize(job)

    def _list_leads(self, params: dict) -> Any:
        query = LeadQuery.model_validate(params)
        with self.sessions() as session:
            stmt = lead_statement(query)
            count = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
            leads = list(
                session.scalars(
                    stmt.offset((query.page - 1) * query.page_size).limit(query.page_size)
                )
            )
            ids = [lead.id for lead in leads]
            sources = list(session.scalars(select(LeadSource).where(LeadSource.lead_id.in_(ids))))
            scout = {
                row.lead_id: row
                for row in session.scalars(
                    select(LeadScoutProfile).where(LeadScoutProfile.lead_id.in_(ids))
                )
            }
            availability = {
                item.lead_id: item.extracted_signals.get("browser_capture", {}).get(
                    "unknown_fields", []
                )
                for item in session.scalars(
                    select(LeadAnalysis).where(LeadAnalysis.lead_id.in_(ids))
                )
            }
            return {
                "total": count,
                "items": [
                    {
                        **serialize(lead),
                        "unknown_fields": availability.get(lead.id, []),
                        "sources": [
                            serialize(source) for source in sources if source.lead_id == lead.id
                        ],
                        "scout_profile": scout_summary(scout.get(lead.id)),
                    }
                    for lead in leads
                ],
            }

    def _lead_detail(self, params: dict) -> Any:
        with self.sessions() as session:
            lead = session.get(Lead, int(params["id"]))
            if not lead:
                raise ValueError("Профиль не найден.")
            analysis = session.get(LeadAnalysis, lead.id)
            scout = session.get(ScoutAssessment, lead.id)
            profile = session.get(LeadScoutProfile, lead.id)
            return {
                **serialize(lead),
                "scout": self.scout.summary(scout.details) if scout else None,
                # Profile type decided by the classifier (local rules, optionally AI).
                "classification": {
                    "category": profile.profile_type,
                    "confidence": profile.profile_confidence,
                    "decided_by": profile.profile_decided_by,
                    "reasons": profile.profile_reasons[:6],
                    "ai_model": profile.ai_model,
                    "local_category": profile.local_category,
                    "local_confidence": profile.local_confidence,
                    "ai_category": profile.ai_category,
                    "ai_confidence": profile.ai_confidence,
                }
                if profile
                else None,
                "scout_profile": scout_summary(profile),
                "outreach": self.campaigns.lead_outreach(session, lead),
                # Every SMM source and method the lead was found through.
                "found_via": scout_leads.source_history(session, lead.id),
                "analysis": serialize(analysis) if analysis else None,
                "breakdown": [
                    serialize(item)
                    for item in session.scalars(
                        select(LeadScoreBreakdown).where(LeadScoreBreakdown.lead_id == lead.id)
                    )
                ],
                "sources": [
                    serialize(item)
                    for item in session.scalars(
                        select(LeadSource).where(LeadSource.lead_id == lead.id)
                    )
                ],
            }

    def _set_lead_status(self, params: dict) -> Any:
        status = params["status"]
        if status not in {"new", "reviewed", "qualified", "rejected", "contacted"}:
            raise ValueError("Неизвестный статус.")
        with self.sessions.begin() as session:
            lead = session.get(Lead, int(params["id"]))
            if not lead:
                raise ValueError("Профиль не найден.")
            lead.status = status
        return {"ok": True}

    def _import_dataset(self, params: dict) -> Any:
        if any(state in {"running", "paused"} for state in self.manager.states.values()):
            raise ValueError("Нельзя заменить источник во время поиска.")
        provider = ImportedDatasetProvider(Path(params["path"]))
        provider.profiles = [normalize(profile) for profile in provider.profiles]
        temporary = self.import_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                [profile.model_dump(mode="json") for profile in provider.profiles],
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.import_path)
        self.providers = [p for p in self.providers if p.name != "imported"] + [provider]
        return {"count": len(provider.profiles)}

    def _provider_health(self, params: dict) -> Any:
        with self.sessions() as session:
            health = {
                item.provider: serialize(item) for item in session.scalars(select(ProviderHealth))
            }
        available = {provider.name for provider in self.providers}
        return [
            health.get(
                name,
                {
                    "provider": name,
                    "status": "Healthy" if name in available else "Unavailable",
                    "last_error": None,
                },
            )
            for name in ["mock", "imported", "meta_instagram"]
        ]

    def dashboard(self) -> dict:
        with self.sessions() as session:
            total = session.scalar(select(func.count()).select_from(Lead))
            qualified = session.scalar(
                select(func.count()).select_from(Lead).where(Lead.status == "qualified")
            )
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            new = session.scalar(
                select(func.count()).select_from(Lead).where(Lead.created_at >= today)
            )
            active = session.scalar(
                select(func.count())
                .select_from(SearchJob)
                .where(
                    SearchJob.status.in_(["running", "queued", "paused"]),
                    SearchJob.stage != "interrupted",
                )
            )
            genres = session.execute(
                select(Lead.primary_genre, func.count())
                .where(Lead.primary_genre.is_not(None))
                .group_by(Lead.primary_genre)
                .order_by(func.count().desc())
                .limit(8)
            ).all()
            distribution = [
                session.scalar(
                    select(func.count()).select_from(Lead).where(Lead.lead_score.between(low, high))
                )
                for low, high in [(0, 39), (40, 69), (70, 89), (90, 100)]
            ]
        return {
            "total": total,
            "qualified": qualified,
            "today": new,
            "active": active,
            "genres": [{"name": name, "count": count} for name, count in genres],
            "distribution": distribution,
            "jobs": self.call("jobs.list", {})[:5],
            "leads": self.call("leads.list", {"page_size": 5})["items"],
        }

    def export(self, params: dict) -> dict:
        path = Path(params["path"])
        if path.suffix.casefold() != ".csv":
            raise ValueError("Выберите CSV-файл.")
        query = LeadQuery.model_validate(params.get("query", {}))
        ids = params.get("ids")
        if ids is not None and (
            not isinstance(ids, list)
            or len(ids) > 100000
            or any(not isinstance(value, int) or value <= 0 for value in ids)
        ):
            raise ValueError("Некорректный список выбранных профилей.")
        columns = [
            "username",
            "profile_url",
            "display_name",
            "followers",
            "primary_genre",
            "genres",
            "lead_score",
            "bio",
            "external_url",
            "source",
            "status",
            "last_activity_at",
            "created_at",
        ]
        count = 0
        # Temporary sibling file prevents partial CSV from replacing the user's file on failure.
        temporary = path.with_name(path.name + ".artist-lead-finder.tmp")
        try:
            with (
                temporary.open("w", encoding="utf-8-sig", newline="") as file,
                self.sessions() as session,
            ):
                writer = csv.DictWriter(file, fieldnames=columns)
                writer.writeheader()
                stmt = lead_statement(query)
                if ids is not None:
                    stmt = stmt.where(Lead.id.in_(ids))
                # Related rows are loaded per chunk: per-lead queries made large exports
                # exceed the desktop RPC timeout.
                for chunk in session.scalars(stmt.execution_options(yield_per=500)).partitions():
                    chunk_ids = [lead.id for lead in chunk]
                    missing_by_lead = {
                        lead_id: signals.get("browser_capture", {}).get("unknown_fields", [])
                        for lead_id, signals in session.execute(
                            select(LeadAnalysis.lead_id, LeadAnalysis.extracted_signals).where(
                                LeadAnalysis.lead_id.in_(chunk_ids)
                            )
                        )
                    }
                    sources_by_lead: dict[int, list[str]] = {}
                    for source in session.scalars(
                        select(LeadSource)
                        .where(LeadSource.lead_id.in_(chunk_ids))
                        .order_by(LeadSource.id)
                    ):
                        sources_by_lead.setdefault(source.lead_id, []).append(
                            f"{source.source_provider}:{source.source_type}:{source.source_value}"
                        )
                    for lead in chunk:
                        row = serialize(lead)
                        for field in missing_by_lead.get(lead.id, []):
                            row[field] = None
                        row["genres"] = "; ".join(lead.genres)
                        row["source"] = "; ".join(sources_by_lead.get(lead.id, []))
                        cleaned = {}
                        for key in columns:
                            raw = row.get(key)
                            value = "" if raw is None else str(raw)
                            if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                                value = "'" + value
                            cleaned[key] = value
                        writer.writerow(cleaned)
                        count += 1
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {"count": count, "path": str(path)}

    def shutdown(self) -> None:
        self.chromium.shutdown()
        self.manager.shutdown()
