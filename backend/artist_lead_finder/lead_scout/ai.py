"""Optional AI classification through OpenRouter; it only classifies, never saves leads."""

import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path

from ..browser_sessions import protect
from .classifier import CATEGORIES, ProfileText

log = logging.getLogger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
PROMPT = "\n".join(
    [
        "Classify this public Instagram profile for music lead scouting.",
        "",
        "Categories:",
        "",
        "artist = rapper, singer, musician, DJ, band or performer releasing or performing"
        " own music.",
        "",
        "producer = music producer, beatmaker or composer.",
        "",
        "media = music-focused blog, news outlet, promo page, record label, playlist curator,"
        " repost page or music-oriented creative/audio service.",
        "",
        "other = anything else.",
        "",
        "Return JSON only:",
        "",
        "{",
        '  "category": "artist|producer|media|other",',
        '  "confidence": 0-100',
        "}",
    ]
)


class AIUnavailable(Exception):
    """AI is not configured or the call failed; the local result stays in force."""


class AIKeyStore:
    """OpenRouter key encrypted with Windows DPAPI; never returned to the interface."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "openrouter-key.vault"

    def configured(self) -> bool:
        return self.path.exists()

    def save(self, key: str) -> None:
        key = key.strip()
        if not key:
            self.path.unlink(missing_ok=True)
            return
        if len(key) > 300 or not re.fullmatch(r"[\x21-\x7e]+", key):
            raise ValueError("Некорректный ключ OpenRouter.")
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protect(key.encode("utf-8")))
        temporary.replace(self.path)

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        return protect(self.path.read_bytes(), decrypt=True).decode("utf-8")


def profile_payload(profile: ProfileText) -> dict:
    return {
        "username": profile.username,
        "fullName": profile.full_name,
        "bio": profile.biography[:1500],
        "category": profile.category_name,
        "links": [profile.external_url, *profile.bio_links][:10],
        "captions": [caption[:300] for caption in profile.recent_captions[:6]],
    }


def parse_answer(content: str) -> tuple[str, int]:
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise AIUnavailable("AI вернул ответ не в формате JSON.")
    try:
        data = json.loads(match.group(0))
        category = str(data["category"]).lower()
        confidence = int(data["confidence"])
    except (ValueError, KeyError, TypeError):
        raise AIUnavailable("AI вернул неполный ответ.") from None
    if category not in CATEGORIES:
        raise AIUnavailable("AI вернул неизвестную категорию.")
    return category, max(0, min(100, confidence))


class OpenRouterClassifier:
    def __init__(self, keys: AIKeyStore, transport=None):
        self.keys = keys
        # Injectable for tests: (url, headers, body) -> response text.
        self.transport = transport or self._post

    @staticmethod
    def _post(url: str, headers: dict, body: bytes) -> str:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read(200_000).decode("utf-8")
        except urllib.error.HTTPError as error:
            raise AIUnavailable(f"OpenRouter ответил {error.code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise AIUnavailable("OpenRouter недоступен.") from None

    def classify(self, profile: ProfileText, model: str) -> tuple[str, int]:
        key = self.keys.load() if self.keys.configured() else None
        if not key:
            raise AIUnavailable("Ключ OpenRouter не задан.")
        body = json.dumps(
            {
                "model": model,
                "temperature": 0,
                "max_tokens": 60,
                "messages": [
                    {"role": "system", "content": PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(profile_payload(profile), ensure_ascii=False),
                    },
                ],
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "Artist Lead Finder",
        }
        text = self.transport(ENDPOINT, headers, body)
        try:
            content = json.loads(text)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise AIUnavailable("OpenRouter вернул неожиданный ответ.") from None
        return parse_answer(content or "")
