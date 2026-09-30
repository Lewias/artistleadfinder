"""Typing an outreach message into the thread's message box with keyboard events.

Instagram's box is a React contenteditable: text set directly (value, textContent, the
clipboard) can leave its state out of step with what is shown. Each character therefore
goes in as its own keyboard event, a line break as Shift+Enter (Enter alone sends).

The typer never presses the final Enter: the sender verifies the box and sends. Only
lengths are logged, never the message text.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("artist_lead_finder.outreach")

READ_INPUT = """el => (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')
  ? el.value : (el.innerText ?? el.textContent ?? '')"""


@dataclass(frozen=True)
class MessageTypingOptions:
    # Fixed pause between keyboard events, so the editor handles each one in turn.
    delay_ms: int = 10
    # One more full attempt, after clearing the box, when the result does not match.
    retries: int = 1


class MessageTypingMismatch(Exception):
    """The box does not hold the message. Nothing was sent."""

    code = "MESSAGE_TYPING_MISMATCH"

    def __init__(self, expected_length: int, actual_length: int):
        super().__init__(self.code)
        self.expected_length = expected_length
        self.actual_length = actual_length


class MessageTypingCancelled(Exception):
    """Stopped between characters; the partial text was cleared and not sent."""


def normalize_message_text(text: str) -> str:
    """The message as it should be typed: CRLF / CR line breaks become LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def comparable(text: str) -> str:
    """Box text and message compared alike: NBSP as a space, no trailing spaces on a
    line, one break between lines (editors render a break as a new paragraph), no
    surrounding blank space."""
    text = normalize_message_text(text).replace(" ", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def type_character(page, char: str) -> None:
    page.keyboard.type(char)


def type_newline(page) -> None:
    page.keyboard.down("Shift")
    try:
        page.keyboard.press("Enter")
    finally:
        page.keyboard.up("Shift")


def clear_input(page, input) -> None:
    input.click(timeout=5000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")


def read_message_input_text(input) -> str:
    return str(input.evaluate(READ_INPUT) or "")


def verify_typed_message(expected: str, actual: str) -> None:
    if comparable(actual) != comparable(expected):
        raise MessageTypingMismatch(len(expected), len(normalize_message_text(actual)))


class MessageTyper:
    def __init__(self, sleep: Callable[[object, int], None] | None = None):
        # Pauses go through the page so browser events keep being handled.
        self.sleep = sleep or (lambda page, ms: page.wait_for_timeout(ms))

    def type_message(
        self,
        page,
        input,
        text: str,
        options: MessageTypingOptions | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        options = options or MessageTypingOptions()
        message = normalize_message_text(text)
        if not message.strip():
            raise ValueError("Empty message")
        if not input.is_visible() or not input.is_editable():
            raise MessageTypingMismatch(len(message), 0)
        for attempt in range(options.retries + 1):
            clear_input(page, input)
            log.debug("message_typer_started", extra={"characters": len(message)})
            self._type(page, input, message, options, cancelled)
            try:
                verify_typed_message(message, read_message_input_text(input))
            except MessageTypingMismatch as error:
                log.info(
                    "message_typer_mismatch",
                    extra={
                        "expected_length": error.expected_length,
                        "actual_length": error.actual_length,
                    },
                )
                clear_input(page, input)
                if attempt == options.retries:
                    raise
                continue
            log.debug("message_typer_verified", extra={"characters": len(message)})
            return

    def _type(self, page, input, message, options, cancelled) -> None:
        # Array.from-like: one code point at a time, so emoji are never split in half.
        for char in message:
            if cancelled():
                clear_input(page, input)
                raise MessageTypingCancelled()
            if char == "\n":
                type_newline(page)
            else:
                type_character(page, char)
            if options.delay_ms:
                self.sleep(page, options.delay_ms)
