"""Any model behind an OpenAI-compatible chat completions API (OpenRouter, OpenAI, DeepSeek,
Gemini, Groq, a local Ollama or LM Studio) for the assistant.

The assistant keeps its conversation as Anthropic content blocks and calls
`client.messages.create(...)`. This client accepts the same call, translates the blocks and
tools to chat completions and translates the answer back, so the run loop, the stored history
and the tools stay the same whatever model answers. Thinking blocks of another provider are
dropped on the way out; they mean nothing to a different model.
"""

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from types import SimpleNamespace
from urllib.parse import urlparse

from ..account.client import tls_context
from ..errors import UserError

# (url, headers, body, timeout) -> response text; injectable for tests.
Transport = Callable[[str, dict, bytes, float], str]
STOP_REASONS = {
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class Block(dict):
    """A content block that reads like the SDK's objects (block.type) and stores as a dict."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def is_local(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")


def endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def host_name(base_url: str) -> str:
    return urlparse(base_url).hostname or base_url


def _text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(part for part in parts if part)


def to_chat(system, messages: list[dict]) -> list[dict]:
    """Anthropic system + messages → chat completions messages."""
    out: list[dict] = []
    system_text = _text(system) if not isinstance(system, str) else system
    if system_text:
        out.append({"role": "system", "content": system_text})
    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = _text(content)
            calls = [
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
            item: dict = {"role": "assistant", "content": text or None}
            if calls:
                item["tool_calls"] = calls
            if text or calls:
                out.append(item)
            continue
        # A user turn: tool results answer the calls first, then any text.
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                body = block.get("content")
                body = body if isinstance(body, str) else _text(body)
                if block.get("is_error"):
                    body = f"Ошибка: {body}"
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": body})
        text = _text(content)
        if text:
            out.append({"role": "user", "content": text})
    return out


def to_functions(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def from_chat(data: dict, name: str) -> SimpleNamespace:
    """A chat completions answer → an object shaped like an Anthropic Message."""
    try:
        choice = data["choices"][0]
        message = choice.get("message") or {}
    except (KeyError, IndexError, TypeError):
        error = data.get("error") if isinstance(data, dict) else None
        detail = error.get("message") if isinstance(error, dict) else ""
        raise UserError(f"{name} вернул неожиданный ответ. {detail}".strip()) from None
    content: list[Block] = []
    text = message.get("content")
    if isinstance(text, list):
        text = _text(text)
    if text:
        content.append(Block(type="text", text=str(text)))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        content.append(
            Block(
                type="tool_use",
                id=str(call.get("id") or f"call_{len(content)}"),
                name=str(function.get("name") or ""),
                input=arguments if isinstance(arguments, dict) else {},
            )
        )
    reason = choice.get("finish_reason") or "stop"
    stop = STOP_REASONS.get(reason, "end_turn")
    if stop == "end_turn" and any(block["type"] == "tool_use" for block in content):
        stop = "tool_use"
    usage = data.get("usage") or {}
    return SimpleNamespace(
        content=content,
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def http_post(url: str, headers: dict, body: bytes, timeout: float) -> str:
    name = host_name(url)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    context = None if url.startswith("http://") else tls_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.read(4_000_000).decode("utf-8")
    except urllib.error.HTTPError as error:
        try:
            said = json.loads(error.read(20_000) or b"{}")
            detail = said.get("error", said)
            detail = detail.get("message") if isinstance(detail, dict) else str(detail)
        except (OSError, ValueError, AttributeError):
            detail = ""
        hints = {
            401: "ключ не подошёл",
            402: "на счёте закончились деньги",
            403: "нет доступа к этой модели",
            404: "нет такой модели или адреса",
            429: "слишком много запросов, повторите через минуту",
        }
        hint = hints.get(error.code, "ошибка сервера" if error.code >= 500 else "запрос отклонён")
        text = f"{name}: {hint} ({error.code})"
        raise UserError(f"{text}. {str(detail or '')[:300]}".strip()) from None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError):
        raise UserError(f"Нет связи с {name}. Проверьте адрес и интернет.") from None


class CompatClient:
    """The slice of the Anthropic client the assistant uses, over chat completions."""

    def __init__(self, base_url: str, key: str, transport: Transport | None = None):
        self.url = endpoint(base_url)
        self.key = key
        self.transport = transport or http_post
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict],
        system="",
        tools=None,
        **_anthropic_only,
    ) -> SimpleNamespace:
        request: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": to_chat(system, messages),
        }
        if tools:
            request["tools"] = to_functions(tools)
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        if "openrouter.ai" in self.url:
            headers["X-Title"] = "Artist Lead Finder"
        body = json.dumps(request, ensure_ascii=False).encode("utf-8")
        text = self.transport(self.url, headers, body, 180)
        try:
            data = json.loads(text)
        except ValueError:
            raise UserError(f"{host_name(self.url)} вернул не JSON.") from None
        return from_chat(data, host_name(self.url))
