"""Phones and emails in the text of a reply.

Phones are matched by `phonenumbers` and kept only when valid for their country; a number
written without a country code takes the default region and is marked as guessed, so the
review screen shows it. Dates, sums and zip codes are not valid numbers and are skipped.
"""

import re
from dataclasses import dataclass

import phonenumbers

EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,}"
)
MAX_TEXT = 4000
SNIPPET = 160


@dataclass(frozen=True)
class Found:
    kind: str  # "phone" or "email"
    value: str  # E.164 phone or a lowercased email
    raw: str
    guessed: bool  # the country code came from the default region
    snippet: str


def snippet(text: str, start: int, end: int) -> str:
    """The words around a match, on one line."""
    left = max(0, start - 60)
    right = min(len(text), end + 60)
    piece = " ".join(text[left:right].split())
    return (("…" if left else "") + piece + ("…" if right < len(text) else ""))[:SNIPPET]


def find_contacts(text: str, region: str = "US") -> list[Found]:
    text = (text or "")[:MAX_TEXT]
    found: list[Found] = []
    seen: set[tuple[str, str]] = set()
    emails = list(EMAIL.finditer(text))
    for match in emails:
        value = match.group(0).lower()
        if ("email", value) not in seen:
            seen.add(("email", value))
            found.append(Found("email", value, match.group(0), False, snippet(text, *match.span())))
    # Digits inside an email are never a phone.
    masked = text
    for match in emails:
        masked = masked[: match.start()] + " " * len(match.group(0)) + masked[match.end() :]
    for match in phonenumbers.PhoneNumberMatcher(masked, region, phonenumbers.Leniency.VALID):
        value = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        if ("phone", value) in seen:
            continue
        seen.add(("phone", value))
        raw = match.raw_string.strip()
        guessed = not raw.startswith(("+", "00"))
        found.append(Found("phone", value, raw, guessed, snippet(text, match.start, match.end)))
    return found
