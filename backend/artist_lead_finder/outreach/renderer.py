"""MessageTemplateRenderer: {{variable}} templates rendered for one lead.

`{{firstName}}` uses the built-in fallback when the lead has no value;
`{{firstName|friend}}` sets its own. Unknown variables are template errors, and a
rendered message never contains "undefined", "None" or a leftover placeholder.
"""

import re
import unicodedata
from dataclasses import dataclass, field

MAX_MESSAGE_LENGTH = 1000
MAX_TEMPLATE_LENGTH = 4000
VARIABLES = (
    "firstName",
    "fullName",
    "username",
    "artistName",
    "followers",
    "source",
    "profileType",
)
DEFAULT_FALLBACKS = {
    "firstName": "there",
    "fullName": "there",
    "artistName": "",  # replaced by the username
    "username": "",
    "followers": "",
    "source": "",
    "profileType": "artist",
}
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z]+)\s*(?:\|([^{}]*))?\}\}")
PROFILE_TYPES = {"artist": "artist", "producer": "producer", "media": "media", "other": "creator"}


@dataclass
class LeadVariables:
    username: str
    display_name: str = ""
    followers: int | None = None
    source_username: str | None = None
    profile_type: str | None = None


@dataclass
class RenderResult:
    text: str
    valid: bool
    errors: list[str] = field(default_factory=list)
    # Variables that had no value and used a fallback.
    fallbacks: list[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.text)


def clean_name(value: str) -> str:
    """Display name without emoji, symbols and separators: "Jay Carter 🎤 | Official"."""
    value = re.split(r"\s[|•·/–—-]\s", value or "")[0]
    kept = "".join(
        char
        for char in value
        if unicodedata.category(char)[0] in {"L", "M", "N"} or char in " '.&-"
    )
    return re.sub(r"\s+", " ", kept).strip(" .-&'")


def first_name(full_name: str) -> str:
    for word in full_name.split():
        word = word.strip(".-&'")
        if any(char.isalpha() for char in word):
            return word
    return ""


def compact_number(value: int) -> str:
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}M"
    if value >= 100_000:
        return f"{value // 1000}K"
    if value >= 1000:
        text = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{text}K"
    return str(value)


def lead_values(lead: LeadVariables) -> dict[str, str]:
    full = clean_name(lead.display_name)
    username = (lead.username or "").strip().lstrip("@")
    return {
        "firstName": first_name(full),
        "fullName": full,
        "username": username,
        "artistName": full or username,
        "followers": compact_number(lead.followers) if lead.followers else "",
        "source": f"@{lead.source_username}" if lead.source_username else "",
        "profileType": PROFILE_TYPES.get(lead.profile_type or "", ""),
    }


def template_errors(body: str) -> list[str]:
    errors = []
    if not body.strip():
        errors.append("Текст шаблона пустой.")
    if len(body) > MAX_TEMPLATE_LENGTH:
        errors.append(f"Шаблон длиннее {MAX_TEMPLATE_LENGTH} символов.")
    for match in PLACEHOLDER.finditer(body):
        if match.group(1) not in VARIABLES:
            errors.append(f"Неизвестная переменная {{{{{match.group(1)}}}}}.")
    leftover = PLACEHOLDER.sub("", body)
    if "{{" in leftover or "}}" in leftover:
        errors.append("Незакрытая переменная: проверьте фигурные скобки.")
    return errors


def tidy(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", line)
        # A variable that fell back to nothing leaves "Hey , ..." behind.
        line = re.sub(r" +([,.!?;:])", r"\1", line)
        line = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", line)
        lines.append(line.strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class MessageTemplateRenderer:
    def __init__(self, max_length: int = MAX_MESSAGE_LENGTH):
        self.max_length = max_length

    def render(self, template: str, lead: LeadVariables) -> RenderResult:
        errors = template_errors(template)
        values = lead_values(lead)
        fallbacks: list[str] = []

        def replace(match: re.Match) -> str:
            name, own = match.group(1), match.group(2)
            if name not in VARIABLES:
                return ""
            value = values[name]
            if value:
                return value
            fallbacks.append(name)
            if own is not None:
                return own.strip()
            if name == "artistName":
                return values["username"]
            return DEFAULT_FALLBACKS[name]

        text = tidy(PLACEHOLDER.sub(replace, template))
        if not text:
            errors.append("Сообщение пустое.")
        if len(text) > self.max_length:
            errors.append(f"Сообщение длиннее {self.max_length} символов ({len(text)}).")
        return RenderResult(text, not errors, errors, sorted(set(fallbacks)))
