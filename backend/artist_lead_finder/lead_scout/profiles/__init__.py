"""Instagram Profile Resolver: candidate username -> normalized profile with contacts."""

from .browser import BrowserProfileProvider
from .cache import SqlProfileCache
from .model import (
    AbortSignal,
    NormalizedInstagramProfile,
    PartialProfile,
    ProfileResolveError,
    ProfileResolveFailure,
    ProfileResolveResult,
    ProfileResolveSuccess,
    ResolveCancelled,
)
from .normalize import normalize_external_url, parse_instagram_count, profile_from_fields
from .resolver import (
    InstagramProfileResolver,
    ProfileResolveContext,
    ResolveStep,
)
from .user_id import resolve_instagram_user_id

__all__ = [
    "AbortSignal",
    "BrowserProfileProvider",
    "InstagramProfileResolver",
    "NormalizedInstagramProfile",
    "PartialProfile",
    "ProfileResolveContext",
    "ProfileResolveError",
    "ProfileResolveFailure",
    "ProfileResolveResult",
    "ProfileResolveSuccess",
    "ResolveCancelled",
    "ResolveStep",
    "SqlProfileCache",
    "normalize_external_url",
    "parse_instagram_count",
    "profile_from_fields",
    "resolve_instagram_user_id",
]
