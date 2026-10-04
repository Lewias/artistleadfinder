"""Canonical identities and safe text; independent from storage."""

import re
import unicodedata
from datetime import timezone

from .errors import UserError
from .providers import Candidate


def clean_text(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", value)
        if unicodedata.category(char) != "Cc" or char in "\n\t"
    ).strip()


def normalize(candidate: Candidate) -> Candidate:
    data = candidate.model_dump()
    data["platform"] = clean_text(candidate.platform).casefold()
    data["username"] = clean_text(candidate.username).lstrip("@").casefold()
    if not re.fullmatch(r"[\w.\-]+", data["username"], flags=re.UNICODE):
        raise UserError("Некорректное имя профиля.")
    data["platform_user_id"] = (candidate.platform_user_id or "").strip() or None
    for key in ("bio", "display_name"):
        data[key] = clean_text(data[key])
    if candidate.last_activity_at:
        dt = candidate.last_activity_at
        data["last_activity_at"] = (
            dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        )
    data["recent_content"] = [clean_text(value) for value in candidate.recent_content]
    return Candidate.model_validate(data)
