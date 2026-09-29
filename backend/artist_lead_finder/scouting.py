"""Lead Scout runs: SMM sources -> candidates -> profiles -> classification -> leads.

Stages live in the lead_scout package; this service drives them page by page for the
browser queue and keeps evidence-based service recommendations (assess).
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from .browser_capture import parse_snapshot, profile_url
from .chromium_runtime import GUEST_ID
from .lead_scout import events, leads, memory
from .lead_scout.candidates import CandidateGate, profile_link
from .lead_scout.classification import (
    ClassificationService,
    ClassificationSettings,
    SqlAIClassificationCache,
    SqlPendingAIJobs,
)
from .lead_scout.decision import (
    ALLOWED_TYPES,
    ALREADY_PROCESSED,
    CLASSIFICATION_FAILED,
    DUPLICATE_LEAD,
    OTHER,
    PROFILE_NOT_FOUND,
    PROFILE_PARSE_FAILED,
    PROFILE_PRIVATE,
    PROFILE_UNAVAILABLE,
    RATE_LIMITED,
    WRONG_PROFILE_TYPE,
    CandidateRef,
    FilterInput,
    LeadFilterDecision,
    decide,
)
from .lead_scout.discovery import (
    GROUP_OF_METHOD,
    METRIC_KEYS,
    DiscoveryContext,
    initial_tasks,
    post_url,
    provider_for,
    source_name,
)
from .lead_scout.errors import InstagramUnavailableError, error_for
from .lead_scout.memory import aware
from .lead_scout.profiles import (
    AbortSignal,
    InstagramProfileResolver,
    NormalizedInstagramProfile,
    ProfileResolveContext,
    ProfileResolveFailure,
    ResolveCancelled,
    ResolveStep,
    SqlProfileCache,
)
from .lead_scout.profiles import model as resolve_reasons
from .lead_scout.settings import ScoutSettings
from .models import (
    BrowserQueue,
    Lead,
    LeadScoutProfile,
    LeadSource,
    ScoutAccount,
    ScoutAssessment,
    ScoutPost,
    ScoutRun,
    ScoutSource,
    SearchJob,
    utcnow,
)
from .pacing import Pacer, PacingSettings
from .providers import Candidate

log = logging.getLogger(__name__)

__all__ = ["ScoutService", "assess", "post_url", "take_batch"]

ARTIST = (
    r"\b(?:rapper|singer|musician|songwriter|recording artist"
    r"|independent artist|hip.hop artist|band)\b"
    r"|рэпер|репер|пев[еи]|музыкант|исполнитель|автор песен|музыкальная группа"
)
BUSINESS = (
    r"\b(?:magazine|record label|fan ?page|news|photographer|store|music media)\b"
    r"|паблик|журнал|лейбл|фан.?аккаунт|фотограф|магазин|музыкальн\w* сми"
)
MENTION = r"(?<![\w.])@([a-zA-Z0-9_.]{1,30})"


def assess(candidate, observations, now=None):
    now = now or utcnow()
    text = candidate.bio
    artist = (
        not candidate.is_private
        and bool(re.search(ARTIST, text, re.I))
        and not re.search(BUSINESS, text, re.I)
    )
    services = {key: {"score": 0, "reasons": []} for key in ("beats", "mixing", "promotion")}

    def signal(key, score, reason, evidence=None):
        services[key]["score"] = max(services[key]["score"], score)
        services[key]["reasons"].append({"text": reason, "score": score, "evidence": evidence})

    if artist:
        if re.search(r"\brap(?:per)?\b|hip.hop|r&b|рэп|репер", text, re.I):
            signal("beats", 25, "Профиль рэп/R&B исполнителя; потребность в битах не подтверждена.")
        for obs in observations:
            caption = obs["caption"]
            # Keep evidence tied to this artist, not another act mentioned in the post.
            sentences = re.split(r"[!?\n]|\.(?=\s)", caption)
            scoped = [
                s
                for s in sentences
                if candidate.username.lower()
                in [m.lower().rstrip(".") for m in re.findall(MENTION, s)]
                and len(set(m.lower().rstrip(".") for m in re.findall(MENTION, s))) == 1
            ]
            if obs.get("kind") == "comment":
                scoped = (
                    sentences if obs.get("author", "").lower() == candidate.username.lower() else []
                )
            try:
                date = datetime.fromisoformat(obs["published_at"].replace("Z", "+00:00"))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                age = (now - date).total_seconds() / 86400
                fresh = 0 <= age <= 90
            except (ValueError, TypeError, AttributeError):
                fresh = False
            for sentence in scoped:
                evidence = {**obs, "caption": sentence.strip()[:1500]}
                cap = 90 if fresh else 40
                if re.search(
                    r"\b(?:not|no longer|doesn't|don't)\b|не ищ|не нуж|больше не", sentence, re.I
                ):
                    continue
                for key, product in {
                    "beats": r"beats?|producer|биты?|битов|продюсер",
                    "mixing": r"mix(?:ing)?|master(?:ing)?|engineer|сведени|мастеринг|звукорежисс",
                    "promotion": r"promotion|publicist|pr team|продвижени|пиар",
                }.items():
                    if re.search(
                        r"(?:looking for|needs?|seeking|ищет|ищу|нужен|нужны|нужно|нуждается)"
                        r".{0,45}(?:" + product + ")",
                        sentence,
                        re.I,
                    ):
                        signal(
                            key,
                            cap,
                            "В тексте автора есть запрос на услугу."
                            + ("" if fresh else " Дата неизвестна или сигнал старше 90 дней."),
                            evidence,
                        )
                if re.search(
                    r"new (?:single|album|ep)|out now|releas(?:e|ing)"
                    r"|нов\w* (?:трек|альбом|сингл)|вышел|премьера|релиз",
                    sentence,
                    re.I,
                ):
                    signal(
                        "promotion",
                        60 if fresh else 30,
                        "Упомянут релиз: повод предложить продвижение, но не подтверждение спроса.",
                        evidence,
                    )
                if re.search(r"\bdemo\b|work in progress|демо|готовит запись", sentence, re.I):
                    signal(
                        "mixing",
                        45 if fresh else 25,
                        "Упомянута работа над записью; потребность в сведении не подтверждена.",
                        evidence,
                    )
    contacts = list(dict.fromkeys(re.findall(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text)))[:5]
    return {
        "eligible": artist,
        "priority": max(s["score"] for s in services.values()),
        "services": services,
        "evidence": observations[-30:],
        "contacts": contacts,
        "profile_url": candidate.profile_url,
        "external_url": candidate.external_url,
        "explanation": "Музыкальная роль подтверждена биографией."
        if artist
        else "Не подтверждена роль исполнителя либо это профиль СМИ/бизнеса.",
        "evaluated_at": now.isoformat(),
        "method": "comment_profile_rules_v2",
    }


MAX_NEW_CANDIDATES_PER_POST = 30
POST_BATCH = 12
PAGE_KINDS_WITH_QUOTA = {"post", "tagged_post"}
PUBLICATION_KINDS = {"post", "tagged_post", "story_media"}
GROUP_OF_KIND = {
    "source": "posts",
    "post": "posts",
    "tagged_grid": "tagged",
    "tagged_post": "tagged",
    "stories": "stories",
    "story_media": "stories",
    "followers": "followers",
    "following": "following",
}


def take_batch(backlog: list[dict], size: int) -> tuple[list[dict], list[dict]]:
    """Take publications round-robin by source, so every source contributes early."""
    queues: dict[str, list[dict]] = {}
    for item in backlog:
        queues.setdefault(item["source"], []).append(item)
    batch = []
    while len(batch) < size and any(queues.values()):
        for items in queues.values():
            if items and len(batch) < size:
                batch.append(items.pop(0))
    rest = [item for items in queues.values() for item in items]
    return batch, rest


def add_evidence(session, candidate_url, observation, job_id) -> None:
    """Attach a new comment to an already assessed lead without revisiting the profile."""
    username = candidate_url.split("/")[-2]
    lead = session.scalar(
        select(Lead).where(Lead.platform == "instagram", Lead.username == username)
    )
    row = session.get(ScoutAssessment, lead.id) if lead else None
    if row is None:
        return
    evidence = {
        (e["url"], e.get("author"), e["caption"]): e
        for e in row.details.get("evidence", [])
        if e.get("kind") == "comment"
    }
    evidence[(observation["url"], observation["author"], observation["caption"])] = observation
    updated = assess(
        Candidate(
            platform="instagram",
            username=lead.username,
            bio=lead.bio,
            profile_url=lead.profile_url,
            external_url=lead.external_url,
        ),
        list(evidence.values())[-30:],
    )
    updated["profile_checked_at"] = row.details.get(
        "profile_checked_at", row.details["evaluated_at"]
    )
    row.details, row.priority = updated, updated["priority"]
    add_lead_source(session, lead.id, job_id, "comment", observation["url"])


def add_lead_source(
    session, lead_id: int, job_id: int, source_type: str, value: str | None
) -> None:
    if not value:
        return
    exists = session.scalar(
        select(LeadSource.id).where(
            LeadSource.lead_id == lead_id,
            LeadSource.search_job_id == job_id,
            LeadSource.source_provider == "instagram_scout",
            LeadSource.source_type == source_type,
            LeadSource.source_value == value[:240],
        )
    )
    if exists is None:
        session.add(
            LeadSource(
                lead_id=lead_id,
                search_job_id=job_id,
                source_provider="instagram_scout",
                source_type=source_type,
                source_value=value[:240],
            )
        )


# How the browser queue reaches a profile step: open the page, run the API request in
# the already open Instagram tab, or no page at all (profile served from the cache).
# "filtered": skipped by the candidate filters before anything was opened.
ACCESS_OF_PHASE = {
    "api": "in_place",
    "browser": "navigate",
    "cache": "none",
    "invalid": "none",
    "filtered": "none",
}
# Profile phases that opened no page, so they cost no pace.
NO_PAGE_PHASES = {"cache", "invalid", "filtered"}
STOP_REASON = {
    resolve_reasons.LOGIN_REQUIRED: "login",
    resolve_reasons.CHECKPOINT: "checkpoint",
    resolve_reasons.RATE_LIMITED: "rate_limited",
}
SKIP_REASON = {
    resolve_reasons.NOT_FOUND: PROFILE_NOT_FOUND,
    resolve_reasons.PRIVATE: PROFILE_PRIVATE,
    resolve_reasons.PARSER_ERROR: PROFILE_PARSE_FAILED,
}
EVIDENCE_METHOD = {
    "api": "instagram_web_api",
    "browser": "browser_dom",
    "merged": "instagram_web_api+browser_dom",
}
# Consecutive API answers that could not be used before the run reads pages only.
API_FAILURE_LIMIT = 3


# Source-level pages: when one fails, the source's error counter goes up.
SOURCE_PAGE_KINDS = {"source", "tagged_grid", "stories", "followers", "following"}


def is_fatal(error: Exception) -> bool:
    """Database failures (corruption, disk, lock) stop the run; a unique-constraint
    conflict is a lead race and is handled as an update."""
    return isinstance(error, DBAPIError) and not isinstance(error, IntegrityError)


def candidate_ref(task: dict) -> CandidateRef:
    return CandidateRef(
        username=task["url"].rstrip("/").split("/")[-1],
        source_username=source_name(task.get("source") or ""),
        method=task.get("method") or "comment",
        origin_url=task.get("origin_url"),
    )


def pending_context(job_id: int, task: dict) -> dict:
    """What a pending AI job needs to bring the profile back into a run."""
    return {
        "job_id": job_id,
        "url": task["url"],
        "username": task["url"].rstrip("/").split("/")[-1],
        **{k: task[k] for k in ("source", "method", "origin_url") if task.get(k)},
    }


def crm_record(
    profile: NormalizedInstagramProfile, snapshot: dict | None, url: str
) -> tuple[Candidate, dict]:
    """CRM candidate and capture evidence of a resolved profile (the lead store's format)."""
    candidate = Candidate(
        platform="instagram",
        platform_user_id=profile.id,
        username=profile.username,
        profile_url=url,
        display_name=(profile.full_name or profile.username)[:240],
        bio=(profile.biography or "")[:10000],
        followers=min(profile.followers_count or 0, 1_000_000_000),
        following=min(profile.following_count or 0, 1_000_000_000),
        is_private=bool(profile.is_private),
        external_url=(profile.external_url or "")[:2048],
    )
    evidence = {}
    if snapshot is not None:
        try:
            evidence = parse_snapshot(snapshot, url)[1]
        except ValueError:
            evidence = {}
    unknown = [
        key
        for key, value in {
            "followers": profile.followers_count,
            "following": profile.following_count,
            "bio": profile.biography,
            "last_activity_at": None,
        }.items()
        if value is None
    ]
    evidence.update(
        captured_at=profile.resolved_at.isoformat(),
        url=url,
        unknown_fields=unknown,
        method=EVIDENCE_METHOD[profile.source],
        followers_may_be_rounded=profile.source != "api",
    )
    evidence.setdefault("description", "Профиль получен через Instagram web API в окне браузера.")
    evidence.setdefault("header", "")
    evidence.setdefault("bio_method", "instagram_api")
    return candidate, evidence


def same_page(actual: str, expected: str) -> bool:
    strip = lambda url: url.split("?")[0].split("#")[0].rstrip("/").lower()  # noqa: E731
    return strip(actual) == strip(expected)


def empty_stats(sources: list[str]) -> dict:
    return {
        **{key: 0 for key in leads.RUN_COUNTERS},
        "skips": {},
        "current_source": None,
        "current_profile": None,
        "sources": sources,
        "sources_done": [],
    }


class ScoutService:
    """Drives Lead Scout runs page by page; each stage lives in the lead_scout package."""

    def __init__(
        self, sessions, capture, settings=lambda: {}, pacer_factory=Pacer, ai=None, debug_sink=None
    ):
        self.sessions, self.capture = sessions, capture
        self.settings = settings
        # Optional (job_id, step, payload) -> None; stores debug artifacts when enabled.
        self.debug_sink = debug_sink
        # Pace is per browser profile: each account has its own delays and limits.
        self.pacer_factory = pacer_factory
        self.pacers: dict[str, Pacer] = {}
        self.classification = ClassificationService(
            ai, SqlAIClassificationCache(sessions), SqlPendingAIJobs(sessions)
        )
        self.resolver = InstagramProfileResolver()
        self.profile_cache = SqlProfileCache(sessions)
        self.refreshed_date = None

    @property
    def ai(self):
        """AI profile classifier (OpenRouter); None disables AI."""
        return self.classification.ai

    @ai.setter
    def ai(self, value) -> None:
        self.classification.ai = value

    def pacer_for(self, profile_id: str) -> Pacer:
        if profile_id not in self.pacers:
            self.pacers[profile_id] = self.pacer_factory()
        return self.pacers[profile_id]

    def pacing(self) -> PacingSettings:
        return PacingSettings.model_validate(self.settings())

    def scout_settings(self) -> ScoutSettings:
        return ScoutSettings.model_validate(self.settings())

    # ---------- Accounts ----------

    def accounts(self, profile_ids: list[str]) -> dict[str, dict]:
        """Goal, progress and latest run of each browser profile."""
        rows = {}
        with self.sessions() as session:
            for profile_id in profile_ids:
                account = session.get(ScoutAccount, profile_id)
                latest = session.scalar(
                    select(ScoutRun.job_id)
                    .join(BrowserQueue, BrowserQueue.job_id == ScoutRun.job_id)
                    .where(BrowserQueue.profile_id == profile_id)
                    .order_by(ScoutRun.job_id.desc())
                    .limit(1)
                )
                rows[profile_id] = {
                    "target": account.target if account else 100,
                    "found": account.found if account else 0,
                    "run": latest,
                }
        for row in rows.values():
            row["run"] = self.state(row["run"]) if row["run"] else None
        return rows

    def set_target(self, params) -> dict:
        profile_id = str(params["profile_id"])
        target = int(params["target"])
        if not 1 <= target <= 10000:
            raise ValueError("Цель: от 1 до 10000 лидов.")
        with self.sessions.begin() as session:
            account = session.get(ScoutAccount, profile_id) or ScoutAccount(profile_id=profile_id)
            account.target = target
            if params.get("reset"):
                account.found = 0
            session.add(account)
            return {"target": account.target, "found": account.found}

    # ---------- Sources ----------

    def sources(self, values=None):
        """Legacy list API: replace the enabled set with `values`, return enabled URLs."""
        if values is not None:
            if not isinstance(values, list) or not 1 <= len(values) <= 500:
                raise ValueError("Добавьте от 1 до 500 Instagram-источников.")
            urls = list(dict.fromkeys(profile_url(value) for value in values))
            with self.sessions.begin() as session:
                for source in session.scalars(select(ScoutSource)):
                    source.enabled = source.url in urls
                now = utcnow()
                for index, url in enumerate(urls):
                    row = session.get(ScoutSource, url)
                    if row is None:
                        session.add(
                            ScoutSource(url=url, added_at=now + timedelta(microseconds=index))
                        )
                    else:
                        row.enabled = True
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(ScoutSource.url)
                    .where(ScoutSource.enabled)
                    .order_by(ScoutSource.added_at, ScoutSource.url)
                )
            )

    def source_rows(self, params=None) -> list[dict]:
        with self.sessions() as session:
            return [
                {
                    "url": row.url,
                    "username": row.url.rstrip("/").split("/")[-1],
                    "enabled": row.enabled,
                    "last_scanned_at": aware(row.last_scanned_at).isoformat()
                    if row.last_scanned_at
                    else None,
                    "status": row.status,
                    "leads_found": row.leads_found,
                    "candidates_found": row.candidates_found,
                    "profiles_resolved": row.profiles_resolved,
                    "profiles_skipped": row.profiles_skipped,
                    "errors_count": row.errors_count,
                }
                for row in session.scalars(
                    select(ScoutSource).order_by(ScoutSource.added_at, ScoutSource.url)
                )
            ]

    def add_sources(self, params) -> list[dict]:
        values = params.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= 500:
            raise ValueError("Добавьте от 1 до 500 Instagram-источников.")
        urls = list(dict.fromkeys(profile_url(value) for value in values))
        with self.sessions.begin() as session:
            if session.scalar(select(func.count()).select_from(ScoutSource)) + len(urls) > 500:
                raise ValueError("Не больше 500 источников.")
            now = utcnow()
            for index, url in enumerate(urls):
                row = session.get(ScoutSource, url)
                if row is None:
                    # Keep the typed order for source rotation.
                    session.add(ScoutSource(url=url, added_at=now + timedelta(microseconds=index)))
                else:
                    row.enabled = True
        return self.source_rows()

    def update_source(self, params) -> list[dict]:
        with self.sessions.begin() as session:
            row = session.get(ScoutSource, profile_url(params["url"]))
            if row is None:
                raise ValueError("Источник не найден.")
            row.enabled = bool(params["enabled"])
        return self.source_rows()

    def remove_source(self, params) -> list[dict]:
        with self.sessions.begin() as session:
            row = session.get(ScoutSource, profile_url(params["url"]))
            if row is not None:
                session.delete(row)
        return self.source_rows()

    # ---------- Run lifecycle ----------

    def start(self, params, settings):
        profile = params["profile_id"]
        scout = self.scout_settings()
        explicit = params.get("sources")
        cooling: list[str] = []
        if explicit:
            # Explicit list (legacy callers, smoke tests): no rotation or cooldown, given order.
            self.sources(explicit)
            picked = list(dict.fromkeys(profile_url(value) for value in explicit))
        else:
            with self.sessions.begin() as session:
                picked, cooling = memory.pick_sources(
                    session,
                    scout.scout_sources_per_run,
                    scout.scout_source_cooldown_hours,
                    scout.scout_skip_recent_sources,
                )
            if not picked:
                raise ValueError(
                    "Нет источников для запуска: добавьте и включите источники"
                    + (" или дождитесь конца кулдауна." if cooling else ".")
                )
        tasks, notices = [], []
        with self.sessions() as session:
            for url in picked:
                ctx = self._context(session, scout, profile, url, set())
                added, messages = initial_tasks(url, ctx)
                tasks += added
                notices += [message for message in messages if message not in notices]
        if not tasks:
            raise ValueError("Включите хотя бы один доступный метод поиска в настройках Scout.")
        if cooling:
            notices.append(f"Пропущены по кулдауну: {len(cooling)} источник(ов).")
        if profile != GUEST_ID:
            with self.sessions.begin() as session:
                account = session.get(ScoutAccount, profile)
                if account is None:
                    session.add(ScoutAccount(profile_id=profile))
                elif account.found >= account.target:
                    raise ValueError("Цель достигнута. Увеличьте цель или сбросьте счётчик.")
        created = self.capture.start(
            {"urls": picked, "profile_id": profile, "name": "Lead Scout"},
            settings,
        )
        with self.sessions.begin() as session:
            queue = session.get(BrowserQueue, created["id"])
            queue.urls = [task["url"] for task in tasks]
            session.add(
                ScoutRun(
                    job_id=created["id"],
                    tasks=tasks,
                    notices=notices[-30:],
                    # Rotation runs rewind the source cursor when they stop early.
                    stats={**empty_stats(picked), "rotation": not explicit},
                )
            )
            for url in picked:
                source = session.get(ScoutSource, url)
                if source is not None:
                    source.status = "queued"
            events.prune(session)
            events.emit(
                session,
                created["id"],
                "scout:run-started",
                profile_id=profile,
                sources=[source_name(url) for url in picked],
                methods=scout.scout_methods,
            )
        return created

    def state(self, job_id):
        result = self.capture.state(job_id)
        with self.sessions() as session:
            run = session.get(ScoutRun, job_id)
            if run:
                cursor = result["cursor"]
                task = run.tasks[cursor] if cursor < len(run.tasks) else {"kind": "done"}
                stats = {**empty_stats([]), **(run.stats or {})}
                access, args = "navigate", task.get("args")
                if task["kind"] == "profile":
                    access, args = self._profile_access(task)
                result.update(
                    scout=True,
                    kind=task["kind"],
                    access=access,
                    args=args,
                    # Changes when a transient failure is retried: the queue opens the page again.
                    attempt=int((stats.get("attempts") or {}).get(str(cursor), 0)),
                    notices=run.notices,
                    candidates=stats["discovered"],
                    found=run.found,
                    backlog=len(run.backlog),
                    stats=stats,
                )
                wait, reason = (
                    self.pacer_for(result["profile_id"]).wait(self.pacing(), task["kind"])
                    if result["status"] == "running" and access != "none"
                    else (0.0, None)
                )
                result.update(wait_seconds=round(wait, 1), wait_reason=reason)
        return result

    def commit(self, job_id, snapshot):
        state = self.state(job_id)
        if state["status"] != "running":
            return {"saved": False}
        if snapshot.get("blocked") or not snapshot.get("ready"):
            raise ValueError("Page unavailable")
        with self.sessions() as session:
            task = session.get(ScoutRun, job_id).tasks[state["cursor"]]
        if task["kind"] == "profile":
            return self._analyze(job_id, task, snapshot, state)
        actual = snapshot.get("url", "")
        if task["kind"] in PUBLICATION_KINDS:
            matches = post_url(actual) == task["url"]
        elif task["kind"] == "source":
            matches = profile_url(actual) == task["url"]
        else:
            matches = same_page(actual, task["url"])
        if not matches:
            raise ValueError("Navigation mismatch")
        scout = self.scout_settings()
        provider = provider_for(task["kind"])
        additions, notices = [], []
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            queue = session.get(BrowserQueue, job_id)
            # Per-run candidate set: usernames already queued in this scan run.
            yielded = {
                t["url"].rstrip("/").split("/")[-1] for t in run.tasks if t["kind"] == "profile"
            }
            ctx = self._context(session, scout, queue.profile_id, task["source"], yielded)
            result = provider.handle(task, snapshot, ctx)
            notices += result.notices
            known = {t["url"] for t in run.tasks} | {item["url"] for item in run.backlog}
            run.backlog = [
                *run.backlog,
                *[item for item in result.backlog if item["url"] not in known],
            ]
            stats = {**empty_stats([]), **(run.stats or {})}
            source = source_name(task["source"])
            self._add_metrics(stats, source, result.group, result.metrics)
            quota = MAX_NEW_CANDIDATES_PER_POST if task["kind"] in PAGE_KINDS_WITH_QUOTA else None
            observations = dict(run.observations)
            additions, summary = self.process_candidates(
                session, job_id, run, observations, known, stats, result.candidates, quota, scout
            )
            additions = [*result.steps, *additions]
            if summary:
                notices.append(summary)
            # Publications and stories count as processed only after extraction finished.
            for code, kind, status, error in result.posts:
                memory.mark_post(
                    session, memory.post_key(kind, source, code), source, kind, status, error
                )
            for story_id, status, error in result.stories:
                memory.mark_story(
                    session, memory.story_key(source, story_id), source, status, error
                )
            if result.log:
                events.emit(
                    session,
                    job_id,
                    "scout:discovery-page",
                    source=source,
                    method=result.group,
                    url=task["url"],
                    log="\n".join(result.log),
                )
            if result.debug and scout.scout_debug and self.debug_sink:
                self.debug_sink(job_id, task, result.debug)
            if task["kind"] == "post":
                stored = session.get(ScoutPost, task["url"]) or ScoutPost(
                    url=task["url"], source=task["source"]
                )
                stored.caption = str(snapshot.get("caption", ""))[:12000]
                stored.published_at = str(snapshot.get("published_at") or "")[:80] or None
                stored.mentions = [item["url"] for item in additions]
                session.add(stored)
            run.observations = observations
            run.stats = stats
        self.advance(job_id, additions, notices)
        return {"saved": True}

    def _context(self, session, scout, profile_id, source_url, yielded) -> DiscoveryContext:
        source = source_name(source_url)

        def skip(name: str, kind: str) -> list[str]:
            if kind == "story":
                return memory.story_skip_list(
                    session, name, scout.scout_story_ttl_hours, scout.scout_max_item_failures
                )
            return memory.skip_list(session, name, kind, scout.scout_max_item_failures)

        return DiscoveryContext(
            settings=scout,
            guest=profile_id == GUEST_ID,
            gate=CandidateGate(source, set(scout.scout_ignore_usernames), yielded),
            skip=skip,
        )

    @staticmethod
    def _add_metrics(stats: dict, source: str, group: str, delta: dict) -> None:
        providers = dict(stats.get("providers") or {})
        per_source = dict(providers.get(source) or {})
        current = {key: 0 for key in METRIC_KEYS} | dict(per_source.get(group) or {})
        for key, value in delta.items():
            current[key] = current.get(key, 0) + int(value)
        per_source[group] = current
        providers[source] = per_source
        stats["providers"] = providers

    def process_candidates(
        self, session, job_id, run, observations, known, stats, candidates, quota, scout
    ):
        """Scout pipeline entry for discovered candidates: duplicate checks and page quota.

        Returns profile tasks to queue and a summary; providers never reach the lead store.
        """
        elsewhere = memory.queued_elsewhere(session, job_id)
        fresh, duplicates, limited, additions = 0, set(), False, []
        for candidate, observation in candidates:
            url = profile_link(candidate.username)
            group = GROUP_OF_METHOD.get(candidate.method, candidate.method)
            source = candidate.source_username
            if observation:
                evidence = observations.get(url, [])
                if observation not in evidence:
                    observations[url] = [*evidence, observation][-30:]
            processed = memory.processed_profile(session, candidate.username)
            earlier = bool(processed) and scout.scout_skip_processed
            if url in known or url in elsewhere or earlier:
                if url not in duplicates:
                    duplicates.add(url)
                    key = (
                        "alreadyProcessed" if earlier and url not in known else "duplicatesSkipped"
                    )
                    self._add_metrics(stats, source, group, {key: 1})
                    if earlier and url not in known:
                        leads.count_skip(stats, session, profile_link(source), ALREADY_PROCESSED)
                        events.emit(
                            session,
                            job_id,
                            "scout:profile-skipped",
                            username=candidate.username,
                            source=candidate.source_username,
                            method=candidate.method,
                            reason=ALREADY_PROCESSED,
                            details="analyzed in an earlier run",
                        )
                if processed and url not in known:
                    if observation:
                        add_evidence(session, url, observation, job_id)
                    if processed.result == "lead":
                        self._seen_again(session, job_id, stats, candidate, processed)
                continue
            if quota is not None and fresh >= quota:
                limited = True
                continue
            fresh += 1
            known.add(url)
            additions.append(candidate.task())
            leads.count(stats, session, profile_link(source), "discovered")
            self._add_metrics(stats, source, group, {"candidatesFound": 1})
            events.emit(
                session,
                job_id,
                "scout:candidate-found",
                username=candidate.username,
                source=candidate.source_username,
                method=candidate.method,
                origin_url=candidate.origin_url,
            )
        summary = None
        if duplicates or limited:
            page = candidates[0][0].origin_url if candidates else ""
            summary = f"{page}: новых кандидатов {fresh}, повторов {len(duplicates)}"
            if limited:
                summary += f"; достигнут лимит {MAX_NEW_CANDIDATES_PER_POST} новых"
        return additions, summary

    @staticmethod
    def _seen_again(session, job_id, stats, candidate, processed) -> None:
        """A known lead found again through a source: no profile visit, only the source
        history and lastSeenAt are updated (a new source relation is a lead update)."""
        lead = leads.existing_lead(session, processed.instagram_user_id, candidate.username)
        if lead is None:
            return
        ref = CandidateRef(
            candidate.username,
            candidate.source_username,
            candidate.method,
            origin_url=candidate.origin_url,
        )
        now = utcnow()
        scout_row = session.get(LeadScoutProfile, lead.id)
        if scout_row is not None:
            scout_row.last_seen_at = now
        if leads.record_source(session, lead.id, ref, now):
            leads.count(stats, session, None, "leads_updated")
            events.emit(
                session,
                job_id,
                "scout:lead-updated",
                lead_id=lead.id,
                username=candidate.username,
                source=candidate.source_username,
                method=candidate.method,
                change="new source",
            )

    # ---------- Profile resolution ----------

    def _profile_access(self, task: dict) -> tuple[str, dict | None]:
        data = task.get("resolve") or {"phase": "browser"}
        access = ACCESS_OF_PHASE.get(data.get("phase"), "navigate")
        if access == "in_place":
            return access, self.resolver.request(ResolveStep.from_dict(data))
        return access, None

    def _resolve_context(self, profile_id: str, stats: dict, job_id=None) -> ProfileResolveContext:
        scout = self.scout_settings()
        resolver_stats = (stats or {}).get("resolver") or {}

        def stopped() -> bool:
            with self.sessions() as session:
                job = session.get(SearchJob, job_id)
                return job is None or job.status != "running"

        return ProfileResolveContext(
            signal=AbortSignal(stopped) if job_id else AbortSignal(),
            # Guest sessions cannot use the web API; the page is read instead.
            use_api=scout.scout_profile_api
            and profile_id != GUEST_ID
            and not resolver_stats.get("api_disabled"),
            max_retries=scout.scout_max_retries,
            max_recent_captions=scout.scout_recent_captions,
            cache=self.profile_cache,
            cache_ttl_hours=scout.scout_profile_cache_hours,
        )

    def _prepare_profile(self, session, run, queue) -> None:
        """When a profile becomes the current step, apply the candidate filters (source
        account, ignore list, processed memory) and otherwise pick its first phase (cache,
        API, page). A filtered profile is never opened."""
        if queue.cursor >= len(run.tasks):
            return
        task = run.tasks[queue.cursor]
        if task["kind"] != "profile" or "resolve" in task:
            return
        early = self._filter(candidate_ref(task), self.scout_settings(), task, session=session)
        if early.reason:
            data = {"phase": "filtered", "reason": early.reason, "details": early.details}
        else:
            username = task["url"].rstrip("/").split("/")[-1]
            first = self.resolver.begin(
                username, self._resolve_context(queue.profile_id, run.stats)
            )
            if isinstance(first, ResolveStep):
                data = first.as_dict()
            else:
                data = {"phase": "cache" if first.ok else "invalid"}
        tasks = list(run.tasks)
        tasks[queue.cursor] = {**task, "resolve": data}
        run.tasks = tasks

    def _resolve(self, job_id, task, snapshot, state):
        """One resolver step for the current profile.

        Returns the final result, None while another page step is needed, or "cancelled".
        """
        username = task["url"].rstrip("/").split("/")[-1]
        with self.sessions() as session:
            stats = dict(session.get(ScoutRun, job_id).stats or {})
        ctx = self._resolve_context(state["profile_id"], stats, job_id)
        data = (
            task.get("resolve") or ResolveStep("browser", username, utcnow().isoformat()).as_dict()
        )
        before = data.get("phase")
        try:
            if before in {"cache", "invalid"}:
                result = self.resolver.begin(username, ctx)
            else:
                result = self.resolver.advance(ResolveStep.from_dict(data), snapshot, ctx)
        except ResolveCancelled:
            return "cancelled"
        pending = isinstance(result, ResolveStep)
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            stats = {**empty_stats([]), **(run.stats or {})}
            health = dict(stats.get("resolver") or {})
            if before == "api" and not (pending and result.phase == "api"):
                # A usable API answer resets the streak; answers that fell back count.
                failed = pending and result.api is None
                health["api_failures"] = health.get("api_failures", 0) + 1 if failed else 0
                if failed and health["api_failures"] >= API_FAILURE_LIMIT:
                    if not health.get("api_disabled"):
                        run.notices = [
                            *run.notices,
                            "Instagram web API не отвечает: до конца запуска профили"
                            " читаются со страницы.",
                        ][-30:]
                    health["api_disabled"] = True
            if pending and result.api_limited and not health.get("api_disabled"):
                # A 429 of the web API does not stop the run: profiles are read from their
                # pages (with the usual pacing) and the API is not asked again this run.
                health["api_disabled"] = True
                run.notices = [
                    *run.notices,
                    "Instagram ограничил web API (429): до конца запуска профили"
                    " читаются со страницы.",
                ][-30:]
            if pending:
                tasks = list(run.tasks)
                tasks[state["cursor"]] = {**tasks[state["cursor"]], "resolve": result.as_dict()}
                run.tasks = tasks
            else:
                key = result.profile.source if result.ok and before != "cache" else None
                key = "cache" if result.ok and before == "cache" else key or "failed"
                health[key] = health.get(key, 0) + 1
            stats["resolver"] = health
            run.stats = stats
        return None if pending else result

    def halt(self, job_id: int, reason: str) -> bool:
        """Page error of a queue: scout retry/skip first, otherwise pause with a typed reason.

        Rate limits start the pacer's break; nothing retries around login, checkpoint or 429.
        Returns True when the queue keeps going.
        """
        if self.on_error(job_id, reason):
            return True
        if reason == "rate_limited":
            profile_id = self.capture.state(job_id)["profile_id"]
            self.pacer_for(profile_id).rate_limited(self.pacing())
        self.capture.stop_with_error(job_id, reason)
        return False

    def _resolve_failed(self, job_id, task, snapshot, failure: ProfileResolveFailure):
        """Login, checkpoint and 429 go to the scheduler (pause, break): they are not skips."""
        if failure.stops_run:
            self.halt(job_id, STOP_REASON[failure.reason])
            return {"saved": False, "reason": failure.reason}
        scout = self.scout_settings()
        if failure.reason == resolve_reasons.PARSER_ERROR and scout.scout_debug and self.debug_sink:
            self.debug_sink(
                job_id,
                task,
                {"reason": "profile_parse_failed", "message": failure.message[:300]},
            )
        reason = SKIP_REASON.get(failure.reason, PROFILE_UNAVAILABLE)
        return self._skip(
            job_id,
            task,
            failure.profile,
            None,
            reason,
            failure.message[:200] or None,
            resolve_log=failure.log,
        )

    def _analyze(self, job_id, task, snapshot, state):
        """Profile step: cheap filters, resolve, filters, classification, filters, then one
        transaction that saves the lead or the skip and moves the queue on."""
        scout = self.scout_settings()
        ref = candidate_ref(task)
        first_step = not task.get("announced")
        early = task.get("resolve") or {}
        if early.get("phase") == "filtered":
            # Skipped by the candidate filters when the step was prepared; nothing opened.
            return self._skip(job_id, task, None, None, early["reason"], early.get("details"))
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            run.stats = {**empty_stats([]), **(run.stats or {}), "current_profile": ref.username}
            if first_step:
                events.emit(
                    session,
                    job_id,
                    "scout:profile-resolving",
                    username=ref.username,
                    source=ref.source_username,
                )
                tasks = list(run.tasks)
                tasks[state["cursor"]] = {**tasks[state["cursor"]], "announced": True}
                run.tasks = tasks
            observations = list(run.observations.get(task["url"], []))
        with self.sessions() as session:
            task = session.get(ScoutRun, job_id).tasks[state["cursor"]]
        phase = (task.get("resolve") or {}).get("phase", "browser")
        outcome = self._resolve(job_id, task, snapshot, state)
        if outcome == "cancelled":
            return {"saved": False}
        if outcome is None:
            return {"saved": False, "pending": True}
        if not outcome.ok:
            return self._resolve_failed(job_id, task, snapshot, outcome)
        try:
            return self._classify_and_decide(
                job_id,
                task,
                state,
                ref,
                outcome.profile,
                outcome.log,
                snapshot if phase == "browser" else None,
                observations,
                scout,
            )
        except Exception as error:
            # One profile's failure never stops the run; database failures do.
            if is_fatal(error):
                raise
            return self._profile_error(job_id, task, ref, error)

    def _classify_and_decide(
        self, job_id, task, state, ref, profile, resolve_log, snapshot, observations, scout
    ):
        self._resolved(job_id, state, ref, profile)
        local = self.classification.local(profile)
        checked = self._filter(ref, scout, task, profile)
        if checked.reason == DUPLICATE_LEAD:
            return self._save_lead(
                job_id, task, ref, profile, None, checked, snapshot, observations, resolve_log
            )
        if checked.reason:
            # Cheap filters failed: the local result goes to the log, AI is not asked.
            classification = self.classification.local_only(profile, local, "not needed (filtered)")
            return self._skip(
                job_id,
                task,
                profile,
                classification,
                checked.reason,
                checked.details,
                resolve_log=resolve_log,
                decision=checked,
            )
        if not self._running(job_id):
            return {"saved": False}  # safe point: paused or stopped before classification
        self._emit(
            job_id,
            "scout:classification-started",
            username=ref.username,
            source=ref.source_username,
        )
        settings = ClassificationSettings.from_scout(scout)
        self._retry_pending_ai(job_id, settings)
        try:
            classification = self.classification.decide(
                profile, local, settings, context=pending_context(job_id, task)
            )
        except Exception as error:
            if is_fatal(error):
                raise
            log.warning("scout_classification_failed: %s", type(error).__name__)
            return self._skip(
                job_id,
                task,
                profile,
                None,
                CLASSIFICATION_FAILED,
                type(error).__name__,
                resolve_log=resolve_log,
            )
        if not self._running(job_id):
            # The AI answer is cached: the step is repeated cheaply after resume.
            return {"saved": False}
        decision = self._filter(ref, scout, task, profile, classification)
        if decision.reason and decision.reason != DUPLICATE_LEAD:
            return self._skip(
                job_id,
                task,
                profile,
                classification,
                decision.reason,
                decision.details,
                resolve_log=resolve_log,
                decision=decision,
                classified=True,
            )
        # A lead saved meanwhile by another run is updated by the same transaction.
        return self._save_lead(
            job_id,
            task,
            ref,
            profile,
            classification,
            decision,
            snapshot,
            observations,
            resolve_log,
        )

    def _filter(
        self, ref, scout, task, profile=None, classification=None, session=None
    ) -> LeadFilterDecision:
        if session is None:
            with self.sessions() as own:
                return self._filter(ref, scout, task, profile, classification, own)
        processed = memory.processed_profile(session, ref.username)
        lead = leads.existing_lead(session, profile.id, ref.username) if profile else None
        data = FilterInput(
            ref,
            profile,
            classification,
            # A profile brought back by an AI retry is checked again on purpose.
            already_processed=processed is not None and not task.get("recheck"),
            existing_lead_id=lead.id if lead else None,
        )
        return decide(data, scout)

    def _running(self, job_id) -> bool:
        with self.sessions() as session:
            job = session.get(SearchJob, job_id)
            return job is not None and job.status == "running"

    def _emit(self, job_id, event_type, **payload) -> None:
        with self.sessions.begin() as session:
            events.emit(session, job_id, event_type, **payload)

    def _atomic(self, work):
        """Run work(session) in one transaction. A unique-constraint conflict means another
        run saved the same lead meanwhile: the retry finds it and updates it instead."""
        for attempt in range(2):
            try:
                with self.sessions.begin() as session:
                    return work(session)
            except IntegrityError:
                if attempt:
                    raise
        return None

    def _resolved(self, job_id, state, ref, profile) -> None:
        """scout:profile-resolved and the resolved counter, once per profile step."""
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            task = run.tasks[state["cursor"]]
            if task.get("resolved"):
                return
            tasks = list(run.tasks)
            tasks[state["cursor"]] = {**task, "resolved": True}
            run.tasks = tasks
            stats = {**empty_stats([]), **(run.stats or {})}
            leads.count(stats, session, task.get("source"), "resolved")
            run.stats = stats
            events.emit(
                session,
                job_id,
                "scout:profile-resolved",
                username=ref.username,
                source=ref.source_username,
                via=profile.source,
                followers=profile.followers_count,
                emails=len(profile.emails),
                phones=len(profile.phones),
            )

    def _save_lead(
        self,
        job_id,
        task,
        ref,
        profile,
        classification,
        decision,
        snapshot,
        observations,
        resolve_log,
    ):
        """Create or update the CRM lead in one transaction with its source relation,
        processed profile, run and source metrics, decision log, event and queue step.

        Without a classification this is the DUPLICATE_LEAD path: the known lead gets the
        current profile data, lastSeenAt and the source; its type stays as it was.
        """
        scout = self.scout_settings()
        candidate, evidence = crm_record(profile, snapshot, task["url"])
        if not observations:
            observations = [
                {
                    "source": task.get("source"),
                    "url": ref.origin_url or task["url"],
                    "caption": "",
                    "published_at": None,
                    "kind": ref.method,
                    "author": ref.username,
                }
            ]

        def write(session):
            now = utcnow()
            existing = leads.existing_lead(session, profile.id, ref.username)
            created = existing is None
            previous_status = existing.status if existing else None
            result = self.capture.store(session, job_id, candidate, evidence, existing=existing)
            lead_id = result["lead_id"]
            # New leads get the configured CRM status; a known lead keeps its own.
            session.get(Lead, lead_id).status = (
                scout.scout_lead_status if created else previous_status
            )
            if classification is not None:
                self._assess(session, lead_id, candidate, observations, classification, profile)
            for url in dict.fromkeys(e["url"] for e in observations if e.get("url")):
                add_lead_source(session, lead_id, job_id, ref.method, url)
            scout_row = leads.fill_scout_profile(
                session, lead_id, profile, classification, ref, now
            )
            new_source = leads.record_source(session, lead_id, ref, now)
            memory.mark_profile(
                session,
                ref.username,
                ref.source_username,
                ref.method,
                "lead",
                None if created else DUPLICATE_LEAD,
                instagram_user_id=profile.id,
                category=scout_row.profile_type,
                confidence=scout_row.profile_confidence,
            )
            run = session.get(ScoutRun, job_id)
            stats = {**empty_stats([]), **(run.stats or {})}
            source_url = task.get("source")
            leads.count(stats, session, None, "analyzed")
            if classification is not None:
                leads.count(stats, session, None, "classified")
            if created:
                leads.count(stats, session, source_url, "leads")
            else:
                leads.count(stats, session, None, "leads_updated")
                skips = dict(stats.get("skips") or {})
                skips[DUPLICATE_LEAD] = skips.get(DUPLICATE_LEAD, 0) + 1
                stats["skips"] = skips
            run.stats = stats
            text = events.profile_log(
                ref.username,
                ref.source_username,
                ref.method,
                "LEAD CREATED" if created else "LEAD UPDATED",
                profile=profile,
                classification=classification,
                filters=decision.log(),
                reason=None if created else DUPLICATE_LEAD,
                details=None if created else decision.details,
                resolve_log=resolve_log,
                classification_log=classification.log if classification else None,
            )
            if classification is not None:
                self._classified_event(session, job_id, ref, classification)
            payload = dict(
                lead_id=lead_id,
                username=ref.username,
                source=ref.source_username,
                method=ref.method,
                category=scout_row.profile_type,
                confidence=scout_row.profile_confidence,
                decided_by=scout_row.profile_decided_by,
                log=text,
            )
            if created:
                events.emit(session, job_id, "scout:lead-created", **payload)
            else:
                change = "new source" if new_source else "profile data"
                events.emit(session, job_id, "scout:lead-updated", change=change, **payload)
            leads.log_decision(
                session,
                job_id,
                ref,
                "lead_created" if created else "lead_updated",
                profile=profile,
                classification=classification,
                reason=None if created else DUPLICATE_LEAD,
            )
            self._advance(session, job_id, found=created)
            return {**result, "created": created}

        return self._atomic(write)

    @staticmethod
    def _assess(session, lead_id, candidate, observations, classification, profile) -> None:
        """Service recommendations (assess) of the lead, merged with earlier evidence."""
        details = assess(candidate, observations)
        previous = session.get(ScoutAssessment, lead_id)
        if previous:
            merged = {
                (e["url"], e.get("author"), e["caption"]): e
                for e in previous.details.get("evidence", [])
            }
            merged.update(
                {(e["url"], e.get("author"), e["caption"]): e for e in details["evidence"]}
            )
            details = assess(candidate, list(merged.values())[-30:])
        details["contacts"] = list(dict.fromkeys([*profile.emails, *details["contacts"]]))[:5]
        details["explanation"] = (
            "; ".join(classification.local.reasons[:4]) or details["explanation"]
        )
        row = previous or ScoutAssessment(lead_id=lead_id)
        row.eligible, row.priority, row.details = True, details["priority"], details
        session.add(row)

    @staticmethod
    def _classified_event(session, job_id, ref, classification) -> None:
        events.emit(
            session,
            job_id,
            "scout:classification-completed",
            username=ref.username,
            source=ref.source_username,
            category=classification.category,
            confidence=classification.confidence,
            decided_by=classification.decided_by,
        )

    def _retry_pending_ai(self, job_id, settings) -> None:
        """One due pending AI job per profile step. When AI now says the profile fits
        and it is not a lead yet, it is checked again in this run (the resolver and AI
        caches make that cheap); the classifier itself never saves leads."""
        scout = self.scout_settings()
        for retried in self.classification.retry_pending(settings, limit=1):
            context = retried.context
            url, username = context.get("url"), context.get("username")
            allowed = ALLOWED_TYPES[scout.scout_profile_type]
            if not url or not username or retried.ai.category not in allowed:
                continue
            with self.sessions.begin() as session:
                lead = leads.existing_lead(session, None, username)
                queue, run = session.get(BrowserQueue, job_id), session.get(ScoutRun, job_id)
                upcoming = run.tasks[queue.cursor + 1 :]
                if lead or any(t["kind"] == "profile" and t["url"] == url for t in upcoming):
                    continue
                task = {
                    "kind": "profile",
                    "url": url,
                    # Already in the processed memory as skipped: checked again on purpose.
                    "recheck": True,
                    **{k: context[k] for k in ("source", "method", "origin_url") if k in context},
                }
                run.tasks = [*run.tasks, task]
                queue.urls = [t["url"] for t in run.tasks]
                run.notices = [
                    *run.notices,
                    f"@{username}: AI ответил после повтора ({retried.ai.category}),"
                    " профиль будет проверен ещё раз.",
                ][-30:]

    # Skips decided before the profile was analyzed stay out of the processed memory: the
    # ignore list may change, and a processed profile keeps its first result.
    UNRECORDED_SKIPS = {"ALREADY_PROCESSED", "IGNORED_USERNAME", "SOURCE_ACCOUNT"}

    def _skip(
        self,
        job_id,
        task,
        profile,
        classification,
        reason,
        details=None,
        *,
        resolve_log=None,
        decision=None,
        classified=False,
    ):
        """Record a skip with its reason in one transaction and move the queue on."""
        ref = candidate_ref(task)
        text = events.profile_log(
            ref.username,
            ref.source_username,
            ref.method,
            "SKIPPED",
            profile=profile,
            classification=classification,
            filters=decision.log() if decision else None,
            reason=reason,
            details=details,
            resolve_log=resolve_log,
            classification_log=classification.log if classification else None,
        )

        def write(session):
            if reason not in self.UNRECORDED_SKIPS:
                memory.mark_profile(
                    session,
                    ref.username,
                    ref.source_username,
                    ref.method,
                    "skipped",
                    reason,
                    instagram_user_id=profile.id if profile else None,
                    category=classification.category if classification else None,
                    confidence=classification.confidence if classification else None,
                )
            if reason == WRONG_PROFILE_TYPE:
                lead = leads.existing_lead(session, profile.id, ref.username)
                previous = session.get(ScoutAssessment, lead.id) if lead else None
                if previous:
                    previous.eligible = False
            run = session.get(ScoutRun, job_id)
            stats = {**empty_stats([]), **(run.stats or {})}
            leads.count_skip(stats, session, task.get("source"), reason)
            if profile:
                leads.count(stats, session, None, "analyzed")
            if classified:
                leads.count(stats, session, None, "classified")
                self._classified_event(session, job_id, ref, classification)
            run.stats = stats
            events.emit(
                session,
                job_id,
                "scout:profile-skipped",
                username=ref.username,
                source=ref.source_username,
                method=ref.method,
                reason=reason,
                details=details,
                category=classification.category if classification else None,
                confidence=classification.confidence if classification else None,
                log=text,
            )
            leads.log_decision(
                session,
                job_id,
                ref,
                "skipped",
                profile=profile,
                classification=classification,
                reason=reason,
                details=details,
            )
            self._advance(session, job_id, notice=f"@{ref.username}: пропущен ({reason}).")

        self._atomic(write)
        return {"saved": False, "reason": reason}

    def _profile_error(self, job_id, task, ref, error):
        """Unexpected failure while deciding on one profile: logged, counted, skipped for
        this run only (not remembered as processed), and the run goes on."""
        log.warning("scout_profile_error @%s: %s", ref.username, type(error).__name__)
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            stats = {**empty_stats([]), **(run.stats or {})}
            leads.count(stats, session, task.get("source"), "errors")
            run.stats = stats
            events.emit(
                session,
                job_id,
                "scout:error",
                reason=OTHER,
                kind="profile",
                profile=ref.username,
                source=ref.source_username,
                details=f"{type(error).__name__}: {str(error)[:160]}",
            )
            self._advance(
                session,
                job_id,
                notice=f"@{ref.username}: ошибка обработки ({type(error).__name__}), пропущен.",
            )
        return {"saved": False, "reason": OTHER}

    def advance(self, job_id, additions=None, notice=None, found=False):
        with self.sessions.begin() as session:
            self._advance(session, job_id, additions, notice, found)

    def _advance(self, session, job_id, additions=None, notice=None, found=False):
        """Move the queue to the next step in the caller's transaction."""
        notices_in = notice if isinstance(notice, list) else ([notice] if notice else [])
        queue, job, run = (
            session.get(BrowserQueue, job_id),
            session.get(SearchJob, job_id),
            session.get(ScoutRun, job_id),
        )
        account = session.get(ScoutAccount, queue.profile_id)
        pacing = self.pacing()
        done = run.tasks[queue.cursor]
        # A profile served from the cache opened nothing, so it costs no pace.
        if (done.get("resolve") or {}).get("phase") not in NO_PAGE_PHASES:
            self.pacer_for(queue.profile_id).page_done(pacing, profile=done["kind"] == "profile")
        if found:
            run.found += 1
            if account:
                account.found += 1
        # Process source pages and publications before candidates, so all evidence is available.
        remaining = run.tasks[queue.cursor + 1 :] + (additions or [])
        remaining.sort(key=lambda task: task["kind"] == "profile")
        notices = list(notices_in)
        limit = pacing.profiles_per_run
        checked = sum(t["kind"] == "profile" for t in run.tasks[: queue.cursor + 1])
        run_limited = bool(limit and checked >= limit)
        if run_limited and any(t["kind"] == "profile" for t in remaining):
            skipped = sum(t["kind"] == "profile" for t in remaining)
            remaining = [t for t in remaining if t["kind"] != "profile"]
            notices.append(f"Достигнут лимит {limit} профилей за запуск; не проверено: {skipped}.")
        if account and account.found >= account.target:
            remaining = []
            notices.append(f"Цель достигнута: найдено {account.found} из {account.target}.")
        elif not remaining and run.backlog and not run_limited:
            remaining, run.backlog = take_batch(run.backlog, POST_BATCH)
        elif not remaining and account:
            notices.append(
                "Доступные публикации источников закончились: "
                f"найдено {account.found} из {account.target}."
            )
        run.tasks = run.tasks[: queue.cursor + 1] + remaining
        queue.urls = [task["url"] for task in run.tasks]
        queue.cursor += 1
        queue.last_error = None
        self._prepare_profile(session, run, queue)
        if notices:
            run.notices = [*run.notices, *notices][-30:]
        stats = {**empty_stats([]), **(run.stats or {})}
        self._track_sources(session, job_id, run, stats, remaining)
        run.stats = stats
        if queue.cursor >= len(queue.urls):
            job.status, job.stage, job.completed_at = "completed", "completed", utcnow()
            events.emit(
                session,
                job_id,
                "scout:completed",
                **{key: stats[key] for key in leads.RUN_COUNTERS},
                skips=stats.get("skips") or {},
            )
        else:
            job.stage = "scout_reading"

    @staticmethod
    def _track_sources(session, job_id, run, stats, remaining) -> None:
        """source-started when work moves to a new source, source-completed when nothing
        of it is left."""
        upcoming = remaining[0].get("source") if remaining else None
        if upcoming and upcoming != stats["current_source"]:
            stats["current_source"] = upcoming
            row = session.get(ScoutSource, upcoming)
            if row is not None:
                row.status = "scanning"
            events.emit(session, job_id, "scout:source-started", source=source_name(upcoming))
        pending = {t.get("source") for t in remaining} | {
            item.get("source") for item in run.backlog
        }
        for url in stats["sources"]:
            if url in stats["sources_done"] or url in pending:
                continue
            stats["sources_done"] = [*stats["sources_done"], url]
            row = session.get(ScoutSource, url)
            found = 0
            if row is not None:
                # A source whose own page failed keeps its error status.
                row.status = "error" if row.status == "error" else "done"
                row.last_scanned_at = utcnow()
                found = row.leads_found
            events.emit(
                session,
                job_id,
                "scout:source-completed",
                source=source_name(url),
                leads_found=found,
            )

    def on_control(self, job_id: int, action: str) -> None:
        """Events, source statuses and the source cursor for pause/resume/cancel.

        Pause and cancel are cooperative: the queue status changes at once, a profile
        step in progress stops at its next safe point (before classification, before
        saving) and never leaves half a decision. Resume continues the same run from
        its cursor. Cancel keeps every saved lead, the processed memory and the source
        cursor (rewound to the first unfinished source).
        """
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            if run is None:
                return
            if action == "pause":
                events.emit(session, job_id, "scout:paused")
            elif action == "resume":
                events.emit(session, job_id, "scout:resumed")
            elif action == "cancel":
                stats = {**empty_stats([]), **(run.stats or {})}
                self._stop_sources(session, stats, "stopped")
                events.emit(
                    session,
                    job_id,
                    "scout:cancelled",
                    **{key: stats[key] for key in leads.RUN_COUNTERS},
                    skips=stats.get("skips") or {},
                )

    @staticmethod
    def _stop_sources(session, stats: dict, status: str) -> None:
        for url in stats["sources"]:
            row = session.get(ScoutSource, url)
            if row is not None and url not in stats["sources_done"]:
                row.status = status
        if stats.get("rotation"):
            memory.rewind_cursor(session, stats["sources"], stats["sources_done"])

    def recover_interrupted(self) -> int:
        """Crash recovery at start-up. The job manager has already turned runs that were
        running into paused/"interrupted" (never completed); here their sources are marked
        interrupted and the source cursor is rewound, so the next run continues from the
        first unfinished source and the processed memory skips what was done."""
        recovered = 0
        with self.sessions.begin() as session:
            rows = session.execute(
                select(ScoutRun, SearchJob)
                .join(SearchJob, SearchJob.id == ScoutRun.job_id)
                .where(SearchJob.stage == "interrupted")
            )
            for run, job in rows:
                stats = {**empty_stats([]), **(run.stats or {})}
                if stats.get("recovered"):
                    continue
                self._stop_sources(session, stats, "interrupted")
                run.stats = {**stats, "recovered": True, "current_profile": None}
                events.emit(
                    session,
                    job.id,
                    "scout:error",
                    reason="INTERRUPTED",
                    kind="fatal",
                    details="Приложение закрылось во время поиска.",
                )
                recovered += 1
        return recovered

    def on_error(self, job_id: int, reason: str) -> bool:
        """Handle a page error of a scout run; True when the run keeps going unpaused.

        Transient failures (timeouts, failed navigation) are retried up to the configured
        limit; unavailable pages are skipped. Login, checkpoint and rate limits stop the run
        and go back to the queue as typed errors; nothing tries to get around them.
        """
        error = error_for(reason)
        scout = self.scout_settings()
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            queue = session.get(BrowserQueue, job_id)
            job = session.get(SearchJob, job_id)
            if run is None or job.status != "running":
                return False
            stats = {**empty_stats([]), **(run.stats or {})}
            cursor = str(queue.cursor)
            attempts = dict(stats.get("attempts") or {})
            if error.retryable and attempts.get(cursor, 0) < scout.scout_max_retries:
                attempts[cursor] = attempts.get(cursor, 0) + 1
                stats["attempts"] = attempts
                run.stats = stats
                return True
            task = run.tasks[queue.cursor] if queue.cursor < len(run.tasks) else {}
            if error is InstagramUnavailableError or error.retryable:
                # Give up on this page only: record the failure and move on.
                source = source_name(task.get("source") or "")
                if task.get("kind") in {"post", "tagged_post"}:
                    code = post_url(task["url"]).rstrip("/").split("/")[-1]
                    memory.mark_post(
                        session,
                        memory.post_key(task["kind"], source, code),
                        source,
                        task["kind"],
                        "unavailable" if error is InstagramUnavailableError else "failed",
                        reason,
                    )
                self._add_metrics(
                    stats, source, GROUP_OF_KIND.get(task.get("kind"), "posts"), {"failures": 1}
                )
                # Source error: this page of the source is given up, the run goes on.
                leads.count(stats, session, task.get("source"), "errors")
                row = session.get(ScoutSource, task.get("source") or "")
                if row is not None and task.get("kind") == "source":
                    row.status = "error"
                run.stats = stats
                events.emit(
                    session,
                    job_id,
                    "scout:error",
                    reason=reason,
                    kind="profile" if task.get("kind") == "profile" else "source",
                    url=task.get("url"),
                    source=source,
                    skipped=True,
                )
                skip_page = True
            else:
                skip_page = False
        if skip_page:
            self.advance(job_id, notice=f"Страница пропущена ({reason}): {task.get('url')}")
            return True
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            stats = {**empty_stats([]), **(run.stats or {})}
            stats["errors"] += 1
            run.stats = stats
            source = session.get(ScoutSource, stats["current_source"] or "")
            if source is not None and reason == "rate_limited":
                # Stop and back off; the pacer enforces the break before the next page.
                source.status = "rate_limited"
            # A rate limit goes to the scheduler (break before the next page); login and
            # checkpoint need the user. Both pause the whole run.
            events.emit(
                session,
                job_id,
                "scout:error",
                reason=RATE_LIMITED if reason == "rate_limited" else reason,
                kind="rate_limit" if reason == "rate_limited" else "fatal",
                error=error.__name__,
                profile=stats["current_profile"],
                source=source_name(stats["current_source"] or ""),
            )
        return False

    def event_list(self, params) -> list[dict]:
        with self.sessions() as session:
            return events.listing(
                session,
                params.get("job_id"),
                int(params.get("after", 0)),
                int(params.get("limit", 200)),
            )

    def results(self):
        today = utcnow().date()
        if self.refreshed_date != today:
            # Age signals even when no new browser run has been started.
            with self.sessions.begin() as session:
                for row, lead in session.execute(select(ScoutAssessment, Lead).join(Lead)):
                    updated = assess(
                        Candidate(
                            platform="instagram",
                            username=lead.username,
                            bio=lead.bio,
                            profile_url=lead.profile_url,
                            external_url=lead.external_url,
                        ),
                        row.details["evidence"],
                    )
                    updated["profile_checked_at"] = row.details.get(
                        "profile_checked_at", row.details["evaluated_at"]
                    )
                    row.details, row.priority = updated, updated["priority"]
                    # Eligibility is decided by Lead Scout filters when the lead is saved.
            self.refreshed_date = today
        with self.sessions() as session:
            rows = session.execute(
                select(ScoutAssessment, Lead)
                .join(Lead)
                .where(ScoutAssessment.eligible, Lead.status.notin_(["rejected", "contacted"]))
                .order_by(ScoutAssessment.priority.desc(), ScoutAssessment.updated_at.desc())
                .limit(100)
            )
            return [
                {
                    "id": lead.id,
                    "username": lead.username,
                    "name": lead.display_name,
                    "status": lead.status,
                    **self.summary(assessment.details),
                }
                for assessment, lead in rows
            ]

    @staticmethod
    def summary(details):
        result = {
            **details,
            "evidence": [{**e, "caption": e["caption"][:1500]} for e in details["evidence"][-5:]],
        }
        result["services"] = {
            key: {
                **value,
                "reasons": sorted(value["reasons"], key=lambda r: -r["score"])[:3],
            }
            for key, value in details["services"].items()
        }
        return result

    def skip(self, job_id):
        state = self.state(job_id)
        if not state.get("scout") or state["status"] != "paused" or state["stage"] == "interrupted":
            raise ValueError("Можно пропустить шаг только в приостановленном поиске.")
        self.advance(job_id, notice="Пропущено пользователем: " + state["url"])
        if self.state(job_id)["status"] == "paused":
            self.capture.control(job_id, "resume")
        return {"ok": True}
