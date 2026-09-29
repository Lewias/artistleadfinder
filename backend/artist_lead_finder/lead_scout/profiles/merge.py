"""Merge API and browser partials; decide when the browser fallback is worth a page load."""

from dataclasses import dataclass, field

from .model import PartialProfile

# Fields whose absence may justify opening the profile page.
CRITICAL_FIELDS = ("biography", "followers_count", "external_url", "category_name")
NUMERIC_FIELDS = ("followers_count", "following_count", "posts_count")
TEXT_FIELDS = ("full_name", "biography", "external_url", "category_name", "phone_country_code")
FLAG_FIELDS = ("is_business", "is_private")
LIST_FIELDS = ("emails", "phones", "bio_links", "recent_captions")


def missing_fields(profile: PartialProfile | None) -> list[str]:
    """Critical fields the source did not answer (None); an empty answer counts as known."""
    if profile is None:
        return list(CRITICAL_FIELDS)
    return [name for name in CRITICAL_FIELDS if getattr(profile, name) is None]


def is_profile_data_sufficient(profile: PartialProfile | None, max_missing: int = 1) -> bool:
    """Enough to classify without a page load: identity, followers, and at most one gap.

    External link and category are often legitimately empty; the API reports that as ""
    (known), so a normal API answer never triggers the browser.
    """
    if profile is None or not profile.username or profile.followers_count is None:
        return False
    return len(missing_fields(profile)) <= max_missing


@dataclass
class MergeResult:
    profile: PartialProfile
    source: str
    anomalies: list[str] = field(default_factory=list)


def _dedupe(values) -> list:
    seen, result = set(), []
    for value in values:
        key = value.strip().lower() if isinstance(value, str) else value
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def merge_profile_data(api: PartialProfile | None, browser: PartialProfile | None) -> MergeResult:
    """API is primary for structured and numeric data; the browser fills what is missing.

    A good value is never replaced by an empty one; lists are unioned without duplicates;
    conflicting username or id are reported as anomalies (the API value wins).
    """
    if api is None and browser is None:
        raise ValueError("Nothing to merge")
    if api is None or browser is None:
        only = api or browser
        return MergeResult(only, only.source)
    anomalies = []
    for name in ("username", "id"):
        first, second = getattr(api, name), getattr(browser, name)
        if first and second and first != second:
            anomalies.append(f"{name} differs: api={first} browser={second}")
    merged = PartialProfile(
        source="api",
        username=api.username or browser.username,
        id=api.id or browser.id,
        contact_text=browser.contact_text or api.contact_text,
        strategies={**browser.strategies, **api.strategies},
    )
    for name in NUMERIC_FIELDS:
        value = getattr(api, name)
        setattr(merged, name, value if value is not None else getattr(browser, name))
    for name in TEXT_FIELDS:
        first, second = getattr(api, name), getattr(browser, name)
        # Non-empty beats empty; an explicit empty answer beats "unknown".
        value = first or second or (first if first is not None else second)
        setattr(merged, name, value)
        if not first and second:
            merged.strategies[name] = browser.strategies.get(name, "browser")
    for name in FLAG_FIELDS:
        first, second = getattr(api, name), getattr(browser, name)
        setattr(merged, name, first if first is not None else second)
    for name in LIST_FIELDS:
        setattr(merged, name, _dedupe([*getattr(api, name), *getattr(browser, name)]))
    external = (merged.external_url or "").rstrip("/")
    merged.bio_links = [link for link in merged.bio_links if link.rstrip("/") != external]
    return MergeResult(merged, "merged", anomalies)
