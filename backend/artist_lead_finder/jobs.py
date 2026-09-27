"""Bounded workers; durable job lifecycle and cooperative pause/cancellation."""

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Condition
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .discovery import DiscoveryEngine, DiscoveryRecord
from .models import ProviderHealth, SearchJob, utcnow
from .pipeline import ProcessResult
from .schemas import SearchConfiguration

log = logging.getLogger(__name__)
Processor = Callable[[int, DiscoveryRecord, SearchConfiguration], tuple[bool, bool] | ProcessResult]


class DiscoveryManager:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        engine: DiscoveryEngine,
        processor: Processor | None = None,
    ) -> None:
        self.sessions = sessions
        self.engine = engine
        self.processor = processor or (lambda _job, _record, _config: (False, False))
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discovery")
        self.condition = Condition()
        self.states: dict[int, str] = {}
        self.closing = False
        self.futures = {}
        with sessions.begin() as session:
            for job in session.scalars(
                select(SearchJob).where(
                    SearchJob.status.in_(["running", "queued", "paused"]),
                    SearchJob.stage != "interrupted",
                )
            ):
                job.status = "paused"
                job.stage = "interrupted"
                job.errors = [*job.errors, {"message": "Поиск прерван. Запустите новый поиск."}]

    def start(self, config: SearchConfiguration) -> int:
        if not (config.seed_accounts or config.keywords or config.hashtags):
            raise ValueError("Укажите хотя бы один источник поиска.")
        with self.condition:
            if self.closing:
                raise ValueError("Приложение завершает работу.")
            for finished_id in [key for key, future in self.futures.items() if future.done()]:
                self.futures.pop(finished_id)
                self.states.pop(finished_id, None)
            with self.sessions.begin() as session:
                job = SearchJob(**config.model_dump())
                session.add(job)
                session.flush()
                job_id = job.id
            self.states[job_id] = "running"
            self.futures[job_id] = self.pool.submit(self._run, job_id, config)
        log.info("search_created", extra={"job_id": job_id})
        return job_id

    def control(self, job_id: int, action: str) -> None:
        if action not in {"pause", "resume", "cancel"}:
            raise ValueError("Неизвестное действие.")
        with self.condition:
            state = self.states.get(job_id)
            if state not in {"running", "paused"}:
                raise ValueError("Поиск завершён либо прерван при предыдущем запуске.")
            self.states[job_id] = {"pause": "paused", "resume": "running", "cancel": "cancelled"}[
                action
            ]
            with self.sessions.begin() as session:
                job = session.get(SearchJob, job_id)
                job.status = self.states[job_id]
            self.condition.notify_all()

    def _checkpoint(self, job_id: int) -> bool:
        with self.condition:
            while self.states[job_id] == "paused" and not self.closing:
                self.condition.wait(timeout=0.25)
            return not self.closing and self.states[job_id] == "running"

    def _health(self, job_id: int, provider: str, status: str, error: str | None) -> None:
        with self.sessions.begin() as session:
            health = session.get(ProviderHealth, provider)
            if health is None:
                health = ProviderHealth(provider=provider)
                session.add(health)
            health.last_request_at = utcnow()
            if status != "request":
                health.status = status
                health.last_error = error
            if status == "Healthy":
                health.last_success_at = utcnow()
            if error:
                job = session.get(SearchJob, job_id)
                job.errors = [*job.errors, {"provider": provider, "message": error}]
                log.warning("provider_failure", extra={"job_id": job_id, "provider": provider})

    def _run(self, job_id: int, config: SearchConfiguration) -> None:
        failed = False
        try:
            with self.sessions.begin() as session:
                job = session.get(SearchJob, job_id)
                job.status = self.states[job_id]
                job.started_at = utcnow()
                job.stage = "discovering"
            for record in self.engine.discover(
                config,
                lambda: self._checkpoint(job_id),
                lambda provider, status, error: self._health(job_id, provider, status, error),
            ):
                try:
                    result = self.processor(job_id, record, config)
                except ValueError as error:
                    log.warning(
                        "candidate_analysis_error",
                        extra={"job_id": job_id, "error_type": type(error).__name__},
                    )
                    with self.sessions.begin() as session:
                        job = session.get(SearchJob, job_id)
                        job.candidates_found += 1
                        if len(job.errors) < 20:
                            job.errors = [
                                *job.errors,
                                {
                                    "message": (
                                        "Кандидат пропущен: некорректные данные "
                                        "или конфликт идентификатора."
                                    )
                                },
                            ]
                    continue
                if isinstance(result, ProcessResult):
                    analyzed, artist, qualified = result.analyzed, result.artist, result.qualified
                else:
                    analyzed, (artist, qualified) = True, result
                with self.sessions.begin() as session:
                    job = session.get(SearchJob, job_id)
                    job.candidates_found += 1
                    job.profiles_analyzed += int(analyzed)
                    job.artists_detected += int(artist)
                    job.qualified_leads += int(qualified)
                    job.stage = "analyzing"
                    # Scan complete sources to retain provenance even after target is reached.
        except Exception:
            failed = True
            log.exception("search_failed", extra={"job_id": job_id})
            with self.sessions.begin() as session:
                job = session.get(SearchJob, job_id)
                job.errors = [*job.errors, {"message": "Не удалось выполнить поиск. См. журнал."}]
        finally:
            with self.condition:
                cancelled = self.closing or self.states.get(job_id) == "cancelled"
                with self.sessions.begin() as session:
                    job = session.get(SearchJob, job_id)
                    no_results_error = bool(job.errors) and job.candidates_found == 0
                    job.status = (
                        "cancelled"
                        if cancelled
                        else "failed"
                        if failed or no_results_error
                        else "completed"
                    )
                    job.stage = job.status
                    job.completed_at = utcnow()
                    self.states[job_id] = job.status
                self.condition.notify_all()
            log.info(
                "search_completed",
                extra={
                    "job_id": job_id,
                    "candidates": job.candidates_found,
                    "analyzed": job.profiles_analyzed,
                    "qualified": job.qualified_leads,
                },
            )

    def shutdown(self) -> None:
        with self.condition:
            self.closing = True
            self.condition.notify_all()
        self.pool.shutdown(wait=True, cancel_futures=False)
