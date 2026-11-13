"""«Диалоги» iMessage: every one-to-one chat of the Mac's Messages, read and answered here.

Reading is chat.db, read only (the same reader the assistant uses), so it works only in the
app on a Mac with Full Disk Access. A reply is the user's own text: it becomes a draft and
goes through the same Mac send as an assistant draft — only to contacts from the CRM, not
to «Не связываться», at most SEND_LIMIT_PER_HOUR an hour, sent once, its fate read back from
chat.db. No browser window is involved.
"""

from collections.abc import Callable

from ..errors import UserError
from ..imessage.phones import normalize_recipient
from ..models import AssistantDraft
from .mac_send import DO_NOT_CONTACT, MacSend, confirmation_token
from .messages_db import MessagesDb

MAX_TEXT = 2000
DAYS = 90
MAX_THREADS = 100
MAX_MESSAGES = 200
USER_MODEL = "user"
REASON = "Ответ из «Диалогов»"


class IMessageDialogs:
    def __init__(
        self,
        sessions,
        messages_db: MessagesDb,
        mac_send: MacSend,
        contacts: Callable[[], dict[str, dict]],
    ):
        self.sessions = sessions
        self.messages_db = messages_db
        self.mac_send = mac_send
        # Phones and emails of the user's CRM contacts → the contact (id, crm, name, statuses).
        self.contacts = contacts

    def _contact(self, index: dict[str, dict], handle: str) -> dict | None:
        return index.get(normalize_recipient(handle) or "")

    def _reply_block(self, contact: dict | None, sending: dict) -> str:
        """Why the reply box is closed for this chat; empty when a reply can go."""
        if not sending["enabled"]:
            return sending["description"]
        if contact is None:
            return "Этого номера нет в CRM: отвечать отсюда можно только контактам из CRM."
        if DO_NOT_CONTACT in contact["statuses"]:
            return "У контакта статус «Не связываться»."
        return ""

    def state(self, params: dict) -> dict:
        self.mac_send.refresh()
        available = self.messages_db.available()
        sending = self.mac_send.status()
        threads: list[dict] = []
        if available["available"]:
            days = max(1, min(int(params.get("days") or DAYS), 365))
            try:
                rows = self.messages_db.threads(days=days, limit=MAX_THREADS)
            except UserError as error:
                available = {"available": False, "reason": str(error)}
                rows = []
            index = self.contacts()
            for row in rows:
                contact = self._contact(index, row["handle"])
                threads.append(
                    {
                        **row,
                        "contact": contact,
                        "reply_block": self._reply_block(contact, sending),
                    }
                )
        return {
            "messages": available,
            "sending": sending,
            "threads": threads,
            "sends": self.mac_send.recent(days=7, limit=30),
        }

    def thread(self, params: dict) -> dict:
        handle = str(params.get("handle") or "").strip()
        if not handle:
            raise UserError("Выберите переписку.")
        messages = self.messages_db.thread(handle, MAX_MESSAGES)
        contact = self._contact(self.contacts(), handle)
        return {
            "handle": handle,
            "contact": contact,
            "reply_block": self._reply_block(contact, self.mac_send.status()),
            "messages": messages,
        }

    def reply(self, params: dict) -> dict:
        handle = normalize_recipient(params.get("handle"))
        if handle is None:
            raise UserError("Отвечать можно только на телефон или email.")
        text = str(params.get("text") or "").strip()
        if not text:
            raise UserError("Введите текст.")
        if len(text) > MAX_TEXT:
            raise UserError(f"Сообщение длиннее {MAX_TEXT} символов.")
        contact = self.contacts().get(handle)
        block = self._reply_block(contact, self.mac_send.status())
        if block:
            raise UserError(block)
        with self.sessions.begin() as session:
            draft = AssistantDraft(
                handle=handle,
                contact_id=contact["id"],
                contact_name=contact["name"][:160],
                text=text,
                reason=REASON,
                model=USER_MODEL,
            )
            session.add(draft)
            session.flush()
            draft_id = draft.id
        try:
            # The user pressed «Отправить» on exactly this text: that is the confirmation.
            send = self.mac_send.send(draft_id, confirmation_token(handle, text))
        except UserError:
            with self.sessions.begin() as session:
                session.get(AssistantDraft, draft_id).status = "rejected"
            raise
        return {"send": send}
