"""Final Scout decision: one ordered filter pipeline over a classified profile.

SAVE LEAD or SKIP, and a skip always carries a stable reason and readable details.
A filter runs only when its input is available: the Scout pipeline calls decide()
before opening the profile (source, ignore list, processed memory), after resolving it
(everything but the profile type, so no AI is spent on a profile that is skipped
anyway) and after classification. Earlier filters simply run again: they are cheap.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from .candidates import clean_username
from .classification import FinalClassificationResult
from .profiles import NormalizedInstagramProfile
from .settings import ScoutSettings

# LeadSkipReason. The profile resolver's own reasons (PROFILE_NOT_FOUND, ...) are kept as
# more precise variants of PROFILE_UNAVAILABLE.
SOURCE_ACCOUNT = "SOURCE_ACCOUNT"
IGNORED_USERNAME = "IGNORED_USERNAME"
ALREADY_PROCESSED = "ALREADY_PROCESSED"
DUPLICATE_LEAD = "DUPLICATE_LEAD"
MISSING_REQUIRED_DATA = "MISSING_REQUIRED_DATA"
FOLLOWERS_TOO_LOW = "FOLLOWERS_TOO_LOW"
FOLLOWERS_TOO_HIGH = "FOLLOWERS_TOO_HIGH"
WRONG_PROFILE_TYPE = "WRONG_PROFILE_TYPE"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
NO_CONTACT = "NO_CONTACT"
PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
PROFILE_PRIVATE = "PROFILE_PRIVATE"
PROFILE_PARSE_FAILED = "PROFILE_PARSE_FAILED"
CLASSIFICATION_FAILED = "CLASSIFICATION_FAILED"
RATE_LIMITED = "RATE_LIMITED"
OTHER = "OTHER"

SKIP_REASONS = (
    SOURCE_ACCOUNT,
    IGNORED_USERNAME,
    ALREADY_PROCESSED,
    DUPLICATE_LEAD,
    MISSING_REQUIRED_DATA,
    FOLLOWERS_TOO_LOW,
    FOLLOWERS_TOO_HIGH,
    WRONG_PROFILE_TYPE,
    LOW_CONFIDENCE,
    NO_CONTACT,
    PROFILE_UNAVAILABLE,
    PROFILE_NOT_FOUND,
    PROFILE_PRIVATE,
    PROFILE_PARSE_FAILED,
    CLASSIFICATION_FAILED,
    RATE_LIMITED,
    OTHER,
)

# Profile type mode -> categories that pass ("artists" is the stored name of artists_only).
ALLOWED_TYPES = {
    "artists": {"artist"},
    "artists_producers": {"artist", "producer"},
    "everyone": {"artist", "producer", "media", "other"},
}


@dataclass(frozen=True)
class CandidateRef:
    username: str
    source_username: str
    method: str
    origin_id: str | None = None
    origin_url: str | None = None


@dataclass(frozen=True)
class FilterInput:
    candidate: CandidateRef
    profile: NormalizedInstagramProfile | None = None
    classification: FinalClassificationResult | None = None
    # Scout memory: the profile was decided on before (and this is not a planned re-check).
    already_processed: bool = False
    # CRM lead with the same Instagram id or username.
    existing_lead_id: int | None = None


@dataclass(frozen=True)
class FilterCheck:
    name: str
    outcome: str  # "pass", "fail" or "not checked"
    details: str = ""


@dataclass(frozen=True)
class LeadFilterDecision:
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    reason: str | None = None
    details: str | None = None
    checks: list[FilterCheck] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """False while a classification-dependent filter has not run yet."""
        return all(check.outcome != "not checked" for check in self.checks)

    def log(self) -> list[str]:
        """Readable "Filters:" block for the profile log."""
        return [
            f"{check.name}: {check.outcome}" + (f" ({check.details})" if check.details else "")
            for check in self.checks
        ]


# A filter returns None (pass, optional note) or (reason, details).
Outcome = tuple[str, str] | None


@dataclass(frozen=True)
class LeadFilter:
    name: str
    check: Callable[[FilterInput, ScoutSettings], Outcome]
    pass_note: Callable[[FilterInput, ScoutSettings], str] = lambda data, settings: ""
    # "candidate", "profile" (resolved profile) or "classification".
    needs: str = "candidate"

    def ready(self, data: FilterInput) -> bool:
        if self.needs == "classification":
            return data.classification is not None
        if self.needs == "profile":
            return data.profile is not None
        return True


def normalized_username(value: str) -> str:
    return (clean_username(value) or value.strip().lstrip("@")).casefold()


def _source_account(data: FilterInput, settings: ScoutSettings) -> Outcome:
    candidate = data.candidate
    if candidate.method == "profile":
        return None  # "Проверить профили": the source itself is the profile under check
    if normalized_username(candidate.username) == normalized_username(candidate.source_username):
        return SOURCE_ACCOUNT, f"@{candidate.username} is the source itself"
    return None


def _ignored(data: FilterInput, settings: ScoutSettings) -> Outcome:
    ignored = {normalized_username(name) for name in settings.scout_ignore_usernames if name}
    if normalized_username(data.candidate.username) in ignored:
        return IGNORED_USERNAME, "in the ignore list"
    return None


def _processed(data: FilterInput, settings: ScoutSettings) -> Outcome:
    if settings.scout_skip_processed and data.already_processed:
        return ALREADY_PROCESSED, "analyzed in an earlier run"
    return None


def _duplicate(data: FilterInput, settings: ScoutSettings) -> Outcome:
    if data.existing_lead_id is not None:
        return DUPLICATE_LEAD, f"lead #{data.existing_lead_id} is updated instead"
    return None


def _required(data: FilterInput, settings: ScoutSettings) -> Outcome:
    profile = data.profile
    if not profile.username:
        return MISSING_REQUIRED_DATA, "username missing"
    if profile.is_private:
        return PROFILE_PRIVATE, "private account"
    if (
        profile.followers_count is None
        and settings.scout_use_followers_range
        and not settings.scout_allow_unknown_followers
    ):
        return MISSING_REQUIRED_DATA, "followers count unknown"
    return None


def _followers(data: FilterInput, settings: ScoutSettings) -> Outcome:
    followers = data.profile.followers_count
    if followers is None or not settings.scout_use_followers_range:
        return None
    if followers < settings.scout_min_followers:
        return FOLLOWERS_TOO_LOW, f"{followers} < min {settings.scout_min_followers}"
    if followers > settings.scout_max_followers:
        return FOLLOWERS_TOO_HIGH, f"{followers} > max {settings.scout_max_followers}"
    return None


def _followers_note(data: FilterInput, settings: ScoutSettings) -> str:
    followers = data.profile.followers_count
    return "unknown, allowed" if followers is None else str(followers)


def _profile_type(data: FilterInput, settings: ScoutSettings) -> Outcome:
    category = data.classification.category
    if category not in ALLOWED_TYPES[settings.scout_profile_type]:
        return WRONG_PROFILE_TYPE, f"{category} not allowed in {settings.scout_profile_type}"
    return None


def _confidence(data: FilterInput, settings: ScoutSettings) -> Outcome:
    threshold = settings.scout_min_confidence
    confidence = data.classification.confidence
    if threshold and confidence < threshold:
        return LOW_CONFIDENCE, f"{confidence}% < min {threshold}%"
    return None


def _contacts(data: FilterInput, settings: ScoutSettings) -> Outcome:
    profile = data.profile
    if settings.scout_only_contacts and not (profile.emails or profile.phones):
        return NO_CONTACT, "no email or phone in the profile"
    return None


def _contacts_note(data: FilterInput, settings: ScoutSettings) -> str:
    profile = data.profile
    found = [
        name for name, values in (("email", profile.emails), ("phone", profile.phones)) if values
    ]
    return ", ".join(found) or ("not required" if not settings.scout_only_contacts else "")


FILTERS: tuple[LeadFilter, ...] = (
    LeadFilter("source account", _source_account),
    LeadFilter("ignored username", _ignored),
    LeadFilter("already processed", _processed),
    # The Instagram id is known only after resolving, so the identity check waits for it.
    LeadFilter("duplicate lead", _duplicate, needs="profile"),
    LeadFilter("required data", _required, needs="profile"),
    LeadFilter("followers", _followers, _followers_note, needs="profile"),
    LeadFilter(
        "profile type",
        _profile_type,
        lambda data, settings: data.classification.category,
        needs="classification",
    ),
    LeadFilter(
        "confidence",
        _confidence,
        lambda data, settings: f"{data.classification.confidence}%",
        needs="classification",
    ),
    LeadFilter("contacts", _contacts, _contacts_note, needs="profile"),
)


def decide(data: FilterInput, settings: ScoutSettings) -> LeadFilterDecision:
    """Apply FILTERS strictly in order; the first failing filter decides.

    A filter whose input is missing is "not checked" and the later filters still run,
    so a resolved profile without contacts is skipped before AI is asked about its type.
    The decision is accepted only when every filter ran.
    """
    checks: list[FilterCheck] = []
    for item in FILTERS:
        if not item.ready(data):
            checks.append(FilterCheck(item.name, "not checked"))
            continue
        outcome = item.check(data, settings)
        if outcome is not None:
            reason, details = outcome
            checks.append(FilterCheck(item.name, "fail", details))
            return LeadFilterDecision(False, reason=reason, details=details, checks=checks)
        checks.append(FilterCheck(item.name, "pass", item.pass_note(data, settings)))
    if any(check.outcome == "not checked" for check in checks):
        return LeadFilterDecision(False, checks=checks)
    reasons = [
        f"{data.classification.category} {data.classification.confidence}%",
        *[f"{check.name}: {check.details or 'pass'}" for check in checks[4:]],
    ]
    return LeadFilterDecision(True, reasons=reasons, checks=checks)
