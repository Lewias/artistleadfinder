"""Lead filters with stable skip reasons, applied in a fixed order."""

from .settings import ScoutSettings

ALREADY_PROCESSED = "ALREADY_PROCESSED"
FOLLOWERS_TOO_LOW = "FOLLOWERS_TOO_LOW"
FOLLOWERS_TOO_HIGH = "FOLLOWERS_TOO_HIGH"
NO_CONTACT = "NO_CONTACT"
WRONG_PROFILE_TYPE = "WRONG_PROFILE_TYPE"
PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
CLASSIFICATION_FAILED = "CLASSIFICATION_FAILED"

ALLOWED = {
    "artists": {"artist"},
    "artists_producers": {"artist", "producer"},
    "everyone": {"artist", "producer", "media", "other"},
}


def audience_filter(followers: int | None, settings: ScoutSettings) -> str | None:
    """Followers min/max; unknown counts pass rather than guessing."""
    if followers is None:
        return None
    if followers < settings.scout_min_followers:
        return FOLLOWERS_TOO_LOW
    if followers > settings.scout_max_followers:
        return FOLLOWERS_TOO_HIGH
    return None


def contact_filter(emails: list[str], phones: list[str], settings: ScoutSettings) -> str | None:
    if settings.scout_only_contacts and not emails and not phones:
        return NO_CONTACT
    return None


def type_filter(category: str, settings: ScoutSettings) -> str | None:
    return None if category in ALLOWED[settings.scout_profile_type] else WRONG_PROFILE_TYPE


def apply_filters(
    followers: int | None,
    category: str,
    emails: list[str],
    phones: list[str],
    settings: ScoutSettings,
) -> str | None:
    """Order after the duplicate check: followers, profile type, contacts."""
    return (
        audience_filter(followers, settings)
        or type_filter(category, settings)
        or contact_filter(emails, phones, settings)
    )
