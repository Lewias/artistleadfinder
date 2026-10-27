"""Sends one text through the Messages app on this Mac (AppleScript via `osascript`).

The recipient and text are passed as script arguments, never pasted into the script, so a
message cannot change what the script does. macOS asks once for permission to control
Messages (Privacy & Security → Automation; the reason shown is NSAppleEventsUsageDescription
in src-tauri/Info.plist).
"""

import subprocess
import sys

TIMEOUT_SECONDS = 30

SCRIPT = """
on run argv
    set theRecipient to item 1 of argv
    set theText to item 2 of argv
    tell application "Messages"
        set targetAccount to 1st account whose service type = iMessage
        send theText to participant theRecipient of targetAccount
    end tell
end run
"""

NOT_ALLOWED = (
    "macOS не разрешил приложению управлять Сообщениями. Откройте «Системные настройки» → "
    "«Конфиденциальность и безопасность» → «Автоматизация» и включите для Artist Lead Finder "
    "пункт «Сообщения»."
)


class SendFailed(Exception):
    """Messages refused or the script failed: nothing was sent."""


class SendUnknown(Exception):
    """No answer from Messages in time: the message may or may not have gone out."""


class MessagesTransport:
    def __init__(self, enabled: bool | None = None):
        self.enabled = sys.platform == "darwin" if enabled is None else enabled

    def describe(self) -> str:
        if not self.enabled:
            return "Отправка через Сообщения работает только в приложении на Mac."
        return (
            "Черновик уходит через Сообщения на этом Mac после того, как вы подтвердили "
            "получателя и текст. Статус берётся из базы Сообщений: отправлено, доставлено "
            "или ошибка."
        )

    def send(self, recipient: str, text: str) -> None:
        try:
            # Fixed binary and script; recipient and text are plain arguments.
            result = subprocess.run(  # noqa: S603
                ["/usr/bin/osascript", "-", recipient, text],
                input=SCRIPT,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SendUnknown(f"Сообщения не ответили за {TIMEOUT_SECONDS} секунд.") from None
        except OSError as error:
            raise SendFailed(f"Не удалось запустить osascript ({type(error).__name__}).") from None
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            if "-1743" in detail or "not authorized" in detail.lower():
                raise SendFailed(NOT_ALLOWED)
            raise SendFailed(f"Сообщения не отправили сообщение: {detail[-300:] or 'без описания'}")
