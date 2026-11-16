"""Optional shared provider supplied privately at packaging time.

No credential lives in tracked source or crosses the settings/state API. A client
binary is not a secret store: a distributor must treat its bundled key as extractable.
"""

GO_URL = "https://opencode.ai/inference/go/openai/v1"
GO_MODEL = "claude-haiku-5-5"


def shared_key() -> str:
    try:
        from alf_bundled_provider import OPENCODE_KEY
    except ImportError:
        return ""
    return OPENCODE_KEY if isinstance(OPENCODE_KEY, str) else ""


def key_for(config: dict) -> str:
    base = str(config.get("base_url") or "").rstrip("/")
    if config.get("provider") == "compatible" and base in (GO_URL, GO_URL + "/chat/completions"):
        return shared_key()
    return ""
