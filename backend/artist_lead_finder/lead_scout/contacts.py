"""ContactExtractor: public e-mails and phone numbers of a profile, normalized and deduplicated.

Only the profile's own contact surface is read (bio, public/business contact fields,
visible header contact text, external link text); never comments or other posts.
Phones use libphonenumber (the `phonenumbers` port): E.164 when the country is known
from a "+" prefix or the profile's country code, otherwise a cleaned original string.
"""

import re
from dataclasses import dataclass, field

import phonenumbers

EMAIL = re.compile(
    r"(?<![\w.+-])([a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9_%+-])?)"
    r"@((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?![\w-])",
    re.I,
)
# "name [at] mail [dot] com" and "name(at)mail(dot)com" spellings.
OBFUSCATED_AT = re.compile(r"\s*[\[(]\s*(?:at|собака)\s*[\])]\s*", re.I)
OBFUSCATED_DOT = re.compile(r"\s*[\[(]\s*(?:dot|точка)\s*[\])]\s*", re.I)
# File names and asset hosts that look like addresses (image@2x.png).
FAKE_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "mov", "js", "css", "html"}
# A phone without "+" needs a phone keyword; bare digit runs are usually counters.
PHONE_AFTER_KEYWORD = re.compile(
    r"(?:phone|tel|call|text|whats\s?app|viber|telegram|телефон|тел|звони\w*|пиши\w*)"
    r"\.?\s*[:\-–]?\s*(\+?\d[\d\s().-]{6,18}\d)",
    re.I,
)
PLUS_PHONE = re.compile(r"\+\s?\d[\d\s().-]{6,18}\d")


@dataclass
class ContactSource:
    """Everything a profile publicly shows as contacts; built from a PartialProfile."""

    biography: str = ""
    public_emails: list[str] = field(default_factory=list)
    public_phones: list[str] = field(default_factory=list)
    phone_country_code: str | None = None
    contact_text: str = ""
    external_url: str = ""


@dataclass
class ContactResult:
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)


def build_contact_text(source: ContactSource) -> str:
    """Profile contact surface as one text: bio, contact fields, visible contact text."""
    parts = [
        source.biography,
        *source.public_emails,
        *source.public_phones,
        source.contact_text,
        source.external_url,
    ]
    return "\n".join(part.strip() for part in parts if part and part.strip())


def clean_email(value: str) -> str | None:
    match = EMAIL.search(value.strip().strip(".,;:!?)(<>\"'«»").lower())
    if not match:
        return None
    local, domain = match.group(1), match.group(2).rstrip(".")
    if ".." in local or domain.split(".")[-1] in FAKE_TLDS:
        return None
    return f"{local}@{domain}"


def extract_emails(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        text = OBFUSCATED_DOT.sub(".", OBFUSCATED_AT.sub("@", text or ""))
        for match in EMAIL.finditer(text):
            email = clean_email(match.group(0))
            if email and email not in found:
                found.append(email)
    return found[:10]


def _cleaned(raw: str) -> str | None:
    """Original number with only digits, spaces, +, brackets and dashes; 8-15 digits."""
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        return None
    return re.sub(r"\s+", " ", re.sub(r"[^\d+()\s-]", "", raw)).strip()


def normalize_phone(raw: str, country_code: str | None = None) -> str | None:
    """E.164 when the country is certain; the cleaned original otherwise; None if not a phone."""
    raw = (raw or "").strip()
    if not raw:
        return None
    candidate = raw
    if not raw.startswith("+") and country_code and str(country_code).isdigit():
        # The profile states its country code: the national number gets that prefix.
        national = re.sub(r"\D", "", raw).lstrip("0")
        candidate = f"+{country_code}{national}"
    if candidate.startswith("+"):
        try:
            number = phonenumbers.parse(candidate, None)
        except phonenumbers.NumberParseException:
            return None
        if not phonenumbers.is_possible_number(number):
            return None
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return _cleaned(raw)


def _key(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def extract_phones(*texts: str, country_code: str | None = None) -> list[str]:
    found: list[str] = []
    for text in texts:
        text = text or ""
        for raw in PLUS_PHONE.findall(text) + PHONE_AFTER_KEYWORD.findall(text):
            phone = normalize_phone(raw, country_code if not raw.strip().startswith("+") else None)
            if phone and _key(phone) not in {_key(known) for known in found}:
                found.append(phone)
    return found[:5]


class ContactExtractor:
    def extract(self, source: ContactSource) -> ContactResult:
        text = build_contact_text(source)
        emails = extract_emails(*source.public_emails, text)
        phones: list[str] = []
        # Public phone fields are phones by definition; bio text needs "+" or a keyword.
        for raw in source.public_phones:
            phone = normalize_phone(raw, source.phone_country_code)
            if phone and _key(phone) not in {_key(known) for known in phones}:
                phones.append(phone)
        for phone in extract_phones(text, country_code=source.phone_country_code):
            if _key(phone) not in {_key(known) for known in phones}:
                phones.append(phone)
        return ContactResult(emails=emails[:10], phones=phones[:5])
