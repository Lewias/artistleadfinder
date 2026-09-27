"""Application API shared by local RPC and a future cloud transport."""

import csv
import json
import logging
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
        self.scout = ScoutService(sessions, self.browser_capture)

    def settings(self) -> dict:
        with self.sessions() as session:
            stored = {setting.key: setting.value for setting in session.scalars(select(Setting))}
        return {**DEFAULTS, **stored}

    def call(self, method: str, params: dict) -> Any:
        if method.startswith("browser.runtime."):
            action = method.removeprefix("browser.runtime.")
            if action == "self_test":
                return self.chromium.self_test()
            if action == "is_open":
                return {"open": self.chromium.is_open(params["id"])}
            if action == "open":
                return self.chromium.open(params["id"], params.get("proxy_override"))
            if action == "save":
                return self.chromium.save(params["id"])
            if action == "navigate":
                return self.chromium.navigate(params["id"], params["url"])
            if action == "eval":
                return self.chromium.evaluate(params["id"], params["script"])
            if action == "search_open":
                return self.chromium.search_open(params["url"])
            raise ValueError("Unknown browser runtime method")
        if method == "scout.sources":
            return self.scout.sources(params.get("sources"))
        if method == "scout.start_internal":
            return self.scout.start(params, self.settings())
        if method == "scout.commit_internal":
            return self.scout.commit(int(params["id"]), params["snapshot"])
        if method == "scout.results":
            return self.scout.results()
        if method == "scout.skip":
            return self.scout.skip(int(params["id"]))
        if method == "capture.start_internal":
            return self.browser_capture.start(params, self.settings())
        if method == "capture.commit_internal":
            return self.browser_capture.capture(int(params["id"]), params["snapshot"])
        if method == "capture.error_internal":
            self.browser_capture.stop_with_error(int(params["id"]), params["reason"])
            return {"ok": True}
        if method == "capture.state":
            return self.scout.state(int(params["id"]))
        if method == "capture.latest":
            latest = self.browser_capture.latest()
            return self.scout.state(latest["id"]) if latest else None
        if method.startswith("browser."):
            return self.browser_sessions.call(method, params)
        if method == "system.info":
            return {
                "version": "0.1.0",
                "data_dir": str(self.data_dir),
                "log_dir": str(self.data_dir / "logs"),
                "transport": "stdio",
            }
        if method == "settings.get":
            return self.settings()
        if method == "settings.save":
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
            enabled = settings["enabled_providers"]
            if not isinstance(enabled, list) or set(enabled) - {"mock", "imported"}:
                raise ValueError("Источник недоступен.")
            with self.sessions.begin() as session:
                for key, value in settings.items():
                    setting = session.get(Setting, key) or Setting(key=key)
                    setting.value = value
                    session.add(setting)
            return settings
        if method == "jobs.start":
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
        if method == "jobs.control":
            with self.sessions() as session:
                browser_job = session.get(BrowserQueue, int(params["id"])) is not None
            if browser_job:
                self.browser_capture.control(int(params["id"]), params["action"])
                return {"ok": True}
            self.manager.control(int(params["id"]), params["action"])
            return {"ok": True}
        if method == "jobs.list":
            with self.sessions() as session:
                return [
                    serialize(job)
                    for job in session.scalars(
                        select(SearchJob).order_by(SearchJob.created_at.desc()).limit(200)
                    )
                ]
        if method == "jobs.detail":
            with self.sessions() as session:
                job = session.get(SearchJob, int(params["id"]))
                if not job:
                    raise ValueError("Поиск не найден.")
                return serialize(job)
        if method == "leads.list":
            query = LeadQuery.model_validate(params)
            with self.sessions() as session:
                stmt = lead_statement(query)
                count = session.scalar(
                    select(func.count()).select_from(stmt.order_by(None).subquery())
                )
                leads = list(
                    session.scalars(
                        stmt.offset((query.page - 1) * query.page_size).limit(query.page_size)
                    )
                )
                ids = [lead.id for lead in leads]
                sources = list(
                    session.scalars(select(LeadSource).where(LeadSource.lead_id.in_(ids)))
                )
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
        if method == "leads.detail":
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
        if method == "leads.status":
            status = params["status"]
            if status not in {"new", "reviewed", "qualified", "rejected", "contacted"}:
                raise ValueError("Неизвестный статус.")
            with self.sessions.begin() as session:
                lead = session.get(Lead, int(params["id"]))
                if not lead:
                    raise ValueError("Профиль не найден.")
                lead.status = status
            return {"ok": True}
        if method == "providers.import":
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
        if method == "providers.health":
            with self.sessions() as session:
                health = {
                    item.provider: serialize(item)
                    for item in session.scalars(select(ProviderHealth))
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
        if method == "dashboard.get":
            return self.dashboard()
        if method == "leads.export":
            return self.export(params)
        raise ValueError("Неизвестный метод приложения.")

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
                for lead in session.scalars(stmt.execution_options(yield_per=250)):
                    row = serialize(lead)
                    analysis = session.get(LeadAnalysis, lead.id)
                    if analysis:
                        missing = analysis.extracted_signals.get("browser_capture", {}).get(
                            "unknown_fields", []
                        )
                        for field in missing:
                            row[field] = None
                    row["genres"] = "; ".join(lead.genres)
                    row["source"] = "; ".join(
                        f"{s.source_provider}:{s.source_type}:{s.source_value}"
                        for s in session.scalars(
                            select(LeadSource).where(LeadSource.lead_id == lead.id)
                        )
                    )
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
