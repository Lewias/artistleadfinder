"""Parse bounded page observations and persist browser queues; no network access."""

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from .discovery import DiscoveryRecord
from .errors import UserError
from .models import BrowserQueue, Lead, LeadAnalysis, SearchJob, utcnow
from .pipeline import CandidatePipeline
from .providers import Candidate
from .schemas import SearchConfiguration
from .scoring import LeadScorer, ScoringWeights

log = logging.getLogger(__name__)

RESERVED = {
    "accounts",
    "explore",
    "reels",
    "reel",
    "p",
    "direct",
    "stories",
    "challenge",
    "web",
    "about",
    "developer",
    "legal",
    "privacy",
    "terms",
}


def profile_url(value: str) -> str:
    value = value.strip()
    if value.startswith("@"):
        value = "https://www.instagram.com/" + value[1:]
    parsed = urlsplit(value)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"instagram.com", "www.instagram.com"}
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or len(parts) != 1
        or not re.fullmatch(r"[a-zA-Z0-9_.]{1,30}", parts[0])
        or parts[0].lower() in RESERVED
    ):
        raise ValueError("Expected Instagram profile URL")
    return f"https://www.instagram.com/{parts[0].lower()}/"


def count_from(text: str, label: str) -> int | None:
    patterns = {
        "followers": r"followers|подписчик(?:ов|а|и)?",
        "following": r"following|подписок|подписки",
    }
    number = r"(\d(?:[\d \t.,\u00a0\u202f]*\d)?[ \t\u00a0\u202f]*(?:[kKmM]|тыс\.?|млн\.?)?)"
    match = re.search(number + r"[ \t\u00a0\u202f]*(?:" + patterns[label] + r")\b", text, re.I)
    if not match:
        match = re.search(r"(?:" + patterns[label] + r")\s*:\s*" + number, text, re.I)
    if not match:
        return None
    value = re.sub(r"\s", "", match.group(1)).lower()
    multiplier = 1
    suffix = re.search(r"(k|m|тыс\.?|млн\.?)$", value)
    if suffix:
        multiplier = 1000 if suffix.group(1).startswith(("k", "тыс")) else 1_000_000
        value = value[: suffix.start()].replace(",", ".")
    else:
        value = value.replace(",", "").replace(".", "")
    try:
        return min(1_000_000_000, int(float(value) * multiplier))
    except ValueError:
        return None


def parse_snapshot(snapshot: dict, expected: str | None = None) -> tuple[Candidate, dict]:
    if snapshot.get("blocked"):
        raise ValueError("Page requires user action")
    url = profile_url(snapshot.get("url", ""))
    if expected and url != profile_url(expected):
        raise ValueError("Profile navigation mismatch")
    username = url.split("/")[-2]
    title = str(snapshot.get("title", ""))[:1000]
    description = str(snapshot.get("description", ""))[:12000]
    header = str(snapshot.get("header", ""))[:12000]
    if not snapshot.get("ready") or not (header or f"@{username}" in title.lower()):
        raise ValueError("Profile not loaded")
    text = header + "\n" + description
    followers = count_from(text, "followers")
    following = count_from(text, "following")
    name = re.match(r"^(.*?)\s*\(@[\w.]+\)", title)
    bio_match = re.search(r'Instagram:\s*["“](.*?)["”]\s*$', description, re.S)
    bio = str(snapshot.get("bio", ""))[:10000] or (bio_match.group(1) if bio_match else "")
    bio_method = "page_bio_or_description"
    if not bio and header:
        lines = [line.strip() for line in header.splitlines() if line.strip()]
        stats = [
            index
            for index, line in enumerate(lines)
            if count_from(line, "followers") is not None
            or count_from(line, "following") is not None
        ]
        if stats:
            excerpt = []
            display_name = name.group(1).strip() if name else username
            for line in lines[max(stats) + 1 :]:
                if re.search(r"(?:https?://|[\w-]+\.(?:com|to|net|link|bio|me|org)/)", line):
                    break
                if line.lower().lstrip("@") in {username, display_name.lower()}:
                    continue
                excerpt.append(line)
                if len(excerpt) >= 8:
                    break
            bio = "\n".join(excerpt)[:10000]
            bio_method = "header_excerpt"
    external = str(snapshot.get("external_url", ""))[:2048]
    unknown = [
        key
        for key, value in {
            "followers": followers,
            "following": following,
            "bio": bio or None,
            "last_activity_at": None,
        }.items()
        if value is None
    ]
    fields = dict(
        platform="instagram",
        username=username,
        profile_url=url,
        display_name=name.group(1).strip() if name else username,
        bio=bio,
        followers=followers or 0,
        following=following or 0,
        external_url=external,
        is_private=bool(snapshot.get("private", False)),
    )
    candidate = Candidate.model_validate(fields)
    evidence = dict(
        captured_at=datetime.now(timezone.utc).isoformat(),
        url=url,
        unknown_fields=unknown,
        description=description[:3000],
        header=header[:4000],
        method="browser_dom",
        followers_may_be_rounded=True,
        bio_method=bio_method,
    )
    return candidate, evidence


class BrowserCaptureService:
    def __init__(self, sessions):
        self.sessions = sessions

    def start(self, params: dict, settings: dict) -> dict:
        values = params["urls"]
        if not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise ValueError("Use between 1 and 100 links")
        urls = list(dict.fromkeys(profile_url(value) for value in values))
        profile = params["profile_id"]
        if not re.fullmatch(r"[0-9a-f]{32}", profile):
            raise ValueError("Invalid browser profile")
        config = SearchConfiguration(
            name=params.get("name", "Артисты из браузера"),
            seed_accounts=urls,
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
        with self.sessions.begin() as session:
            # One queue per browser profile; different accounts may run in parallel.
            active = session.scalar(
                select(SearchJob.id)
                .join(BrowserQueue)
                .where(
                    BrowserQueue.profile_id == profile,
                    SearchJob.status.in_(["running", "paused"]),
                    SearchJob.stage != "interrupted",
                )
            )
            if active:
                raise UserError("Для этого профиля уже идёт поиск. Остановите или завершите его.")
            job = SearchJob(
                **config.model_dump(),
                status="running",
                stage="browser_loading",
                started_at=utcnow(),
            )
            session.add(job)
            session.flush()
            session.add(
                BrowserQueue(
                    job_id=job.id, profile_id=profile, urls=urls, weights=settings["weights"]
                )
            )
            return {"id": job.id}

    def state(self, job_id: int) -> dict:
        with self.sessions() as session:
            job, queue = session.get(SearchJob, job_id), session.get(BrowserQueue, job_id)
            if not job or not queue:
                raise ValueError("Browser queue not found")
            return dict(
                id=job.id,
                status=job.status,
                stage=job.stage,
                cursor=queue.cursor,
                total=len(queue.urls),
                url=queue.urls[queue.cursor] if queue.cursor < len(queue.urls) else None,
                profile_id=queue.profile_id,
                error=queue.last_error,
            )

    def latest(self):
        with self.sessions() as session:
            identifier = session.scalar(
                select(BrowserQueue.job_id).order_by(BrowserQueue.job_id.desc()).limit(1)
            )
        return self.state(identifier) if identifier else None

    def control(self, job_id: int, action: str):
        with self.sessions.begin() as session:
            job = session.get(SearchJob, job_id)
            if not job or job.status not in {"running", "paused"}:
                raise ValueError("Queue already finished")
            if action not in {"pause", "cancel", "resume"}:
                raise ValueError("Unknown action")
            # Restarted workers are not silently recreated by a generic UI control.
            if job.stage == "interrupted" and action == "resume":
                raise ValueError("Create a new queue after restart")
            job.status = {"pause": "paused", "resume": "running", "cancel": "cancelled"}[action]
            if action == "cancel":
                job.completed_at = utcnow()

    def stop_with_error(self, job_id: int, message: str):
        messages = {
            "blocked": "Требуется вход или подтверждение. Проверьте браузер и продолжите очередь.",
            "login": "Instagram требует вход. Войдите в окне браузера и продолжите очередь.",
            "checkpoint": (
                "Instagram просит подтвердить аккаунт (checkpoint). Пройдите проверку в окне"
                " браузера вручную и продолжите очередь."
            ),
            "unavailable": "Страница недоступна.",
            "rate_limited": (
                "Instagram ограничил запросы. После продолжения очередь выдержит перерыв."
            ),
            "loading": "Страница не загрузилась или профиль недоступен. Проверьте окно браузера.",
            "closed": "Окно браузера закрыто. Откройте тот же профиль и продолжите.",
            "save": "Не удалось сохранить профиль. Проверьте страницу и повторите.",
        }
        with self.sessions.begin() as session:
            job, queue = session.get(SearchJob, job_id), session.get(BrowserQueue, job_id)
            if job.status not in {"running", "paused"}:
                return
            queue.last_error = messages.get(message, messages["loading"])
            log.warning(
                "queue_paused",
                extra={"job_id": job_id, "reason": message, "detail": queue.last_error},
            )
            job.status = "paused"
            job.stage = "browser_attention"
            job.errors = [
                *job.errors[-19:],
                {"provider": "instagram_browser", "message": queue.last_error},
            ]

    def capture(self, job_id: int, snapshot: dict, advance: bool = True) -> dict:
        state = self.state(job_id)
        if state["status"] != "running":
            return {"saved": False}
        candidate, evidence = parse_snapshot(snapshot, state["url"])
        with self.sessions.begin() as session:
            result = self.store(session, job_id, candidate, evidence)
            job, queue = session.get(SearchJob, job_id), session.get(BrowserQueue, job_id)
            if advance:
                queue.cursor += 1
                queue.last_error = None
                if queue.cursor >= len(queue.urls):
                    job.status, job.stage, job.completed_at = "completed", "completed", utcnow()
                else:
                    job.stage = "browser_loading"
            return result

    def store(self, session, job_id: int, candidate, evidence: dict, existing=None) -> dict:
        """Create or update the CRM lead of a captured profile in the caller's transaction.

        `existing` is the lead already matched by identity (Lead Scout matches the
        Instagram id first); otherwise the username is used.
        """
        queue = session.get(BrowserQueue, job_id)
        job = session.get(SearchJob, job_id)
        config = SearchConfiguration(
            **{key: getattr(job, key) for key in SearchConfiguration.model_fields}
        )
        weights = ScoringWeights.model_validate(queue.weights)
        existing = existing or session.scalar(
            select(Lead).where(Lead.platform == "instagram", Lead.username == candidate.username)
        )
        if existing:
            # Missing page fields must not erase previously observed values.
            for key in evidence["unknown_fields"]:
                if hasattr(existing, key):
                    setattr(candidate, key, getattr(existing, key))
            if not candidate.external_url:
                candidate.external_url = existing.external_url
        result = CandidatePipeline(self.sessions, LeadScorer(weights)).process(
            session,
            job_id,
            DiscoveryRecord(candidate, "instagram_browser", "profile", candidate.profile_url),
            config,
        )
        lead = session.scalar(
            select(Lead).where(Lead.platform == "instagram", Lead.username == candidate.username)
        )
        analysis = session.get(LeadAnalysis, lead.id)
        analysis.extracted_signals = {**analysis.extracted_signals, "browser_capture": evidence}
        analysis.analyzed_at = utcnow()
        if result.analyzed:
            job.candidates_found += 1
            job.profiles_analyzed += 1
            job.artists_detected += int(result.artist)
            job.qualified_leads += int(result.qualified)
        return {
            "saved": True,
            "lead_id": lead.id,
            "score": lead.lead_score,
            "unknown_fields": evidence["unknown_fields"],
        }
