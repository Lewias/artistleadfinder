"""Sending an assistant draft through Messages on this Mac, only after the user confirmed it.

- Nothing sends on its own: the assistant only drafts. The «Ассистент» screen calls this
  after the user saw the exact recipient and text (the confirmation token is a hash of
  both, so an edited text needs a new confirmation).
- One send per draft, by either route: a draft handed to the iPhone queue or to Messages
  is no longer pending. A Mac send is replaced only when it definitely failed. Contacts
  outside the CRM or marked «Не связываться» and anything that is not a phone or an email
  are refused, and at most SEND_LIMIT_PER_HOUR messages go out per hour.
- Messages does the sending (osascript, in a thread: the RPC loop must not wait for it).
  What happened next is read back from chat.db, from the row Messages wrote for the
  message after the ROWID taken before sending: queued -> dispatched -> sent -> delivered,
  or failed (Messages reported an error). A send that does not appear in chat.db within
  CONFIRM_WINDOW, or that Messages did not answer in time, becomes uncertain; it is never
  re-sent automatically.
"""

import hashlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, func, select

from ..errors import UserError
from ..imessage.mac_sender import TIMEOUT_SECONDS, MessagesTransport, SendFailed, SendUnknown
from ..imessage.phones import normalize_recipient
from ..models import AssistantDraft, AssistantSend, utcnow
from .messages_db import OutgoingStatus, handle_key

log = logging.getLogger(__name__)

TRANSITIONS = {
    "queued": {"dispatched", "sent", "delivered", "failed", "uncertain"},
    "dispatched": {"sent", "delivered", "failed", "uncertain"},
    "uncertain": {"sent", "delivered", "failed"},
    "sent": {"delivered", "failed"},
    "delivered": set(),
    "failed": set(),
}
TRACKED = ("queued", "dispatched", "uncertain", "sent")
CONFIRM_WINDOW = timedelta(minutes=2)
# A queued send whose osascript never reported back (the app closed meanwhile).
STALE_QUEUED = timedelta(seconds=TIMEOUT_SECONDS) + CONFIRM_WINDOW
TRACK_FOR = timedelta(hours=24)  # after this a 'sent' message is not checked any more
SEND_LIMIT_PER_HOUR = 20
DO_NOT_CONTACT = "Не связываться"
CHECK_CHAT = "Проверьте переписку в Сообщениях, прежде чем отправлять снова."


def confirmation_token(recipient: str, text: str) -> str:
    return hashlib.sha256(f"{recipient}\x00{text}".encode()).hexdigest()


def naive(moment: datetime | None) -> datetime | None:
    """UTC without tzinfo, as SQLite gives datetimes back."""
    return moment.replace(tzinfo=None) if moment is not None and moment.tzinfo else moment


class Transport(Protocol):
    enabled: bool

    def describe(self) -> str: ...

    def send(self, recipient: str, text: str) -> None: ...


class Outbox(Protocol):
    """The part of the chat.db reader that confirms what Messages did."""

    def max_rowid(self) -> int: ...

    def find_outgoing(self, key: str, text: str, after_rowid: int) -> OutgoingStatus | None: ...


def in_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="mac-send", daemon=True).start()


class MacSend:
    def __init__(
        self,
        sessions,
        contacts: Callable[[], dict[str, dict]],
        outbox: Outbox,
        transport: Transport | None = None,
        background: Callable[[Callable[[], None]], None] = in_thread,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        # Phones and emails of the user's CRM contacts → the contact (id, name, statuses).
        self.contacts = contacts
        self.outbox = outbox
        self.transport = transport or MessagesTransport()
        self.background = background
        self.clock = clock
        self.lock = threading.Lock()

    def now(self) -> datetime:
        return naive(self.clock())

    def status(self) -> dict:
        return {"enabled": self.transport.enabled, "description": self.transport.describe()}

    # ---------- confirm and send ----------

    def preview(self, draft_id: int) -> dict:
        """What the confirmation dialog shows; its token authorizes exactly this send."""
        draft = self._sendable(draft_id)
        return {
            "draft_id": draft.id,
            "contact": draft.contact_name or draft.handle,
            "recipient": draft.handle,
            "text": draft.text,
            "token": confirmation_token(draft.handle, draft.text),
        }

    def send(self, draft_id: int, token: str) -> dict:
        with self.lock:  # a double click must not send twice
            draft = self._sendable(draft_id)
            if token != confirmation_token(draft.handle, draft.text):
                raise UserError(
                    "Черновик изменился после подтверждения. Подтвердите отправку ещё раз."
                )
            # Without chat.db the status cannot be read back: refuse before sending.
            after = self.outbox.max_rowid()
            with self.sessions.begin() as session:
                session.execute(
                    delete(AssistantSend).where(
                        AssistantSend.draft_id == draft.id, AssistantSend.state == "failed"
                    )
                )
                job = AssistantSend(
                    draft_id=draft.id,
                    handle=draft.handle,
                    contact_name=draft.contact_name,
                    text=draft.text,
                    token=token,
                    after_rowid=after,
                    confirmed_at=self.now(),
                    updated_at=self.now(),
                )
                session.add(job)
                session.get(AssistantDraft, draft.id).status = "queued"
                session.flush()
                job_id = job.id
        handle, text = draft.handle, draft.text
        self.background(lambda: self._dispatch(job_id, draft_id, handle, text))
        return self.job(draft_id) or {}

    def _dispatch(self, job_id: int, draft_id: int, handle: str, text: str) -> None:
        try:
            self.transport.send(handle, text)
        except SendFailed as error:
            self._move(job_id, "failed", str(error))
            self._reopen(draft_id)
            return
        except SendUnknown as error:
            self._move(job_id, "uncertain", f"{error} {CHECK_CHAT}")
            return
        except Exception as error:  # noqa: BLE001 - the job must not stay queued
            log.warning("mac_send_failed", extra={"error_type": type(error).__name__})
            self._move(job_id, "uncertain", f"Отправка прервалась. {CHECK_CHAT}")
            return
        self._move(job_id, "dispatched")
        self.refresh(only_job=job_id)

    def _sendable(self, draft_id: int) -> AssistantDraft:
        if not self.transport.enabled:
            raise UserError(self.transport.describe())
        with self.sessions() as session:
            draft = session.get(AssistantDraft, int(draft_id or 0))
            job = session.scalar(select(AssistantSend).where(AssistantSend.draft_id == draft_id))
            recent = session.scalar(
                select(func.count(AssistantSend.id)).where(
                    AssistantSend.state != "failed",
                    AssistantSend.confirmed_at > self.now() - timedelta(hours=1),
                )
            )
        if draft is None:
            raise UserError("Черновик не найден.")
        if job is not None and job.state != "failed":
            raise UserError("Этот черновик уже отправлялся; дважды он не отправится.")
        if draft.status != "pending":
            raise UserError("Этот черновик уже отправлен или отклонён.")
        handle = normalize_recipient(draft.handle)
        if handle is None or handle != draft.handle:
            raise UserError("На этот адрес приложение не отправляет (не телефон и не email).")
        contact = self.contacts().get(handle)
        if contact is None:
            raise UserError("Этого номера больше нет в CRM: писать можно только контактам из CRM.")
        if DO_NOT_CONTACT in contact["statuses"]:
            raise UserError("У контакта статус «Не связываться».")
        if int(recent or 0) >= SEND_LIMIT_PER_HOUR:
            raise UserError(
                f"За последний час отправлено {SEND_LIMIT_PER_HOUR} сообщений. Это предел, "
                "чтобы Apple не посчитала вас спамером. Попробуйте позже."
            )
        return draft

    # ---------- status from chat.db ----------

    def refresh(self, only_job: int | None = None) -> int:
        """Updates tracked sends from chat.db. Never raises: the screen must still load."""
        now = self.now()
        with self.sessions() as session:
            query = select(AssistantSend).where(AssistantSend.state.in_(TRACKED))
            if only_job is not None:
                query = query.where(AssistantSend.id == only_job)
            jobs = list(session.scalars(query))
        changed = 0
        for job in jobs:
            age = now - naive(job.confirmed_at)
            if job.state == "sent" and age > TRACK_FOR:
                continue
            try:
                found = self.outbox.find_outgoing(handle_key(job.handle), job.text, job.after_rowid)
            except UserError:
                return changed
            changed += self._apply(job, found, age)
        return changed

    def _apply(self, job: AssistantSend, found: OutgoingStatus | None, age: timedelta) -> int:
        if found is None:
            if job.state == "dispatched" and age > CONFIRM_WINDOW:
                note = f"Сообщение не появилось в базе Сообщений. {CHECK_CHAT}"
                return int(self._move(job.id, "uncertain", note))
            if job.state == "queued" and age > STALE_QUEUED:
                note = f"Приложение не дождалось ответа Сообщений. {CHECK_CHAT}"
                return int(self._move(job.id, "uncertain", note))
            return 0
        with self.sessions.begin() as session:
            session.get(AssistantSend, job.id).message_rowid = found.rowid
        if found.error:
            note = f"Сообщения сообщили об ошибке (код {found.error}): сообщение не доставлено."
            moved = self._move(job.id, "failed", note)
            if moved:
                self._reopen(job.draft_id)
            return int(moved)
        if found.is_delivered:
            return int(self._move(job.id, "delivered", delivered_at=found.delivered_at))
        if found.is_sent:
            return int(self._move(job.id, "sent"))
        return 0

    # ---------- storage ----------

    def _move(
        self, job_id: int, state: str, note: str = "", delivered_at: datetime | None = None
    ) -> bool:
        """Moves a send forward; a late or out-of-order report is ignored."""
        with self.sessions.begin() as session:
            job = session.get(AssistantSend, job_id)
            if job is None or state == job.state or state not in TRANSITIONS[job.state]:
                return False
            job.state, job.note, job.updated_at = state, note[:300], self.now()
            if state == "delivered":
                job.delivered_at = naive(delivered_at) or self.now()
        return True

    def _reopen(self, draft_id: int) -> None:
        """Nothing went out: the draft waits for the user again."""
        with self.sessions.begin() as session:
            draft = session.get(AssistantDraft, draft_id)
            if draft is not None and draft.status == "queued" and draft.campaign_id is None:
                draft.status = "pending"

    @staticmethod
    def _dict(job: AssistantSend) -> dict:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat(timespec="seconds") + "Z" if value else None

        return {
            "id": job.id,
            "draft_id": job.draft_id,
            "handle": job.handle,
            "contact_name": job.contact_name,
            "text": job.text,
            "state": job.state,
            "note": job.note,
            "confirmed_at": iso(naive(job.confirmed_at)),
            "delivered_at": iso(naive(job.delivered_at)),
        }

    def job(self, draft_id: int) -> dict | None:
        with self.sessions() as session:
            job = session.scalar(select(AssistantSend).where(AssistantSend.draft_id == draft_id))
            return self._dict(job) if job else None

    def recent(self, days: int = 7, limit: int = 20) -> list[dict]:
        with self.sessions() as session:
            jobs = session.scalars(
                select(AssistantSend)
                .where(AssistantSend.confirmed_at > self.now() - timedelta(days=days))
                .order_by(AssistantSend.id.desc())
                .limit(limit)
            )
            return [self._dict(job) for job in jobs]
