"""Profile resolver models: provider partials, the normalized profile and typed results.

A PartialProfile field set to None means "this source did not say"; an empty string or
False means "this source said: nothing". Merging and the sufficiency check rely on that.
"""

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Literal

from ..classification.model import ProfileText

ProfileSource = Literal["api", "browser", "merged"]

# Failure reasons the Scout pipeline acts on.
NOT_FOUND = "NOT_FOUND"
PRIVATE = "PRIVATE"
LOGIN_REQUIRED = "LOGIN_REQUIRED"
CHECKPOINT = "CHECKPOINT"
RATE_LIMITED = "RATE_LIMITED"
NETWORK_ERROR = "NETWORK_ERROR"
PARSER_ERROR = "PARSER_ERROR"
REASONS = (
    NOT_FOUND,
    PRIVATE,
    LOGIN_REQUIRED,
    CHECKPOINT,
    RATE_LIMITED,
    NETWORK_ERROR,
    PARSER_ERROR,
)
# Only temporary network failures are worth another attempt.
RETRYABLE = {NETWORK_ERROR}
# These stop the whole run: the user or the scheduler must act, never a retry loop.
STOPS_RUN = {LOGIN_REQUIRED, CHECKPOINT, RATE_LIMITED}


@dataclass
class PartialProfile:
    """What one provider could read; never a raw Instagram response."""

    source: Literal["api", "browser"]
    username: str | None = None
    id: str | None = None
    full_name: str | None = None
    biography: str | None = None
    external_url: str | None = None
    followers_count: int | None = None
    following_count: int | None = None
    posts_count: int | None = None
    category_name: str | None = None
    is_business: bool | None = None
    is_private: bool | None = None
    # Public contact fields as the source gave them; extraction normalizes later.
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    phone_country_code: str | None = None
    bio_links: list[str] = field(default_factory=list)
    recent_captions: list[str] = field(default_factory=list)
    # Visible contact text of the profile header (buttons, address); never comments.
    contact_text: str = ""
    # Extraction strategy per field, for logs and debugging.
    strategies: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PartialProfile":
        names = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names})


@dataclass
class NormalizedInstagramProfile:
    username: str
    source: ProfileSource
    resolved_at: datetime
    id: str | None = None
    full_name: str | None = None
    biography: str | None = None
    external_url: str | None = None
    followers_count: int | None = None
    following_count: int | None = None
    posts_count: int | None = None
    category_name: str | None = None
    is_business: bool | None = None
    is_private: bool | None = None
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    bio_links: list[str] = field(default_factory=list)
    recent_captions: list[str] = field(default_factory=list)

    def text(self) -> ProfileText:
        """Classifier input; the resolver itself never classifies."""
        return ProfileText(
            username=self.username,
            full_name=self.full_name or "",
            biography=self.biography or "",
            category_name=self.category_name or "",
            external_url=self.external_url or "",
            bio_links=self.bio_links,
            recent_captions=self.recent_captions,
        )

    def as_dict(self) -> dict:
        data = asdict(self)
        data["resolved_at"] = self.resolved_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "NormalizedInstagramProfile":
        names = {item.name for item in fields(cls)}
        values = {key: value for key, value in data.items() if key in names}
        values["resolved_at"] = datetime.fromisoformat(values["resolved_at"])
        return cls(**values)


@dataclass
class ProfileResolveSuccess:
    profile: NormalizedInstagramProfile
    log: list[str] = field(default_factory=list)
    ok: Literal[True] = True


@dataclass
class ProfileResolveFailure:
    reason: str
    retryable: bool = False
    message: str = ""
    # Public header data kept for a private profile, when the page showed it.
    profile: NormalizedInstagramProfile | None = None
    log: list[str] = field(default_factory=list)
    ok: Literal[False] = False

    @property
    def stops_run(self) -> bool:
        return self.reason in STOPS_RUN


ProfileResolveResult = ProfileResolveSuccess | ProfileResolveFailure


class ProfileResolveError(Exception):
    """A provider could not produce a profile; carries a typed reason."""

    def __init__(self, reason: str, message: str = ""):
        if reason not in REASONS:
            raise ValueError(f"Unknown resolve reason: {reason}")
        super().__init__(message or reason)
        self.reason = reason
        self.message = message
        self.retryable = reason in RETRYABLE


class ProviderUnavailable(Exception):
    """A provider cannot answer (no Instagram tab, changed endpoint); try the next one."""


class ResolveCancelled(Exception):
    """The run was stopped; the profile must not be recorded as failed."""


class AbortSignal:
    """Python counterpart of AbortSignal: `aborted` is re-evaluated on every check."""

    def __init__(self, check=lambda: False):
        self._check = check

    @property
    def aborted(self) -> bool:
        return bool(self._check())

    def throw_if_aborted(self) -> None:
        if self.aborted:
            raise ResolveCancelled()


NEVER_ABORTED = AbortSignal()
