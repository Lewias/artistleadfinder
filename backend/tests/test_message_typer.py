import pytest

from artist_lead_finder.message_typer import (
    MessageTyper,
    MessageTypingCancelled,
    MessageTypingMismatch,
    MessageTypingOptions,
    comparable,
    normalize_message_text,
    verify_typed_message,
)


class Keyboard:
    """Records keyboard calls and edits a plain text box the way an editor would."""

    def __init__(self, box, drop: str = ""):
        self.box, self.drop, self.calls, self.shift = box, drop, [], False

    def type(self, text):
        self.calls.append(("type", text))
        if text not in self.drop:
            self.box.text += text

    def down(self, key):
        self.calls.append(("down", key))
        self.shift = self.shift or key == "Shift"

    def up(self, key):
        self.calls.append(("up", key))
        if key == "Shift":
            self.shift = False

    def press(self, key):
        self.calls.append(("press", key))
        if key == "Enter" and self.shift:
            self.box.text += "\n"
        elif key == "Enter":
            self.box.sent = True
        elif key == "Delete":
            self.box.text = ""


class Box:
    def __init__(self, text=""):
        self.text, self.sent, self.visible = text, False, True

    def is_visible(self):
        return self.visible

    def is_editable(self):
        return True

    def click(self, timeout=None):
        pass

    def evaluate(self, script):
        return self.text


class Page:
    def __init__(self, box, drop=""):
        self.keyboard = Keyboard(box, drop)


def typed(text, box=None, drop="", **options):
    box = box or Box()
    page = Page(box, drop)
    MessageTyper(sleep=lambda page, ms: None).type_message(
        page, box, text, MessageTypingOptions(**options)
    )
    return page.keyboard.calls, box


def keys(calls):
    return [call for call in calls if call != ("press", "Control+A") and call[1] != "Delete"]


def test_each_character_is_its_own_key_event_and_newline_is_shift_enter():
    calls, box = typed("Hi\nthere")
    assert keys(calls) == [
        ("type", "H"),
        ("type", "i"),
        ("down", "Shift"),
        ("press", "Enter"),
        ("up", "Shift"),
        *[("type", char) for char in "there"],
    ]
    assert box.text == "Hi\nthere" and not box.sent
    # Enter is pressed only with Shift held: the typer never sends.
    enters = [index for index, call in enumerate(calls) if call == ("press", "Enter")]
    assert all(calls[index - 1] == ("down", "Shift") for index in enters)


def test_crlf_is_one_line_break():
    assert normalize_message_text("a\r\nb\rc") == "a\nb\nc"
    calls, box = typed("a\r\nb")
    assert box.text == "a\nb" and keys(calls).count(("press", "Enter")) == 1


def test_unicode_and_emoji_are_typed_whole():
    calls, box = typed("Привет 🔥🤝 ok")
    assert ("type", "🔥") in calls and ("type", "🤝") in calls
    assert all(len(text) == 1 for kind, text in keys(calls) if kind == "type")
    assert box.text == "Привет 🔥🤝 ok"


def test_spaces_and_punctuation_are_typed_as_they_are():
    calls, box = typed("Yo, gang!  Whats ur #?")
    assert [text for kind, text in keys(calls) if kind == "type"] == list("Yo, gang!  Whats ur #?")


def test_multiline_message():
    _, box = typed("line one\n\nline three")
    assert box.text == "line one\n\nline three"


def test_empty_message_is_refused():
    with pytest.raises(ValueError):
        typed("   \n ")


def test_a_leftover_draft_is_cleared_first():
    _, box = typed("new", box=Box("old draft"))
    assert box.text == "new"


def test_verification_normalizes_only_rendering_differences():
    assert comparable("a b \r\n\nc\n") == comparable("a b\nc")
    verify_typed_message("a b\nc", "a b\n\nc\n")
    with pytest.raises(MessageTypingMismatch) as error:
        verify_typed_message("hello", "helo")
    assert (error.value.expected_length, error.value.actual_length) == (5, 4)


def test_mismatch_retries_once_then_fails_with_the_box_cleared():
    box = Box()
    with pytest.raises(MessageTypingMismatch) as error:
        typed("hey", box=box, drop="y")
    assert error.value.code == "MESSAGE_TYPING_MISMATCH"
    assert (error.value.expected_length, error.value.actual_length) == (3, 2)
    assert box.text == "" and not box.sent


def test_cancel_between_characters_clears_the_partial_text():
    box = Box()
    page = Page(box)
    left = iter([False, False, True])
    with pytest.raises(MessageTypingCancelled):
        MessageTyper(sleep=lambda page, ms: None).type_message(
            page, box, "hello", cancelled=lambda: next(left)
        )
    assert box.text == "" and not box.sent


def test_the_pause_between_characters_is_fixed():
    box, pauses = Box(), []
    MessageTyper(sleep=lambda page, ms: pauses.append(ms)).type_message(
        Page(box), box, "a, b.", MessageTypingOptions(delay_ms=12)
    )
    assert pauses == [12] * 5
