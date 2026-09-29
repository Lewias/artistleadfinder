"""InstagramProfileResolver: username -> NormalizedInstagramProfile with contacts.

Chain: cache -> API provider -> sufficient? -> browser provider -> merge -> normalize ->
contacts. The application's browser queue executes one page step at a time, so the chain
is a small state machine (begin / advance, with a serializable ResolveStep between the
calls); resolve() drives the same machine synchronously through a fetch adapter.

The resolver never classifies, saves leads, touches the CRM or sends messages.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from ...browser_capture import profile_url
from ...models import utcnow
from ..contacts import ContactExtractor, ContactSource
from .api import InstagramApiProfileProvider
from .browser import BrowserProfileProvider
from .merge import is_profile_data_sufficient, merge_profile_data, missing_fields
from .model import (
    NETWORK_ERROR,
    NEVER_ABORTED,
    NOT_FOUND,
    PARSER_ERROR,
    PRIVATE,
    RATE_LIMITED,
    STOPS_RUN,
    AbortSignal,
    NormalizedInstagramProfile,
    PartialProfile,
    ProfileResolveError,
    ProfileResolveFailure,
    ProfileResolveResult,
    ProfileResolveSuccess,
    ProviderUnavailable,
)
from .normalize import normalize_instagram_username

log = logging.getLogger(__name__)
LABELS = {
    "biography": "bio",
    "followers_count": "followers",
    "external_url": "external link",
    "category_name": "category",
}


class ProfileCache(Protocol):
    def get(self, username: str, ttl_hours: int) -> NormalizedInstagramProfile | None: ...

    def put(self, profile: NormalizedInstagramProfile) -> None: ...


@dataclass
class ProfileResolveContext:
    signal: AbortSignal = NEVER_ABORTED
    use_api: bool = True
    max_retries: int = 2
    max_recent_captions: int = 3
    cache: ProfileCache | None = None
    cache_ttl_hours: int = 12
    # resolve() only: (step, script args) -> page snapshot. Raises TimeoutError/OSError
    # for transient failures.
    fetch: Callable[["ResolveStep", dict | None], dict] | None = None
    now: Callable[[], datetime] = utcnow


@dataclass
class ResolveStep:
    """The next page the resolver needs; stored in the queue between browser steps."""

    phase: Literal["api", "browser"]
    username: str
    started_at: str
    api: dict | None = None
    attempts: int = 0
    log: list[str] = field(default_factory=list)
    # The API endpoint answered 429: this profile falls back to the page, and the
    # scheduler stops using the API for the rest of the run.
    api_limited: bool = False

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ResolveStep":
        return cls(**data)


def _yes(value) -> str:
    return "yes" if value else "no"


class InstagramProfileResolver:
    def __init__(self, api=None, browser=None, contacts=None):
        self.api = api or InstagramApiProfileProvider()
        self.browser = browser or BrowserProfileProvider()
        self.contacts = contacts or ContactExtractor()

    # ---------- Step machine (used by the Scout browser queue) ----------

    def begin(
        self, username: str, ctx: ProfileResolveContext
    ) -> "ResolveStep | ProfileResolveResult":
        ctx.signal.throw_if_aborted()
        name = normalize_instagram_username(username)
        if name is None:
            return ProfileResolveFailure(NOT_FOUND, message="not a valid Instagram username")
        header = f"[ProfileResolver][@{name}]"
        cached = self.cached(name, ctx)
        if cached is not None:
            when = f"{cached.resolved_at:%Y-%m-%d %H:%M}"
            return ProfileResolveSuccess(
                cached, [header, "", "Cache:", f"hit ({cached.source}, {when})"]
            )
        phase = "api" if ctx.use_api else "browser"
        step = ResolveStep(phase, name, ctx.now().isoformat(), log=[header])
        if not ctx.use_api:
            step.log += ["", "API:", "skipped (disabled for this run)"]
        return step

    @staticmethod
    def cached(username: str, ctx: ProfileResolveContext) -> NormalizedInstagramProfile | None:
        if ctx.cache is None or ctx.cache_ttl_hours <= 0:
            return None
        return ctx.cache.get(username, ctx.cache_ttl_hours)

    def request(self, step: ResolveStep) -> dict | None:
        """Page-script arguments of the step; the browser step reads the page without args."""
        if step.phase == "api":
            return self.api.request(step.username, profile_url(f"@{step.username}"))
        return None

    def advance(
        self, step: ResolveStep, snapshot: dict, ctx: ProfileResolveContext
    ) -> "ResolveStep | ProfileResolveResult":
        ctx.signal.throw_if_aborted()
        if step.phase == "api":
            return self._after_api(step, snapshot, ctx)
        return self._after_browser(step, snapshot, ctx)

    def on_transient(
        self, step: ResolveStep, ctx: ProfileResolveContext, message: str
    ) -> "ResolveStep | ProfileResolveResult":
        """Timeout or interrupted navigation: retry within the limit, then give up."""
        if step.attempts < ctx.max_retries:
            ctx.signal.throw_if_aborted()
            step.attempts += 1
            step.log.append(f"{step.phase}: {message}; retry {step.attempts}/{ctx.max_retries}")
            return step
        if step.phase == "api":
            return self._to_browser(step, None, ctx, f"API failed: {message}")
        api = PartialProfile.from_dict(step.api) if step.api else None
        if api is not None:
            step.log += ["", "Browser fallback:", f"failed ({message}); using API data"]
            return self._finish(step, api, None, ctx)
        return self._fail(step, NETWORK_ERROR, message)

    def _to_browser(self, step, api, ctx, note):
        ctx.signal.throw_if_aborted()  # before the browser fallback
        step.log += ["", "API:", note] if note else []
        step.phase, step.attempts = "browser", 0
        step.api = api.as_dict() if api else None
        return step

    def _after_api(self, step, snapshot, ctx):
        try:
            partial = self.api.get_profile(step.username, snapshot, ctx.max_recent_captions)
        except ProviderUnavailable as error:
            return self._to_browser(step, None, ctx, f"unavailable ({error})")
        except ProfileResolveError as error:
            if error.reason == RATE_LIMITED:
                # The web API has its own, much stricter limit than page loads; the
                # profile page is still read the ordinary way. No API retries.
                step.api_limited = True
                return self._to_browser(step, None, ctx, "rate limited (HTTP 429); API paused")
            if error.reason in STOPS_RUN or error.reason == NOT_FOUND:
                return self._fail(step, error.reason, error.message, source="API")
            if error.retryable:
                return self.on_transient(step, ctx, error.message)
            return self._to_browser(step, None, ctx, f"failed: {error.reason} ({error.message})")
        sufficient = is_profile_data_sufficient(partial)
        missing = ", ".join(LABELS[name] for name in missing_fields(partial))
        followers = partial.followers_count
        step.log += [
            "",
            "API:",
            "success" if sufficient else f"partial: missing {missing}",
            f"followers: {followers if followers is not None else 'unknown'}",
            f"bio: {_yes(partial.biography)}",
            f"external link: {_yes(partial.external_url)}",
        ]
        if sufficient:
            step.log += ["", "Browser fallback:", "not required"]
            return self._finish(step, partial, None, ctx)
        return self._to_browser(step, partial, ctx, None)

    def _after_browser(self, step, snapshot, ctx):
        api = PartialProfile.from_dict(step.api) if step.api else None
        try:
            partial = self.browser.get_profile(
                step.username, snapshot, profile_url(f"@{step.username}"), ctx.max_recent_captions
            )
        except ProfileResolveError as error:
            if api is not None and error.reason in {PARSER_ERROR, NETWORK_ERROR}:
                step.log += ["", "Browser fallback:", f"failed ({error.reason}); using API data"]
                return self._finish(step, api, None, ctx)
            return self._fail(step, error.reason, error.message, source="Browser")
        step.log += ["", "Browser fallback:", "success"]
        return self._finish(step, api, partial, ctx)

    def _normalize(self, partial: PartialProfile, source: str, now) -> NormalizedInstagramProfile:
        contacts = self.contacts.extract(
            ContactSource(
                biography=partial.biography or "",
                public_emails=partial.emails,
                public_phones=partial.phones,
                phone_country_code=partial.phone_country_code,
                contact_text=partial.contact_text,
                external_url=partial.external_url or "",
            )
        )
        return NormalizedInstagramProfile(
            username=partial.username,
            source=source,
            resolved_at=now,
            id=partial.id,
            full_name=partial.full_name,
            biography=partial.biography,
            external_url=partial.external_url or None,
            followers_count=partial.followers_count,
            following_count=partial.following_count,
            posts_count=partial.posts_count,
            category_name=partial.category_name or None,
            is_business=partial.is_business,
            is_private=partial.is_private,
            emails=contacts.emails,
            phones=contacts.phones,
            bio_links=partial.bio_links,
            recent_captions=partial.recent_captions,
        )

    def _finish(self, step, api, browser, ctx):
        merged = merge_profile_data(api, browser)
        for anomaly in merged.anomalies:
            step.log.append(f"anomaly: {anomaly}")
            log.warning("profile_merge_anomaly", extra={"username": step.username})
        if browser is not None and api is not None:
            step.log.append("Merged profile created.")
        now = ctx.now()
        profile = self._normalize(merged.profile, merged.source, now)
        profile.username = step.username
        elapsed = self._elapsed(step, now)
        step.log += [
            "",
            "Contacts:",
            f"emails: {len(profile.emails)}",
            f"phones: {len(profile.phones)}",
            "",
            f"Resolved in {elapsed}ms",
        ]
        if profile.is_private and profile.followers_count is None and not profile.biography:
            return self._fail(step, PRIVATE, "private profile without a public header")
        if ctx.cache is not None and ctx.cache_ttl_hours > 0:
            ctx.cache.put(profile)
        log.info(
            "profile_resolved",
            extra={"username": step.username, "source": profile.source, "elapsed_ms": elapsed},
        )
        return ProfileResolveSuccess(profile, step.log)

    def _fail(self, step, reason, message, source=None):
        detail = f"failed: {reason}" + (f" ({message})" if message else "")
        step.log += ["", f"{source or 'Resolver'}:", detail]
        log.info("profile_resolve_failed", extra={"username": step.username, "reason": reason})
        return ProfileResolveFailure(
            reason, retryable=reason == NETWORK_ERROR, message=message, log=step.log
        )

    @staticmethod
    def _elapsed(step: ResolveStep, now: datetime) -> int:
        try:
            started = datetime.fromisoformat(step.started_at)
            return max(0, int((now - started).total_seconds() * 1000))
        except (TypeError, ValueError):
            return 0

    # ---------- Direct use ----------

    def resolve(self, username: str, ctx: ProfileResolveContext) -> ProfileResolveResult:
        """Whole chain through `ctx.fetch`; raises ResolveCancelled when the signal aborts."""
        if ctx.fetch is None:
            raise ValueError("resolve() needs a fetch adapter")
        step = self.begin(username, ctx)
        while isinstance(step, ResolveStep):
            ctx.signal.throw_if_aborted()  # before the request
            try:
                snapshot = ctx.fetch(step, self.request(step))
            except (TimeoutError, ConnectionError, OSError) as error:
                step = self.on_transient(step, ctx, type(error).__name__)
                continue
            step = self.advance(step, snapshot, ctx)
        return step
