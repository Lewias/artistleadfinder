"""Public contact extraction: e-mails and phone numbers, normalised and de-duplicated."""

import re

EMAIL = re.compile(
    r"(?<![\w.+-])[a-zA-Z0-9._%+-]{1,64}@[a-zA-Z0-9-]{1,63}(?:\.[a-zA-Z0-9-]{1,63})*\.[a-zA-Z]{2,24}"
)
# A phone needs a leading "+" or a phone keyword nearby; bare digit runs are usually counters.
PHONE_WITH_PLUS = re.compile(r"\+\s?\d[\d\s().-]{6,18}\d")
PHONE_AFTER_KEYWORD = re.compile(
    r"(?:phone|tel|call|text|whats\s?app|телефон|тел|звони\w*)\.?\s*[:\-–]?\s*(\+?\d[\d\s().-]{6,18}\d)",
    re.I,
)


def extract_emails(*texts: str) -> list[str]:
    found = []
    for text in texts:
        for match in EMAIL.findall(text or ""):
            email = match.lower().rstrip(".")
            if email not in found:
                found.append(email)
    return found[:10]


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        return None
    return ("+" if raw.strip().startswith("+") else "") + digits


def extract_phones(*texts: str) -> list[str]:
    found = []
    for text in texts:
        text = text or ""
        candidates = PHONE_WITH_PLUS.findall(text) + PHONE_AFTER_KEYWORD.findall(text)
        for raw in candidates:
            phone = normalize_phone(raw)
            if phone and phone.lstrip("+") not in [known.lstrip("+") for known in found]:
                found.append(phone)
    return found[:5]
