"""Instagram web API profile provider.

The request runs inside the application's own logged-in Instagram tab (profile_api.js):
the browser attaches the session cookies itself, so this code never reads, stores or
logs cookies, CSRF tokens or authorization headers. Endpoints live in adapters; the
resolver only sees PartialProfile or a typed error.
"""

import re
from urllib.parse import quote

from .model import (
    CHECKPOINT,
    LOGIN_REQUIRED,
    NETWORK_ERROR,
    NOT_FOUND,
    PARSER_ERROR,
    RATE_LIMITED,
    PartialProfile,
    ProfileResolveError,
    ProviderUnavailable,
)
from .normalize import normalize_instagram_username, profile_from_fields

RATE_LIMIT_TEXT = re.compile(
    r"wait a few minutes|try again later|too many requests|rate.?limit|feedback_required", re.I
)
CHECKPOINT_TEXT = re.compile(r"checkpoint_required|challenge_required|checkpoint", re.I)
LOGIN_TEXT = re.compile(r"login_required|not.?logged.?in|require_login", re.I)


class WebProfileInfoEndpoint:
    """GET /api/v1/users/web_profile_info/?username=<username> (the web client's own call)."""

    name = "web_profile_info"

    def request(self, username: str) -> dict:
        return {
            "endpoint": self.name,
            "path": f"/api/v1/users/web_profile_info/?username={quote(username)}",
        }

    @staticmethod
    def user(body: dict):
        data = body.get("data")
        return data.get("user") if isinstance(data, dict) else None


def _message(body) -> str:
    if not isinstance(body, dict):
        return ""
    parts = [body.get(key) for key in ("message", "error_type", "status")]
    if body.get("require_login"):
        parts.append("require_login")
    if body.get("checkpoint_url"):
        parts.append("checkpoint_required")
    if body.get("spam"):
        parts.append("feedback_required")
    return " ".join(str(part) for part in parts if part)


def parse_instagram_profile_api_response(
    response: dict, endpoint=None, max_captions: int = 3
) -> PartialProfile:
    """One API reply of profile_api.js to a PartialProfile, or a typed error.

    `response` is {status, body, error, redirect}; the raw body goes no further than here.
    Raises ProfileResolveError for Instagram's answers and ProviderUnavailable when the
    API could not be asked at all (the browser provider takes over then).
    """
    endpoint = endpoint or WebProfileInfoEndpoint()
    if not isinstance(response, dict):
        raise ProviderUnavailable("no API response")
    error = response.get("error")
    if error in {"timeout", "network"}:
        raise ProfileResolveError(NETWORK_ERROR, f"API {error}")
    if error:
        raise ProviderUnavailable(str(error)[:80])
    redirect = response.get("redirect")
    if redirect == "login":
        raise ProfileResolveError(LOGIN_REQUIRED, "API redirected to login")
    if redirect == "challenge":
        raise ProfileResolveError(CHECKPOINT, "API redirected to a checkpoint")
    status = response.get("status")
    body = response.get("body")
    message = _message(body)
    if status == 429 or RATE_LIMIT_TEXT.search(message):
        raise ProfileResolveError(RATE_LIMITED, "Instagram limited API requests")
    if CHECKPOINT_TEXT.search(message):
        raise ProfileResolveError(CHECKPOINT, "Instagram asks to confirm the account")
    if status in {401, 403} or LOGIN_TEXT.search(message):
        raise ProfileResolveError(LOGIN_REQUIRED, "Instagram API needs a login")
    if status == 404:
        raise ProfileResolveError(NOT_FOUND, "profile not found")
    if not isinstance(status, int) or status >= 500:
        raise ProfileResolveError(NETWORK_ERROR, f"API status {status}")
    if status != 200:
        raise ProviderUnavailable(f"API status {status}")
    if not isinstance(body, dict):
        raise ProfileResolveError(PARSER_ERROR, "API answer is not JSON")
    if "data" in body and body.get("data") is not None and endpoint.user(body) is None:
        raise ProfileResolveError(NOT_FOUND, "profile not found")
    user = endpoint.user(body)
    if not isinstance(user, dict):
        raise ProfileResolveError(PARSER_ERROR, "API answer has no user object")
    profile = profile_from_fields(user, "api", max_captions=max_captions, strategy="api")
    if not profile.username:
        raise ProfileResolveError(PARSER_ERROR, "API user has no valid username")
    return profile


class InstagramApiProfileProvider:
    """Primary provider: one in-tab web API request per profile, no page load."""

    name = "api"

    def __init__(self, endpoint=None):
        self.endpoint = endpoint or WebProfileInfoEndpoint()

    def request(self, username: str, profile_url: str) -> dict:
        """Arguments for profile_api.js; the path comes from the endpoint adapter."""
        return {"username": username, "url": profile_url, **self.endpoint.request(username)}

    def get_profile(self, username: str, snapshot: dict, max_captions: int = 3) -> PartialProfile:
        profile = parse_instagram_profile_api_response(
            snapshot.get("api"), self.endpoint, max_captions
        )
        expected = normalize_instagram_username(username)
        if profile.username != expected:
            raise ProfileResolveError(
                PARSER_ERROR, f"API answered for @{profile.username}, expected @{expected}"
            )
        return profile
