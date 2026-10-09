"""The emoji before a CRM status label, one rule for the app and the server's copy."""

MAX_EMOJI = 16


def clean_emoji(value) -> str | None:
    """The trimmed emoji, "" for none, None when it is not an emoji: letters, digits and
    spaces are not."""
    emoji = str(value or "").strip()
    if len(emoji) > MAX_EMOJI or any(char.isalnum() or char.isspace() for char in emoji):
        return None
    return emoji
