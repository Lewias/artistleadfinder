"""Lead Scout runs: SMM sources -> candidates -> profiles -> classification -> leads.

Stages live in the lead_scout package; this service drives them page by page for the
browser queue and keeps evidence-based service recommendations (assess).
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .browser_capture import profile_url
from .chromium_runtime import GUEST_ID
from .lead_scout import events, memory
from .lead_scout.ai import AIUnavailable
from .lead_scout.candidates import CandidateGate, profile_link
from .lead_scout.classifier import classify
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
from .lead_scout.filters import (
    ALREADY_PROCESSED,
    PROFILE_UNAVAILABLE,
    RATE_LIMITED,
    WRONG_PROFILE_TYPE,
    audience_filter,
    contact_filter,
    type_filter,
)
from .lead_scout.memory import aware
from .lead_scout.resolver import resolve_instagram_profile
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


def same_page(actual: str, expected: str) -> bool:
    strip = lambda url: url.split("?")[0].split("#")[0].rstrip("/").lower()  # noqa: E731
    return strip(actual) == strip(expected)


def empty_stats(sources: list[str]) -> dict:
    return {
        "discovered": 0,
        "analyzed": 0,
        "leads": 0,
        "skipped": 0,
        "errors": 0,
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
        self.ai = ai
        self.refreshed_date = None

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
                    stats=empty_stats(picked),
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
                "scout:start",
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
                result.update(
                    scout=True,
                    kind=task["kind"],
                    args=task.get("args"),
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
                    if result["status"] == "running"
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
                    "discovery:page",
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
                        stats["skipped"] += 1
                        events.emit(
                            session,
                            job_id,
                            "profile:skipped",
                            username=candidate.username,
                            source=candidate.source_username,
                            method=candidate.method,
                            reason=ALREADY_PROCESSED,
                        )
                if processed and observation and url not in known:
                    add_evidence(session, url, observation, job_id)
                continue
            if quota is not None and fresh >= quota:
                limited = True
                continue
            fresh += 1
            known.add(url)
            additions.append(candidate.task())
            stats["discovered"] += 1
            self._add_metrics(stats, source, group, {"candidatesFound": 1})
            events.emit(
                session,
                job_id,
                "candidate:found",
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

    def _analyze(self, job_id, task, snapshot, state):
        """Profile page: resolve, classify, filter, optional AI, then save or skip."""
        scout = self.scout_settings()
        username = task["url"].rstrip("/").split("/")[-1]
        source = source_name(task.get("source") or "")
        method = task.get("method") or "comment"
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            run.stats = {**empty_stats([]), **(run.stats or {}), "current_profile": username}
            events.emit(session, job_id, "profile:analyzing", username=username, source=source)
            observations = list(run.observations.get(task["url"], []))
        try:
            profile, candidate, evidence = resolve_instagram_profile(snapshot, task["url"])
        except ValueError:
            return self._skip(
                job_id, task, username, source, method, None, None, None, PROFILE_UNAVAILABLE
            )
        if profile.is_private:
            return self._skip(
                job_id, task, username, source, method, profile, None, None, PROFILE_UNAVAILABLE
            )
        local = classify(profile.text())
        # Cheap filters first, so AI is not spent on profiles that fail them anyway.
        reason = audience_filter(profile.followers_count, scout) or contact_filter(
            profile.emails, profile.phones, scout
        )
        category, ai_info = local.category, None
        if not reason:
            ai_info = self._ai(profile, local, scout)
            if ai_info and "category" in ai_info:
                category = ai_info["category"]
            reason = type_filter(category, scout)
        if reason:
            return self._skip(
                job_id, task, username, source, method, profile, local, ai_info, reason
            )
        result = self.capture.capture(job_id, snapshot, advance=False, parsed=(candidate, evidence))
        origin = task.get("origin_url")
        if not observations:
            observations = [
                {
                    "source": task.get("source"),
                    "url": origin or task["url"],
                    "caption": "",
                    "published_at": None,
                    "kind": method,
                    "author": username,
                }
            ]
        log = events.profile_log(
            username,
            source,
            method,
            profile.followers_count,
            local.as_dict(),
            ai_info,
            "LEAD SAVED",
        )
        now = utcnow()
        with self.sessions.begin() as session:
            lead_id = result["lead_id"]
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
            details["explanation"] = "; ".join(local.reasons[:4]) or details["explanation"]
            row = previous or ScoutAssessment(lead_id=lead_id)
            row.eligible, row.priority, row.details = True, details["priority"], details
            session.add(row)
            for url in dict.fromkeys(e["url"] for e in observations if e.get("url")):
                add_lead_source(session, lead_id, job_id, method, url)
            ai_ok = ai_info if ai_info and "category" in ai_info else None
            scout_row = session.get(LeadScoutProfile, lead_id) or LeadScoutProfile(
                lead_id=lead_id, first_seen_at=now
            )
            scout_row.instagram_id = profile.id
            scout_row.posts_count = profile.posts_count
            scout_row.emails, scout_row.phones = profile.emails, profile.phones
            scout_row.profile_type = category
            scout_row.profile_score = local.score
            scout_row.profile_confidence = ai_ok["confidence"] if ai_ok else local.confidence
            scout_row.profile_reasons = local.reasons[:20]
            scout_row.source_username = source
            scout_row.discovery_method = method
            scout_row.origin_url = origin
            scout_row.ai_model = ai_ok["model"] if ai_ok else None
            scout_row.ai_confidence = ai_ok["confidence"] if ai_ok else None
            scout_row.last_seen_at = now
            session.add(scout_row)
            source_row = session.get(ScoutSource, task.get("source") or "")
            if source_row is not None:
                source_row.leads_found += 1
            memory.mark_profile(session, username, source, method, "lead")
            run = session.get(ScoutRun, job_id)
            stats = {**empty_stats([]), **(run.stats or {})}
            stats["analyzed"] += 1
            stats["leads"] += 1
            run.stats = stats
            events.emit(
                session,
                job_id,
                "lead:found",
                username=username,
                source=source,
                method=method,
                category=category,
                confidence=scout_row.profile_confidence,
                lead_id=lead_id,
                log=log,
            )
        self.advance(job_id, found=True)
        return result

    def _ai(self, profile, local, scout) -> dict | None:
        mode = scout.scout_ai_mode
        if mode == "off" or (mode == "uncertain" and not local.uncertain):
            return None
        model = scout.scout_ai_model
        with self.sessions() as session:
            cached = memory.ai_cached(session, profile.username, model)
            if cached:
                return {
                    "category": cached.category,
                    "confidence": cached.confidence,
                    "model": model,
                    "cached": True,
                }
        if self.ai is None:
            return {"error": "AI не настроен"}
        try:
            category, confidence = self.ai.classify(profile.text(), model)
        except AIUnavailable as error:
            log.warning("scout_ai_unavailable", extra={"error_type": type(error).__name__})
            return {"error": str(error)}
        with self.sessions.begin() as session:
            memory.ai_store(session, profile.username, category, confidence, model)
        return {"category": category, "confidence": confidence, "model": model}

    def _skip(self, job_id, task, username, source, method, profile, local, ai_info, reason):
        followers = profile.followers_count if profile else None
        log = events.profile_log(
            username,
            source,
            method,
            followers,
            local.as_dict() if local else None,
            ai_info,
            f"SKIPPED - {reason}",
        )
        with self.sessions.begin() as session:
            memory.mark_profile(session, username, source, method, "skipped", reason)
            lead = session.scalar(
                select(Lead).where(Lead.platform == "instagram", Lead.username == username)
            )
            previous = session.get(ScoutAssessment, lead.id) if lead else None
            if previous and reason == WRONG_PROFILE_TYPE:
                previous.eligible = False
            run = session.get(ScoutRun, job_id)
            stats = {**empty_stats([]), **(run.stats or {})}
            stats["skipped"] += 1
            if profile:
                stats["analyzed"] += 1
            run.stats = stats
            events.emit(
                session,
                job_id,
                "profile:skipped",
                username=username,
                source=source,
                method=method,
                reason=reason,
                log=log,
            )
        self.advance(job_id, notice=f"@{username}: пропущен ({reason}).")
        return {"saved": False, "reason": reason}

    def advance(self, job_id, additions=None, notice=None, found=False):
        notices_in = notice if isinstance(notice, list) else ([notice] if notice else [])
        with self.sessions.begin() as session:
            queue, job, run = (
                session.get(BrowserQueue, job_id),
                session.get(SearchJob, job_id),
                session.get(ScoutRun, job_id),
            )
            account = session.get(ScoutAccount, queue.profile_id)
            pacing = self.pacing()
            done = run.tasks[queue.cursor]
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
                notices.append(
                    f"Достигнут лимит {limit} профилей за запуск; не проверено: {skipped}."
                )
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
                    "scout:done",
                    **{
                        k: stats[k]
                        for k in ("discovered", "analyzed", "leads", "skipped", "errors")
                    },
                )
            else:
                job.stage = "scout_reading"

    @staticmethod
    def _track_sources(session, job_id, run, stats, remaining) -> None:
        """source:start when work moves to a new source, source:done when nothing of it is left."""
        upcoming = remaining[0].get("source") if remaining else None
        if upcoming and upcoming != stats["current_source"]:
            stats["current_source"] = upcoming
            row = session.get(ScoutSource, upcoming)
            if row is not None:
                row.status = "scanning"
            events.emit(session, job_id, "source:start", source=source_name(upcoming))
        pending = {t.get("source") for t in remaining} | {
            item.get("source") for item in run.backlog
        }
        for url in stats["sources"]:
            if url in stats["sources_done"] or url in pending:
                continue
            stats["sources_done"] = [*stats["sources_done"], url]
            row = session.get(ScoutSource, url)
            leads = 0
            if row is not None:
                row.status, row.last_scanned_at = "done", utcnow()
                leads = row.leads_found
            events.emit(session, job_id, "source:done", source=source_name(url), leads_found=leads)

    def on_control(self, job_id: int, action: str) -> None:
        """Events and source statuses for pause/resume/cancel of a scout run."""
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            if run is None:
                return
            if action == "pause":
                events.emit(session, job_id, "scout:pause")
            elif action == "cancel":
                stats = {**empty_stats([]), **(run.stats or {})}
                for url in stats["sources"]:
                    row = session.get(ScoutSource, url)
                    if row is not None and url not in stats["sources_done"]:
                        row.status = "stopped"
                events.emit(
                    session,
                    job_id,
                    "scout:stop",
                    **{k: stats[k] for k in ("discovered", "analyzed", "leads", "skipped")},
                )

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
                stats["errors"] += 1
                run.stats = stats
                events.emit(
                    session, job_id, "scout:error", reason=reason, url=task.get("url"), skipped=True
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
            events.emit(
                session,
                job_id,
                "scout:error",
                reason=RATE_LIMITED if reason == "rate_limited" else reason,
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
