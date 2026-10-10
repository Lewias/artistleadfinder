"""Cloud parser jobs: queued → collecting → completed (or failed, cancelled).

One job runs at a time. The process holds a lease on it and renews it while the parser
works; a job whose lease ran out (the process died, the server restarted) is taken again
and continues with a new scout run for the leads still missing. Leads go to the CRM as
they are found, each at most once per job, so a restart loses nothing and adds nothing
twice.
"""

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from . import crm

log = logging.getLogger(__name__)

LEASE = timedelta(minutes=3)
RENEW_SECONDS = 30
CHECK_SECONDS = 5
MAX_ATTEMPTS = 3
# Transient page failures (slow page, lost window) resumed in a row before giving up.
MAX_RESUMES = 8
FATAL_MARKERS = ("требует вход", "подтвердить аккаунт", "Требуется вход")
RATE_MARKER = "ограничил запросы"
# A job with no sign of life this long: a browser or core call hung. The process exits,
# Docker starts it again and the job goes on after its lease (see `watch`).
STUCK_SECONDS = 300


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobFailed(Exception):
    """The job cannot go on; the text is shown to the user."""


class Runner:
    # The jobs this process takes; the outreach has a process of its own (outreach.py).
    kind = "scout"

    def __init__(
        self,
        connect: Callable,
        engine_for: Callable[[str], object],
        driver_for: Callable[[object], object],
        worker_id: str = "cloud-parser",
        now: Callable[[], datetime] = utcnow,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.connect = connect
        self.engine_for = engine_for
        self.driver_for = driver_for
        self.worker_id = worker_id
        self.now = now
        self.clock = clock
        self.conn = None
        # What the watchdog looks at: the job in work, its engine and its last sign of life.
        self.active: str | None = None
        self.engine = None
        self.alive = clock()

    # ---------- database ----------

    def db(self):
        if self.conn is None or self.conn.closed:
            self.conn = self.connect()
        return self.conn

    def update(self, job_id: str, **fields) -> None:
        names = ", ".join(f"{name} = %({name})s" for name in fields)
        values = {
            name: Jsonb(value) if isinstance(value, (dict, list)) else value
            for name, value in fields.items()
        }
        with self.db().transaction(), self.db().cursor() as cur:
            cur.execute(
                f"update public.cloud_jobs set {names} where id = %(job_id)s",
                {**values, "job_id": job_id},
            )

    def load(self, job_id: str) -> dict:
        with self.db().cursor() as cur:
            cur.execute("select * from public.cloud_jobs where id = %s", (job_id,))
            return cur.fetchone()

    def claim(self) -> dict | None:
        """The oldest open job of this process's kind with no live lease, now leased to
        it. An Instagram account works in one place at a time: a job waits while another
        job (the parser or the outreach) uses the same account."""
        with self.db().transaction(), self.db().cursor() as cur:
            cur.execute(
                """
                update public.cloud_jobs
                   set locked_by = %(me)s, locked_until = now() + %(lease)s,
                       attempts = attempts + case when stage = 'collecting' then 1 else 0 end
                 where id = (
                   select j.id from public.cloud_jobs j
                    where j.stage in ('queued', 'collecting') and j.kind = %(kind)s
                      and (j.locked_until is null or j.locked_until < now())
                      and not exists (
                        select 1 from public.cloud_jobs o
                         where o.owner_id = j.owner_id and o.profile_id = j.profile_id
                           and o.id <> j.id and o.stage = 'collecting'
                           and o.locked_until > now())
                    order by j.created_at
                    for update skip locked
                    limit 1)
                returning *
                """,
                {"me": self.worker_id, "lease": LEASE, "kind": self.kind},
            )
            return cur.fetchone()

    def renew(self, job_id: str) -> dict:
        """Keeps the lease and returns the job (to see a cancel)."""
        with self.db().transaction(), self.db().cursor() as cur:
            cur.execute(
                """
                update public.cloud_jobs set locked_until = now() + %s
                 where id = %s and locked_by = %s
                returning *
                """,
                (LEASE, job_id, self.worker_id),
            )
            return cur.fetchone()

    def session_record(self, job: dict) -> dict:
        with self.db().cursor() as cur:
            cur.execute(
                "select name, record from public.cloud_sessions "
                "where owner_id = %s and profile_id = %s",
                (job["owner_id"], job["profile_id"]),
            )
            row = cur.fetchone()
        if row is None:
            raise JobFailed("Сессия аккаунта Instagram не найдена на сервере. Отправьте её снова.")
        return row

    def save_session(self, job: dict, record: dict) -> None:
        """Instagram renews cookies while the parser works; the next job takes the new ones."""
        with self.db().transaction(), self.db().cursor() as cur:
            cur.execute(
                """
                update public.cloud_sessions set record = %s, updated_at = now()
                 where owner_id = %s and profile_id = %s
                """,
                (Jsonb(record), job["owner_id"], job["profile_id"]),
            )

    # ---------- loop ----------

    def tick(self) -> bool:
        job = self.claim()
        if job is None:
            return False
        self.run(job)
        return True

    def stuck(self) -> bool:
        """The job in work gave no sign of life for STUCK_SECONDS; logs what hung."""
        if self.active is None or self.clock() - self.alive < STUCK_SECONDS:
            return False
        calling = getattr(self.engine, "calling", None)
        log.error(
            "cloud_parser_stuck",
            extra={"job": self.active, "error_type": calling or "unknown"},
        )
        return True

    def watch(self, exit: Callable[[int], None] = os._exit, every: float = 30.0) -> None:
        """Watchdog thread: a hung call never returns, so only a fresh process helps."""
        while True:
            time.sleep(every)
            if self.stuck():
                exit(70)

    def run_forever(self, idle: float = 5.0) -> None:
        log.info("cloud_parser_started" if self.kind == "scout" else f"cloud_{self.kind}_started")
        threading.Thread(target=self.watch, name="watchdog", daemon=True).start()
        while True:
            try:
                if not self.tick():
                    time.sleep(idle)
            except Exception as error:  # a lost database connection: start over
                log.warning("cloud_parser_loop_error", extra={"error_type": type(error).__name__})
                self.conn = None
                time.sleep(10)

    def run(self, job: dict) -> None:
        job_id = str(job["id"])
        if job["cancel_requested"]:
            self.finish(job_id, "cancelled", None)
            return
        if job["attempts"] > MAX_ATTEMPTS:
            self.finish(job_id, "failed", "Задача прерывалась слишком много раз.")
            return
        engine = None
        self.active, self.alive = job_id, self.clock()
        try:
            row = self.session_record(job)
            engine = self.engine = self.engine_for(str(job["owner_id"]))
            self.collect(job, engine, row)
        except JobFailed as error:
            self.finish(job_id, "failed", str(error))
        except Exception as error:
            log.exception("cloud_job_error", extra={"job": job_id})
            self.finish(job_id, "failed", f"Ошибка парсера на сервере ({type(error).__name__}).")
        finally:
            self.active, self.engine = None, None
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    log.warning("cloud_engine_close_failed", extra={"job": job_id})

    def collect(self, job: dict, engine, row: dict) -> None:
        job_id = str(job["id"])
        params = job["params"]
        profile = job["profile_id"]
        engine.cancel_open_runs()
        engine.put_session(profile, row["name"], row["record"])
        engine.apply_settings(params.get("settings") or {})
        counters = dict(job.get("counters") or {})
        missing = int(params["target"]) - int(counters.get("passed") or 0)
        if missing <= 0:
            self.finish(job_id, "completed", None)
            return
        self.update(
            job_id,
            stage="collecting",
            error=None,
            started_at=job.get("started_at") or self.now(),
            progress={"status": "starting", "found": 0, "target": missing},
        )
        try:
            run_id = engine.start(profile, list(params["sources"]), missing)
        except Exception as error:
            raise JobFailed(f"Парсер не запустился: {error}") from None

        state_box = {"checked": 0.0, "resumes": 0, "result": None, "fatal": None}

        def tick(state: dict) -> str | None:
            now = self.alive = self.clock()
            if now - state_box["checked"] >= CHECK_SECONDS:
                state_box["checked"] = now
                for lead in engine.new_leads(run_id):
                    self.save_lead(job, lead)
                current = self.renew(job_id)
                if current is None:
                    # The lease went to another process: this one stops quietly.
                    state_box["result"] = "lost"
                    return "stop"
                if current["cancel_requested"]:
                    state_box["result"] = "cancelled"
                    return "stop"
                self.update(job_id, progress=progress(state, missing))
            if state.get("status") == "paused":
                return self.on_paused(engine, run_id, state, state_box)
            if state.get("status") == "running":
                state_box["resumes"] = 0
            return None

        final = self.driver_for(engine).run(run_id, tick)
        # What the run found last, then the session Instagram renewed.
        for lead in engine.new_leads(run_id):
            self.save_lead(job, lead)
        try:
            engine.call("browser.runtime.save", {"id": profile})
            self.save_session(job, engine.session(profile))
        except Exception:
            log.warning("cloud_session_save_failed", extra={"job": job_id})
        result = state_box["result"]
        if result in ("cancelled", "fatal", "lost"):
            try:
                engine.call("jobs.control", {"id": run_id, "action": "cancel"})
            except Exception:
                pass
        if result == "lost":
            return
        if result == "cancelled":
            self.finish(job_id, "cancelled", None)
            return
        if result == "fatal":
            raise JobFailed(state_box["fatal"])
        status = (final or {}).get("status")
        if status == "failed":
            raise JobFailed((final or {}).get("error") or "Парсер остановился с ошибкой.")
        self.finish(job_id, "completed", None)

    def on_paused(self, engine, run_id: int, state: dict, box: dict) -> str | None:
        """The core paused the queue. Login and checkpoint need the user; a rate limit and
        a slow page are waited out and the queue goes on, as the user would do."""
        error = str(state.get("error") or "")
        if any(marker in error for marker in FATAL_MARKERS):
            box["fatal"] = (
                "Instagram просит войти или подтвердить аккаунт. Откройте этот аккаунт в "
                "приложении, войдите и запустите задачу снова — сессия отправится заново."
            )
            box["result"] = "fatal"
            return "stop"
        box["resumes"] += 1
        if box["resumes"] > MAX_RESUMES and RATE_MARKER not in error:
            box["fatal"] = f"Парсер не смог продолжить: {error or 'страница не загрузилась'}"
            box["result"] = "fatal"
            return "stop"
        profile = state.get("profile_id") or ""
        opened = engine.call("browser.runtime.is_open", {"id": profile}) or {}
        if opened.get("open") is not True:
            engine.call("browser.runtime.open", {"id": profile})
        # A rate limit set the pacer's break: the resumed queue waits it out by itself.
        engine.call("jobs.control", {"id": run_id, "action": "resume"})
        return "skip"

    # ---------- results ----------

    def save_lead(self, job: dict, lead: dict) -> None:
        """One lead into the owner's CRM, at most once per job."""
        job_id = str(job["id"])
        wanted = set(job["params"].get("categories") or [])
        with self.db().transaction(), self.db().cursor() as cur:
            cur.execute(
                """
                insert into public.cloud_candidates
                  (job_id, owner_id, username, instagram_id, via, origins, profile,
                   category, confidence, reason)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (job_id, username) do nothing
                returning id
                """,
                (
                    job_id,
                    job["owner_id"],
                    lead["username"],
                    lead.get("instagram_id"),
                    lead.get("via") or [],
                    Jsonb(lead.get("origins") or []),
                    Jsonb(lead.get("profile") or {}),
                    lead.get("category"),
                    lead.get("confidence"),
                    lead.get("reason"),
                ),
            )
            inserted = cur.fetchone()
            if inserted is None:
                return
            if lead.get("category") not in wanted:
                outcome, contact = "filtered", None
            else:
                outcome, contact = crm.save(cur, str(job["owner_id"]), job_id, lead, self.now())
            cur.execute(
                "update public.cloud_candidates set outcome = %s, contact_id = %s where id = %s",
                (outcome, contact, inserted["id"]),
            )
        self.update_counters(job_id)

    def update_counters(self, job_id: str) -> dict:
        with self.db().cursor() as cur:
            cur.execute(
                """
                select count(*) as found,
                       count(*) filter (where outcome in ('added', 'updated')) as passed,
                       count(*) filter (where outcome = 'added') as added,
                       count(*) filter (where outcome = 'updated') as updated,
                       count(*) filter (where outcome = 'filtered') as filtered,
                       count(*) filter (where outcome = 'error') as errors
                  from public.cloud_candidates where job_id = %s
                """,
                (job_id,),
            )
            counts = dict(cur.fetchone())
        self.update(job_id, counters=counts)
        return counts

    def finish(self, job_id: str, stage: str, error: str | None) -> None:
        self.update_counters(job_id)
        job = self.load(job_id)
        progress_now = dict(job.get("progress") or {})
        progress_now["status"] = stage
        self.update(
            job_id,
            stage=stage,
            error=error,
            progress=progress_now,
            finished_at=self.now(),
            locked_by=None,
            locked_until=None,
        )
        log.info("cloud_job_finished", extra={"job": job_id, "stage": stage})


def progress(state: dict, target: int) -> dict:
    """What the app shows while the parser works; only what the core reports."""
    stats = state.get("stats") or {}
    return {
        "status": state.get("status"),
        "found": int(state.get("found") or 0),
        "target": target,
        "candidates": int(state.get("candidates") or 0),
        "step": state.get("kind"),
        "url": state.get("url"),
        "waiting": float(state.get("wait_seconds") or 0),
        "wait_reason": state.get("wait_reason"),
        "error": state.get("error"),
        "sources_done": len(stats.get("sources_done") or []),
        "notices": list(state.get("notices") or [])[-5:],
    }
