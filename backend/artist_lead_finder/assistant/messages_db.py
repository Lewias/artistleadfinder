"""The Mac's Messages history (`~/Library/Messages/chat.db`), read only.

macOS lets an app read it only with Full Disk Access. The database is opened read-only
and never changed; only one-to-one chats are read (no group chats). Message text is in
`message.text`, or on newer macOS only in `attributedBody` (an archived
NSAttributedString), from which the plain string is taken.
"""

import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import UserError

DEFAULT_PATH = Path.home() / "Library" / "Messages" / "chat.db"
# Apple's epoch; newer macOS keeps nanoseconds, older seconds.
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
# chat.style of a one-to-one chat (43 is a group).
SINGLE_CHAT = 45
MAX_TEXT = 2000
NO_ACCESS = (
    "Нет доступа к сообщениям Mac. Откройте «Системные настройки» → «Конфиденциальность и "
    "безопасность» → «Полный доступ к диску», включите Artist Lead Finder и перезапустите "
    "приложение."
)
NOT_MAC = "Переписка iMessage читается только в приложении на Mac."
NOT_DIGIT = re.compile(r"\D")


@dataclass
class OutgoingStatus:
    """What chat.db says about one message sent from this Mac."""

    rowid: int
    is_sent: bool
    is_delivered: bool
    error: int
    delivered_at: datetime | None


def handle_key(value: str | None) -> str:
    """The same person however Messages wrote the handle: an email in lower case, a phone
    by its last 10 digits."""
    text = (value or "").strip()
    if "@" in text:
        return text.lower()
    digits = NOT_DIGIT.sub("", text)
    return digits[-10:] if len(digits) >= 7 else text.lower()


def comparable_text(text: str) -> str:
    """Text compared across apps: NFC, curly quotes straightened, whitespace collapsed."""
    text = unicodedata.normalize("NFC", text).replace("\ufffc", " ")
    text = text.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'})
    return " ".join(text.split())


def apple_time(value) -> datetime | None:
    if not value:
        return None
    seconds = value / 1_000_000_000 if value > 10**12 else value
    return APPLE_EPOCH + timedelta(seconds=seconds)


def apple_value(moment: datetime) -> int:
    return int((moment - APPLE_EPOCH).total_seconds() * 1_000_000_000)


def attributed_text(blob: bytes | None) -> str:
    """The string of an archived NSAttributedString (typedstream): after the NSString
    class name comes «+», a length (one byte, or 0x81 + 2 bytes, 0x82 + 3 bytes, little
    endian) and the UTF-8 text."""
    if not blob:
        return ""
    start = blob.find(b"NSString")
    if start < 0:
        return ""
    plus = blob.find(b"+", start + len(b"NSString"))
    if plus < 0 or plus + 1 >= len(blob):
        return ""
    position = plus + 1
    length = blob[position]
    position += 1
    if length == 0x81:
        length = int.from_bytes(blob[position : position + 2], "little")
        position += 2
    elif length == 0x82:
        length = int.from_bytes(blob[position : position + 3], "little")
        position += 3
    return blob[position : position + length].decode("utf-8", errors="replace")


def message_text(text: str | None, body: bytes | None) -> str:
    value = text if text else attributed_text(body)
    # The object replacement character stands for an attachment.
    value = (value or "").replace("￼", "[вложение]").strip()
    return value[:MAX_TEXT]


class MessagesDb:
    def __init__(self, path: Path | None = None, platform: str = sys.platform):
        self.path = path or DEFAULT_PATH
        self.platform = platform

    def available(self) -> dict:
        if self.platform != "darwin" and self.path == DEFAULT_PATH:
            return {"available": False, "reason": NOT_MAC}
        try:
            self._open().close()
        except UserError as error:
            return {"available": False, "reason": str(error)}
        return {"available": True, "reason": ""}

    def _open(self) -> sqlite3.Connection:
        if self.platform != "darwin" and self.path == DEFAULT_PATH:
            raise UserError(NOT_MAC)
        if not self.path.exists():
            raise UserError(NO_ACCESS)
        try:
            db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=5)
            db.execute("select 1 from message limit 1")
        except sqlite3.Error:
            raise UserError(NO_ACCESS) from None
        db.row_factory = sqlite3.Row
        return db

    def threads(self, days: int = 14, limit: int = 30, unanswered: bool = False) -> list[dict]:
        """One-to-one chats with messages in the last `days`, newest first: the other side,
        the last message and whether it is theirs (waiting for an answer)."""
        since = apple_value(datetime.now(timezone.utc) - timedelta(days=max(1, days)))
        db = self._open()
        try:
            rows = db.execute(
                """
                select c.chat_identifier as handle, m.text, m.attributedBody as body,
                       m.date, m.is_from_me, m.service
                  from message m
                  join chat_message_join j on j.message_id = m.ROWID
                  join chat c on c.ROWID = j.chat_id
                 where c.style = ? and m.date > ? and m.item_type = 0
                 order by m.date desc
                 limit 5000
                """,
                (SINGLE_CHAT, since),
            ).fetchall()
        finally:
            db.close()
        found: dict[str, dict] = {}
        for row in rows:
            handle = row["handle"]
            item = found.get(handle)
            if item is None:
                item = found[handle] = {
                    "handle": handle,
                    "last_text": message_text(row["text"], row["body"])[:300],
                    "last_at": _iso(apple_time(row["date"])),
                    "last_from_me": bool(row["is_from_me"]),
                    "waiting_since": None,
                    "service": row["service"] or "",
                    "messages": 0,
                    "_open": True,
                }
            item["messages"] += 1
            # Their messages after our last one: the thread waits for an answer since the
            # first of them.
            if row["is_from_me"]:
                item["_open"] = False
            elif item["_open"]:
                item["waiting_since"] = _iso(apple_time(row["date"]))
        for item in found.values():
            item.pop("_open")
        result = list(found.values())
        if unanswered:
            result = [item for item in result if not item["last_from_me"]]
        return result[: max(1, min(limit, 100))]

    def thread(self, handle: str, limit: int = 40) -> list[dict]:
        """The last messages with this phone or email, oldest first."""
        db = self._open()
        try:
            rows = db.execute(
                """
                select m.text, m.attributedBody as body, m.date, m.is_from_me
                  from message m
                  join chat_message_join j on j.message_id = m.ROWID
                  join chat c on c.ROWID = j.chat_id
                 where c.style = ? and c.chat_identifier = ? and m.item_type = 0
                 order by m.date desc
                 limit ?
                """,
                (SINGLE_CHAT, handle, max(1, min(limit, 200))),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "from_me": bool(row["is_from_me"]),
                "text": message_text(row["text"], row["body"]),
                "at": _iso(apple_time(row["date"])),
            }
            for row in reversed(rows)
        ]

    # ---------- what Messages did with a message sent from this app ----------

    def max_rowid(self) -> int:
        """The newest message row now: a send is looked for only above it."""
        db = self._open()
        try:
            row = db.execute("select max(ROWID) from message").fetchone()
        finally:
            db.close()
        return int(row[0] or 0)

    def find_outgoing(self, key: str, text: str, after_rowid: int) -> OutgoingStatus | None:
        """The first message from this Mac to `key` (a handle_key) with this text, newer
        than `after_rowid`, with its sent / delivered / error flags where macOS has them."""
        wanted = comparable_text(text)
        db = self._open()
        try:
            columns = {row[1] for row in db.execute("pragma table_info(message)")}
            flags = [
                f"m.{name}" if name in columns else f"null as {name}"
                for name in ("is_sent", "is_delivered", "error", "date_delivered")
            ]
            rows = db.execute(
                f"""
                select m.ROWID as rowid, m.text, m.attributedBody as body, {", ".join(flags)},
                       c.chat_identifier as handle
                  from message m
                  join chat_message_join j on j.message_id = m.ROWID
                  join chat c on c.ROWID = j.chat_id
                 where m.is_from_me = 1 and m.ROWID > ?
                 order by m.ROWID
                 limit 500
                """,
                (after_rowid,),
            ).fetchall()
        except sqlite3.Error:
            raise UserError(NO_ACCESS) from None
        finally:
            db.close()
        for row in rows:
            if handle_key(row["handle"]) != key:
                continue
            found = comparable_text(row["text"] or attributed_text(row["body"]))
            # Messages may append a link preview or trim spaces; the start must match.
            if found != wanted and not (wanted[:40] and found.startswith(wanted[:40])):
                continue
            code, delivered = row["error"], row["date_delivered"]
            return OutgoingStatus(
                rowid=int(row["rowid"]),
                # Old schemas without the flags: the row existing means Messages sent it.
                is_sent=bool(row["is_sent"]) if row["is_sent"] is not None else True,
                is_delivered=bool(row["is_delivered"]),
                error=int(code) if isinstance(code, int) else 0,
                delivered_at=apple_time(delivered) if isinstance(delivered, int) else None,
            )
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None
