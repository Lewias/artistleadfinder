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
from .models import (
    BrowserQueue,
    Lead,
    LeadAnalysis,
    LeadScoreBreakdown,
    LeadSource,
    ProviderHealth,
    ScoutAssessment,
    SearchJob,
    Setting,
)
from .normalization import normalize
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


DEFAULTS = {
    "min_followers": 1000,
    "max_followers": 50000,
    "activity_days": 30,
    "minimum_score": 70,
    "target_leads": 500,
    "enabled_providers": ["mock"],
    "weights": ScoringWeights().model_dump(),
    **PacingSettings().model_dump(),
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
        self.scout = ScoutService(sessions, self.browser_capture, self.settings)
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
            "browser.runtime.eval": lambda p: chromium.evaluate(p["id"], p["script"]),
            "scout.sources": lambda p: self.scout.sources(p.get("sources")),
            "scout.start_internal": lambda p: self.scout.start(p, self.settings()),
            "scout.commit_internal": lambda p: self.scout.commit(int(p["id"]), p["snapshot"]),
            "scout.results": lambda p: self.scout.results(),
            "scout.skip": lambda p: self.scout.skip(int(p["id"])),
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

    def _capture_error(self, params: dict) -> dict:
        if params["reason"] == "rate_limited":
            self.scout.pacer.rate_limited(self.scout.pacing())
        self.browser_capture.stop_with_error(int(params["id"]), params["reason"])
        return {"ok": True}

    def _capture_latest(self, params: dict) -> dict | None:
        latest = self.browser_capture.latest()
        return self.scout.state(latest["id"]) if latest else None

    def _system_info(self, params: dict) -> dict:
        return {
            "version": "0.1.0",
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
            return {
                **serialize(lead),
                "scout": self.scout.summary(scout.details) if scout else None,
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
