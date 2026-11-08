"""Optional AI classification through OpenRouter or Cloudflare Workers AI; it only
classifies, never saves leads. A Cloudflare model id ("@cf/...") goes to Cloudflare.

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
CLOUDFLARE_ENDPOINT = (
    "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions"
)
CLOUDFLARE_ACCOUNT = re.compile(r"[0-9a-f]{32}")
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


class CloudflareKeys:
    """Cloudflare account id and Workers AI token, encrypted with Windows DPAPI. The token
    is never returned to the interface; the account id is shown to recognize the account."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "cloudflare-ai.vault"

    def configured(self) -> bool:
        return self.path.exists()

    def save(self, account: str, token: str) -> None:
        account, token = account.strip().lower(), token.strip()
        if not account and not token:
            self.path.unlink(missing_ok=True)
            return
        if not CLOUDFLARE_ACCOUNT.fullmatch(account):
            raise UserError("Account ID Cloudflare — это 32 символа: цифры и буквы a–f.")
        if len(token) > 300 or not re.fullmatch(r"[\x21-\x7e]+", token):
            raise UserError("Некорректный токен Cloudflare.")
        data = json.dumps({"account": account, "token": token}).encode("utf-8")
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protect(data))
        temporary.replace(self.path)

    def load(self) -> dict | None:
        """{"account": ..., "token": ...} or None."""
        if not self.path.exists():
            return None
        return json.loads(protect(self.path.read_bytes(), decrypt=True).decode("utf-8"))

    def account(self) -> str | None:
        try:
            return (self.load() or {}).get("account")
        except (OSError, ValueError):
            return None


def is_cloudflare(model: str) -> bool:
    return model.startswith(("@cf/", "@hf/"))


def error_text(body: bytes) -> str:
    """The provider's own words about a refused request, short. OpenRouter sends
    {"error": {"message", "metadata": {"raw"}}}, Cloudflare {"errors": [{"message"}]}."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    parts = []
    error = data.get("error")
    if isinstance(error, dict):
        parts.append(error.get("message"))
        metadata = error.get("metadata")
        if isinstance(metadata, dict):
            parts.append(metadata.get("raw"))
    elif isinstance(error, str):
        parts.append(error)
    errors = data.get("errors")
    for item in errors if isinstance(errors, list) else []:
        if isinstance(item, dict):
            parts.append(item.get("message"))
    words = " — ".join(str(part) for part in parts if part)
    return " ".join(words.split())[:160]


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
    """AIProfileClassifier over OpenAI-style chat completions: OpenRouter, or Cloudflare
    Workers AI for a "@cf/..." model."""

    def __init__(self, keys: AIKeyStore, transport=None, cloudflare: CloudflareKeys | None = None):
        self.keys = keys
        self.cloudflare = cloudflare
        # Injectable for tests: (url, headers, body, timeout) -> response text.
        self.transport = transport or self._post

    @staticmethod
    def _post(url: str, headers: dict, body: bytes, timeout: float) -> str:
        name = provider_name(url)
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(200_000).decode("utf-8")
        except urllib.error.HTTPError as error:
            try:
                said = error_text(error.read(20_000))
            except OSError:
                said = ""
            message = f"{name} ответил {error.code}" + (f": {said}" if said else ".")
            # Rate limits and server errors pass; bad key / no credits do not.
            if error.code == 429 or error.code >= 500:
                raise AITransientError(message) from None
            raise AIClassificationError(message) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise AITransientError(f"{name} недоступен.") from None

    def _target(self, model: str) -> tuple[str, str]:
        """(url, token) for the model."""
        if is_cloudflare(model):
            keys = None
            if self.cloudflare is not None and self.cloudflare.configured():
                keys = self.cloudflare.load()
            if not keys:
                raise AIClassificationError("Cloudflare не настроен: нет Account ID и токена.")
            return CLOUDFLARE_ENDPOINT.format(account=keys["account"]), keys["token"]
        key = self.keys.load() if self.keys.configured() else None
        if not key:
            raise AIClassificationError("Ключ OpenRouter не задан.")
        return ENDPOINT, key

    def classify(
        self,
        profile: ProfileText,
        local: LocalClassificationResult | None = None,
        *,
        model: str,
        timeout: float = 15,
    ) -> AIClassificationResult:
        url, key = self._target(model)
        messages = [
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": json.dumps(profile_payload(profile, local), ensure_ascii=False),
            },
        ]
        content = self._complete(url, key, model, messages, timeout)
        try:
            answer = parse_answer(content)
        except AIInvalidOutput:
            # One repair attempt; a second invalid answer fails the AI step.
            messages += [
                {"role": "assistant", "content": content[:500]},
                {"role": "user", "content": REPAIR},
            ]
            answer = parse_answer(self._complete(url, key, model, messages, timeout))
        return AIClassificationResult(answer.category, answer.confidence, model)

    def _complete(self, url: str, key: str, model: str, messages: list[dict], timeout: float):
        request = {"model": model, "temperature": 0, "max_tokens": 60, "messages": messages}
        if url != ENDPOINT:
            # Workers AI runs Gemma with thinking on: it spends the whole answer budget
            # thinking and returns empty content. The answer here is one short JSON.
            request["chat_template_kwargs"] = {"enable_thinking": False}
        body = json.dumps(request).encode("utf-8")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if url == ENDPOINT:
            headers["X-Title"] = "Artist Lead Finder"
        text = self.transport(url, headers, body, timeout)
        try:
            content = json.loads(text)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise AITransientError(f"{provider_name(url)} вернул неожиданный ответ.") from None
        return content or ""


def provider_name(url: str) -> str:
    return "Cloudflare" if url.startswith("https://api.cloudflare.com/") else "OpenRouter"
