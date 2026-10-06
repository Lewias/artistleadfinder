"""InstagramProfileResolver: username -> NormalizedInstagramProfile with contacts.

Chain: cache -> profile page -> normalize -> contacts. The application's browser queue
executes one page step at a time, so the chain is a small state machine (begin / advance,
with a serializable ResolveStep between the calls); resolve() drives the same machine
synchronously through a fetch adapter.

The resolver never classifies, saves leads, touches the CRM or sends messages.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Protocol

from ...browser_capture import profile_url
from ...models import utcnow
from ..contacts import ContactExtractor, ContactSource
from .browser import BrowserProfileProvider
from .model import (
    NETWORK_ERROR,
    NEVER_ABORTED,
    NOT_FOUND,
    PRIVATE,
    AbortSignal,
    NormalizedInstagramProfile,
    PartialProfile,
    ProfileResolveError,
    ProfileResolveFailure,
    ProfileResolveResult,
    ProfileResolveSuccess,
)
from .normalize import normalize_instagram_username

log = logging.getLogger(__name__)


class ProfileCache(Protocol):
    def get(self, username: str, ttl_hours: int) -> NormalizedInstagramProfile | None: ...

    def put(self, profile: NormalizedInstagramProfile) -> None: ...


@dataclass
class ProfileResolveContext:
    signal: AbortSignal = NEVER_ABORTED
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

    phase: str  # always "browser": the profile page is the only source
    username: str
    started_at: str
    attempts: int = 0
    log: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ResolveStep":
        # Steps saved by older versions may carry web API fields; they are dropped.
        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


class InstagramProfileResolver:
    def __init__(self, browser=None, contacts=None):
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
        return ResolveStep("browser", name, ctx.now().isoformat(), log=[header])

    @staticmethod
    def cached(username: str, ctx: ProfileResolveContext) -> NormalizedInstagramProfile | None:
        if ctx.cache is None or ctx.cache_ttl_hours <= 0:
            return None
        return ctx.cache.get(username, ctx.cache_ttl_hours)

    def advance(
        self, step: ResolveStep, snapshot: dict, ctx: ProfileResolveContext
    ) -> "ResolveStep | ProfileResolveResult":
        ctx.signal.throw_if_aborted()
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
        return self._fail(step, NETWORK_ERROR, message)

    def _after_browser(self, step, snapshot, ctx):
        try:
            partial = self.browser.get_profile(
                step.username, snapshot, profile_url(f"@{step.username}"), ctx.max_recent_captions
            )
        except ProfileResolveError as error:
            return self._fail(step, error.reason, error.message, source="Browser")
        step.log += ["", "Browser:", "success"]
        return self._finish(step, partial, ctx)

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

    def _finish(self, step, browser: PartialProfile, ctx):
        now = ctx.now()
        profile = self._normalize(browser, browser.source, now)
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
                snapshot = ctx.fetch(step, None)
            except (TimeoutError, ConnectionError, OSError) as error:
                step = self.on_transient(step, ctx, type(error).__name__)
                continue
            step = self.advance(step, snapshot, ctx)
        return step
