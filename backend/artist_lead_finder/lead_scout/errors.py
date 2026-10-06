"""Typed Instagram discovery errors; only transient ones are retried."""


class InstagramError(Exception):
    reason = "instagram_error"
    retryable = False


class InstagramAuthRequiredError(InstagramError):
    reason = "login"


class InstagramCheckpointError(InstagramError):
    reason = "checkpoint"


class InstagramRateLimitError(InstagramError):
    reason = "rate_limited"


class InstagramUnavailableError(InstagramError):
    """The page, post or story is gone or hidden; skip it, do not retry."""

    reason = "unavailable"


class InstagramWindowClosedError(InstagramError):
    """The account's browser window is closed: the run pauses until it is open again,
    instead of skipping every page it cannot load."""

    reason = "closed"


class InstagramTransientError(InstagramError):
    """Timeout, failed navigation or network hiccup; retried up to the configured limit."""

    reason = "loading"
    retryable = True


BY_REASON = {
    cls.reason: cls
    for cls in (
        InstagramAuthRequiredError,
        InstagramCheckpointError,
        InstagramRateLimitError,
        InstagramUnavailableError,
        InstagramWindowClosedError,
        InstagramTransientError,
    )
}


def error_for(reason: str | None) -> type[InstagramError]:
    """Map a browser-queue stop reason to its typed error ('blocked' means login needed)."""
    if reason == "blocked":
        return InstagramAuthRequiredError
    return BY_REASON.get(reason or "", InstagramTransientError)
