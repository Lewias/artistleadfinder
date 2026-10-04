"""Optional AI classification through OpenRouter; it only classifies, never saves leads.

The raw model answer is never trusted: it is validated against a strict schema, one
repair request is allowed, and every failure leaves the local result in force.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..browser_sessions import protect
from ..errors import UserError
from .classification.model import (
    AIClassificationError,
    AIClassificationResult,
    AIInvalidOutput,
    AITransientError,
    LocalClassificationResult,
    ProfileText,
)

log = logging.getLogger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
PROMPT = """Classify one public Instagram profile for music lead scouting.

Return ONLY valid minified JSON.

Allowed categories:
artist
producer
media
other

Definitions:

artist = rapper, singer, songwriter, musician, DJ, band, vocalist, performer or person releasing/performing their own music.

producer = music producer, beatmaker or composer.

media = explicitly music-focused blog, news outlet, magazine, promo page, record label, playlist curator, repost hub, music video/photo/audio service, mixing/mastering studio or similar service focused on musicians.

other = anything else.

Important rules:

- Prefer artist when there is clear evidence the person releases their own music.
- Streaming links plus release wording strongly favor artist.
- Beat marketplace links and producer wording strongly favor producer.
- Generic photographers/videographers are not media unless clearly music-focused.
- Generic radio/community/event pages without clear music lead relevance can be other.
- "artist" alone is ambiguous and does not automatically mean musician.
- The local classifier hint may be wrong: check the profile independently.

Return:

{"category":"artist|producer|media|other","confidence":0-100}

No markdown.
No explanation.
No extra keys."""  # noqa: E501
REPAIR = (
    "Your previous answer was not valid. Reply with ONLY minified JSON exactly like "
    '{"category":"artist","confidence":80} - category one of artist, producer, media, '
    "other; confidence an integer 0-100; no other keys, no markdown."
)
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


class AIAnswer(BaseModel):
    """Schema of the model answer (the role Zod plays in a TypeScript app)."""

    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal["artist", "producer", "media", "other"]
    confidence: int = Field(ge=0, le=100)


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
            raise UserError("Некорректный ключ OpenRouter.")
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protect(key.encode("utf-8")))
        temporary.replace(self.path)

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        return protect(self.path.read_bytes(), decrypt=True).decode("utf-8")


def profile_payload(profile: ProfileText, local: LocalClassificationResult | None = None) -> dict:
    """Minimal AI input: public profile fields, plus the local result as a hint."""
    payload = {
        "username": profile.username,
        "fullName": profile.full_name,
        "biography": profile.biography[:1500],
        "categoryName": profile.category_name,
        "externalUrl": profile.external_url,
        "bioLinks": profile.bio_links[:10],
        "recentCaptions": [caption[:300] for caption in profile.recent_captions[:6]],
    }
    if local is not None:
        payload["localHint"] = {
            "category": local.category,
            "confidence": local.confidence,
            "reasons": local.reasons[:8],
        }
    return payload


def parse_answer(content: str) -> AIAnswer:
    """The whole answer must be one JSON object of the schema (code fences tolerated)."""
    text = FENCE.sub("", (content or "").strip())
    try:
        return AIAnswer.model_validate(json.loads(text))
    except (ValueError, ValidationError):
        raise AIInvalidOutput("AI вернул ответ не по схеме.") from None


class OpenRouterClassifier:
    """AIProfileClassifier over OpenRouter chat completions."""

    def __init__(self, keys: AIKeyStore, transport=None):
        self.keys = keys
        # Injectable for tests: (url, headers, body, timeout) -> response text.
        self.transport = transport or self._post

    @staticmethod
    def _post(url: str, headers: dict, body: bytes, timeout: float) -> str:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(200_000).decode("utf-8")
        except urllib.error.HTTPError as error:
            message = f"OpenRouter ответил {error.code}."
            # Rate limits and server errors pass; bad key / no credits do not.
            if error.code == 429 or error.code >= 500:
                raise AITransientError(message) from None
            raise AIClassificationError(message) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise AITransientError("OpenRouter недоступен.") from None

    def classify(
        self,
        profile: ProfileText,
        local: LocalClassificationResult | None = None,
        *,
        model: str,
        timeout: float = 15,
    ) -> AIClassificationResult:
        key = self.keys.load() if self.keys.configured() else None
        if not key:
            raise AIClassificationError("Ключ OpenRouter не задан.")
        messages = [
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": json.dumps(profile_payload(profile, local), ensure_ascii=False),
            },
        ]
        content = self._complete(key, model, messages, timeout)
        try:
            answer = parse_answer(content)
        except AIInvalidOutput:
            # One repair attempt; a second invalid answer fails the AI step.
            messages += [
                {"role": "assistant", "content": content[:500]},
                {"role": "user", "content": REPAIR},
            ]
            answer = parse_answer(self._complete(key, model, messages, timeout))
        return AIClassificationResult(answer.category, answer.confidence, model)

    def _complete(self, key: str, model: str, messages: list[dict], timeout: float) -> str:
        body = json.dumps(
            {"model": model, "temperature": 0, "max_tokens": 60, "messages": messages}
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "Artist Lead Finder",
        }
        text = self.transport(ENDPOINT, headers, body, timeout)
        try:
            content = json.loads(text)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise AITransientError("OpenRouter вернул неожиданный ответ.") from None
        return content or ""
