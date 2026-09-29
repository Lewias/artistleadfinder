"""resolve_instagram_user_id: a numeric Instagram id from data the application already has.

Order: API/profile response -> an existing lookup (cache or CRM) -> browser structured
data -> None. The id is never guessed or derived from the username.
"""

from collections.abc import Callable, Iterable

from .model import PartialProfile


def _valid(value) -> str | None:
    text = str(value or "").strip()
    return text if text.isdigit() and 1 <= len(text) <= 30 else None


def resolve_instagram_user_id(
    username: str,
    api: PartialProfile | None = None,
    lookups: Iterable[Callable[[str], str | None]] = (),
    browser: PartialProfile | None = None,
) -> str | None:
    if api is not None and api.username in (None, username):
        if found := _valid(api.id):
            return found
    for lookup in lookups:
        if found := _valid(lookup(username)):
            return found
    if browser is not None and browser.username in (None, username):
        if found := _valid(browser.id):
            return found
    return None
