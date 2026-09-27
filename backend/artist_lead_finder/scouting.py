"""Bounded scout workflow and conservative, evidence-based service recommendations."""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from .browser_capture import parse_snapshot, profile_url
from .models import (
    BrowserQueue,
    Lead,
    LeadSource,
    ScoutAssessment,
    ScoutPost,
    ScoutRun,
    ScoutSource,
    SearchJob,
    utcnow,
)
from .pacing import Pacer, PacingSettings
from .providers import Candidate

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


def post_url(value):
    parsed = urlsplit(value)
    match = re.fullmatch(
        r"/(?:([a-zA-Z0-9_.]{1,30})/)?(p|reel)/([a-zA-Z0-9_-]{1,80})/?",
        parsed.path,
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"instagram.com", "www.instagram.com"}
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not match
    ):
        raise ValueError("Invalid publication URL")
    author_prefix = f"{match[1]}/" if match[1] else ""
    return f"https://www.instagram.com/{author_prefix}{match[2]}/{match[3]}/"


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
# Duplicates do not use the per-post quota, so more comments are read than candidates kept.
MAX_COMMENTS_PER_POST = 200


def checked_profiles(session, job_id) -> set[str]:
    """Candidate profiles already visited by earlier scout runs."""
    checked = set()
    for run, queue in session.execute(
        select(ScoutRun, BrowserQueue)
        .join(BrowserQueue, BrowserQueue.job_id == ScoutRun.job_id)
        .where(ScoutRun.job_id != job_id)
    ):
        checked.update(t["url"] for t in run.tasks[: queue.cursor] if t["kind"] == "profile")
    return checked


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
    row.eligible = row.eligible and updated["eligible"]
    exists = session.scalar(
        select(LeadSource.id).where(
            LeadSource.lead_id == lead.id,
            LeadSource.search_job_id == job_id,
            LeadSource.source_provider == "instagram_scout",
            LeadSource.source_type == "comment",
            LeadSource.source_value == observation["url"],
        )
    )
    if exists is None:
        session.add(
            LeadSource(
                lead_id=lead.id,
                search_job_id=job_id,
                source_provider="instagram_scout",
                source_type="comment",
                source_value=observation["url"],
            )
        )


class ScoutService:
    def __init__(self, sessions, capture, settings=lambda: {}, pacer=None):
        self.sessions, self.capture = sessions, capture
        self.settings = settings
        self.pacer = pacer or Pacer()
        self.refreshed_date = None

    def pacing(self) -> PacingSettings:
        return PacingSettings.model_validate(self.settings())

    def sources(self, values=None):
        if values is not None:
            if not isinstance(values, list) or not 1 <= len(values) <= 20:
                raise ValueError("Добавьте от 1 до 20 Instagram-источников.")
            urls = list(dict.fromkeys(profile_url(value) for value in values))
            with self.sessions.begin() as session:
                for source in session.scalars(select(ScoutSource)):
                    source.enabled = source.url in urls
                for url in urls:
                    row = session.get(ScoutSource, url)
                    if row is None:
                        session.add(ScoutSource(url=url))
                    else:
                        row.enabled = True
        with self.sessions() as session:
            return list(session.scalars(select(ScoutSource.url).where(ScoutSource.enabled)))

    def start(self, params, settings):
        urls = self.sources(params["sources"])
        created = self.capture.start(
            {"urls": urls, "profile_id": params["profile_id"], "name": "Лиды из скаут-источников"},
            settings,
        )
        with self.sessions.begin() as session:
            session.add(
                ScoutRun(
                    job_id=created["id"],
                    tasks=[{"kind": "source", "url": url, "source": url} for url in urls],
                )
            )
        return created

    def state(self, job_id):
        result = self.capture.state(job_id)
        with self.sessions() as session:
            run = session.get(ScoutRun, job_id)
            if run:
                cursor = result["cursor"]
                kind = run.tasks[cursor]["kind"] if cursor < len(run.tasks) else "done"
                result.update(
                    scout=True,
                    kind=kind,
                    notices=run.notices,
                    candidates=len(run.observations),
                )
                wait, reason = (
                    self.pacer.wait(self.pacing(), kind)
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
            run = session.get(ScoutRun, job_id)
            task = run.tasks[state["cursor"]]
            observations = run.observations
        if task["kind"] == "profile":
            candidate, _ = parse_snapshot(snapshot, task["url"])
            # Skip unrelated accounts before they enter the lead database.
            details = assess(candidate, observations.get(task["url"], []))
            if snapshot.get("private"):
                details["eligible"] = False
            if details["eligible"]:
                result = self.capture.capture(job_id, snapshot)
                with self.sessions.begin() as session:
                    previous = session.get(ScoutAssessment, result["lead_id"])
                    if previous:
                        merged = {
                            (e["url"], e.get("author"), e["caption"]): e
                            for e in previous.details.get("evidence", [])
                            if e.get("kind") == "comment"
                        }
                        merged.update(
                            {
                                (e["url"], e.get("author"), e["caption"]): e
                                for e in details["evidence"]
                            }
                        )
                        details = assess(candidate, list(merged.values())[-30:])
                    row = previous or ScoutAssessment(lead_id=result["lead_id"])
                    row.eligible, row.priority, row.details = True, details["priority"], details
                    session.add(row)
                    for url in dict.fromkeys(e["url"] for e in observations[task["url"]]):
                        session.add(
                            LeadSource(
                                lead_id=result["lead_id"],
                                search_job_id=job_id,
                                source_provider="instagram_scout",
                                source_type="comment",
                                source_value=url,
                            )
                        )
                return result
            self.advance(
                job_id, notice=f"@{candidate.username}: недостаточно признаков исполнителя."
            )
            with self.sessions.begin() as session:
                lead = session.scalar(
                    select(Lead).where(
                        Lead.platform == "instagram", Lead.username == candidate.username
                    )
                )
                previous = session.get(ScoutAssessment, lead.id) if lead else None
                if previous:
                    previous.eligible = False
            return {"saved": False}
        canonical = (
            post_url(snapshot.get("url", ""))
            if task["kind"] == "post"
            else profile_url(snapshot.get("url", ""))
        )
        if canonical != task["url"]:
            raise ValueError("Navigation mismatch")
        additions, notice = [], None
        with self.sessions.begin() as session:
            run = session.get(ScoutRun, job_id)
            known = {t["url"] for t in run.tasks}
            if task["kind"] == "source":
                for raw in snapshot.get("posts", [])[:12]:
                    try:
                        url = post_url(raw)
                    except ValueError:
                        continue
                    if url not in known:
                        # Comments change between runs; never reuse caption nominations.
                        additions.append({"kind": "post", "url": url, "source": task["source"]})
                        known.add(url)
                if not snapshot.get("posts"):
                    notice = "У источника нет доступных ссылок на публикации: " + task["source"]
            else:
                caption = str(snapshot.get("caption", ""))[:12000]
                author = snapshot.get("author", "").lower().lstrip("@")
                expected = task["source"].split("/")[-2]
                if author != expected:
                    notice = "Автор публикации не подтверждён; пропущено: " + task["url"]
                else:
                    comments = snapshot.get("comments", [])
                    if not isinstance(comments, list):
                        raise ValueError("Invalid comments snapshot")
                    if not comments:
                        notice = "Нет доступных комментариев: " + task["url"]
                    if snapshot.get("comments_limited"):
                        notice = "Прочитана доступная часть комментариев: " + task["url"]
                    candidates = []
                    checked = checked_profiles(session, job_id)
                    fresh, duplicates, limited = 0, set(), False
                    for comment in comments[:MAX_COMMENTS_PER_POST]:
                        if not isinstance(comment, dict):
                            continue
                        try:
                            candidate = profile_url(comment.get("profile_url", ""))
                        except (ValueError, AttributeError, TypeError):
                            continue
                        if candidate == task["source"]:
                            continue
                        # Candidates already queued in this run or checked in an earlier one
                        # are duplicates: they gain evidence but are not visited again.
                        duplicate = candidate in known or candidate in checked
                        if not duplicate and fresh >= MAX_NEW_CANDIDATES_PER_POST:
                            limited = True
                            continue
                        candidates.append(candidate)
                        obs = {
                            "source": task["source"],
                            "url": canonical,
                            "caption": str(comment.get("text", ""))[:1500],
                            "published_at": str(comment.get("published_at") or "")[:80] or None,
                            "kind": "comment",
                            "author": candidate.split("/")[-2],
                        }
                        evidence = observations.get(candidate, [])
                        if obs not in evidence:
                            observations[candidate] = [*evidence, obs][-30:]
                        if duplicate:
                            duplicates.add(candidate)
                            if candidate in checked and candidate not in known:
                                add_evidence(session, candidate, obs, job_id)
                        else:
                            fresh += 1
                            additions.append({"kind": "profile", "url": candidate})
                            known.add(candidate)
                    summary = f"{canonical}: новых кандидатов {fresh}, повторов {len(duplicates)}"
                    if limited:
                        summary += f"; достигнут лимит {MAX_NEW_CANDIDATES_PER_POST} новых"
                    if duplicates or limited:
                        notice = f"{notice}; {summary}" if notice else summary
                    stored = session.get(ScoutPost, canonical)
                    if stored is None:
                        stored = ScoutPost(url=canonical, source=task["source"])
                        session.add(stored)
                    stored.caption = caption
                    stored.published_at = str(snapshot.get("published_at") or "")[:80] or None
                    stored.mentions = list(dict.fromkeys(candidates))
            run.observations = dict(observations)
        self.advance(job_id, additions, notice)
        return {"saved": True}

    def advance(self, job_id, additions=None, notice=None):
        with self.sessions.begin() as session:
            queue, job, run = (
                session.get(BrowserQueue, job_id),
                session.get(SearchJob, job_id),
                session.get(ScoutRun, job_id),
            )
            pacing = self.pacing()
            done = run.tasks[queue.cursor]
            self.pacer.page_done(pacing, profile=done["kind"] == "profile")
            # Process source pages and publications before candidates, so all evidence is available.
            remaining = run.tasks[queue.cursor + 1 :] + (additions or [])
            remaining.sort(key=lambda task: task["kind"] == "profile")
            notices = [notice] if notice else []
            limit = pacing.profiles_per_run
            checked = sum(t["kind"] == "profile" for t in run.tasks[: queue.cursor + 1])
            if limit and checked >= limit and any(t["kind"] == "profile" for t in remaining):
                skipped = sum(t["kind"] == "profile" for t in remaining)
                remaining = [t for t in remaining if t["kind"] != "profile"]
                notices.append(
                    f"Достигнут лимит {limit} профилей за запуск; не проверено: {skipped}."
                )
            run.tasks = run.tasks[: queue.cursor + 1] + remaining
            queue.urls = [task["url"] for task in run.tasks]
            queue.cursor += 1
            queue.last_error = None
            if notices:
                run.notices = [*run.notices, *notices][-30:]
            if queue.cursor >= len(queue.urls):
                job.status, job.stage, job.completed_at = "completed", "completed", utcnow()
            else:
                job.stage = "scout_reading"

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
                    row.eligible = (
                        row.eligible
                        and updated["eligible"]
                        and any(e.get("kind") == "comment" for e in row.details["evidence"])
                    )
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
