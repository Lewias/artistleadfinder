"""Recipients: numbers in international form (stored as E.164) or iMessage emails."""

import re

import phonenumbers

MAX_INPUT_LENGTH = 32
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,185}\.[^@\s.]{2,}$")


def normalize_phone(value: object) -> str | None:
    """+1 555 555-0123 -> +15555550123; None for anything without a country code."""
    text = str(value).strip()
    if text.startswith("00"):
        text = "+" + text[2:]
    if not text.startswith("+") or len(text) > MAX_INPUT_LENGTH:
        return None
    try:
        number = phonenumbers.parse(text, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_recipient(value: object) -> str | None:
    """A phone number (E.164) or an email (lower case); None for anything else."""
    text = str(value).strip()
    if "@" in text:
        return text.lower() if len(text) <= 254 and EMAIL.match(text) else None
    return normalize_phone(text)
