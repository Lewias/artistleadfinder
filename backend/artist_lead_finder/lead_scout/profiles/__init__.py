"""Instagram Profile Resolver: candidate username -> normalized profile with contacts."""

from .api import (
    InstagramApiProfileProvider,
    WebProfileInfoEndpoint,
    parse_instagram_profile_api_response,
)
from .browser import BrowserProfileProvider
from .cache import SqlProfileCache
from .merge import is_profile_data_sufficient, merge_profile_data
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
    "InstagramApiProfileProvider",
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
    "WebProfileInfoEndpoint",
    "is_profile_data_sufficient",
    "merge_profile_data",
    "normalize_external_url",
    "parse_instagram_count",
    "parse_instagram_profile_api_response",
    "profile_from_fields",
    "resolve_instagram_user_id",
]
