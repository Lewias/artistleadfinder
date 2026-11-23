"""«Ответы»: reading the Direct threads of outreach for phones and emails.

A scan takes the conversations one sender account had in the last days and reads them one
by one in that account's window, like the outreach queue: the shell asks `next_job`, opens
the thread (or the profile when the thread is unknown), the core reads it
(`inbox.reader`) and `commit` stores what the other person wrote. Phones and emails wait
for review; only the ones the user picks go to the iMessage CRM.

Reading never types or sends. Login, checkpoint and Instagram's limit stop the scan; any
other failure skips that thread.

Between scans the same queue watches the threads that wait for a follow-up step: each one
is read for a reply every few hours (`inbox_watch_hours`), one thread at a time with the
scan's pauses. A reply stops the chain before its next step. The shell runs these reads
between sends in the same sender window, never at the same time as a send.
"""

import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update

from ..errors import UserError
from ..models import (
    BrowserQueue,
    Conversation,
    FollowUpRun,
    InboxFinding,
    InboxScan,
    InboxScanItem,
    Lead,
    Message,
    SearchJob,
    utcnow,
)
from ..outreach import reasons
from ..outreach.campaigns import record_reply
from ..outreach.followups import OPEN as RUN_OPEN
from ..outreach.followups import FollowUpScheduler, new_replies
from ..outreach.senders import as_utc, current_status, set_status
from .extract import find_contacts
from .settings import inbox_settings

log = logging.getLogger(__name__)

THREAD_URL = "https://www.instagram.com/direct/t/{}/"
PROFILE_URL = "https://www.instagram.com/{}/"
# A thread taken by the shell without a result after this long is given up.
STALE_AFTER = timedelta(minutes=2)
STOPPING = {
    "login": "Аккаунт вышел из Instagram. Войдите в окне аккаунта и запустите снова.",
    "session_expired": "Сеанс Instagram истёк. Войдите в окне аккаунта и запустите снова.",
    "checkpoint": "Instagram просит подтверждение (checkpoint). Пройдите его в окне аккаунта.",
    "phone_required": "Instagram просит подтвердить номер телефона в окне аккаунта.",
    "code_required": "Instagram просит код подтверждения в окне аккаунта.",
    "password_reset": "Instagram просит сменить пароль в окне аккаунта.",
    "rate_limited": "Instagram ограничил запросы. Попробуйте через несколько часов.",
    "action_blocked": "Instagram временно ограничил действия. Попробуйте через несколько часов.",
}
# A watch read that meets an account problem stops the account like a send would.
ACCOUNT_STATUS = {
    "login": "auth_required",
    "session_expired": "auth_required",
    "checkpoint": "checkpoint",
    "phone_required": "checkpoint",
    "code_required": "checkpoint",
    "password_reset": "checkpoint",
    "rate_limited": "rate_limited",
    "action_blocked": "rate_limited",
}
LIMIT_PAUSE = timedelta(hours=6)
DAYS = (7, 30, 90, 365)
MAX_FINDINGS = 500
LABEL_PHONE = "Дал номер"
LABEL_EMAIL = "Дал email"


class InboxService:
    def __init__(
        self,
        sessions,
        settings: Callable[[], dict],
        window_open: Callable[[str], bool],
        sender_names: Callable[[], dict[str, str]],
        crm_merge: Callable[[str, list[dict]], dict],
        now: Callable[[], datetime] = utcnow,
        jitter: Callable[[float, float], float] = random.uniform,
        followups: FollowUpScheduler | None = None,
    ):
        self.sessions = sessions
        self.settings = settings
        self.window_open = window_open
        self.sender_names = sender_names
        self.crm_merge = crm_merge
        self.now = now
        self.jitter = jitter
        self.followups = followups or FollowUpScheduler()
        # The next watch read waits until then (the scan's pause between threads).
        self.watch_next_at: datetime | None = None

    # ---------- scans ----------

    def recover(self) -> None:
        """Scans cut off by a closed app stop; their threads are not read twice."""
        with self.sessions.begin() as session:
            session.execute(
                update(InboxScan)
                .where(InboxScan.status == "running")
                .values(
                    status="stopped",
                    reason="Приложение было закрыто во время чтения.",
                    finished_at=self.now(),
                )
            )

    def start(self, params: dict) -> dict:
        sender = str(params.get("sender") or "")
        if sender not in self.sender_names():
            raise UserError("Выберите аккаунт.")
        days = int(params.get("days") or 30)
        if days not in DAYS:
            raise UserError("Неизвестный период.")
        settings = inbox_settings(self.settings())
        now = self.now()
        with self.sessions.begin() as session:
            if session.scalar(select(InboxScan.id).where(InboxScan.status == "running")):
                raise UserError("Директ уже читается. Дождитесь конца или остановите чтение.")
            conversations = session.scalars(
                select(Conversation.id)
                .where(
                    Conversation.sender_account_id == sender,
                    Conversation.last_outbound_at >= now - timedelta(days=days),
                )
                .order_by(Conversation.last_outbound_at.desc())
                .limit(settings.inbox_max_threads)
            ).all()
            if not conversations:
                raise UserError(f"С этого аккаунта никому не писали за {days} дн.: читать нечего.")
            scan = InboxScan(
                sender_account_id=sender,
                days=days,
                total=len(conversations),
                started_at=now,
                next_at=now,
            )
            session.add(scan)
            session.flush()
            session.add_all(
                InboxScanItem(scan_id=scan.id, conversation_id=conversation)
                for conversation in conversations
            )
        log.info("inbox_scan_started", extra={"detail": f"{len(conversations)} threads"})
        return self.state({})

    def stop(self, params: dict) -> dict:
        with self.sessions.begin() as session:
            for scan in session.scalars(select(InboxScan).where(InboxScan.status == "running")):
                self._finish(session, scan, "stopped", "Остановлено вручную.")
        return self.state({})

    @staticmethod
    def _finish(session, scan: InboxScan, status: str, reason: str = "") -> None:
        scan.status, scan.reason, scan.finished_at = status, reason, utcnow()
        session.execute(
            update(InboxScanItem)
            .where(InboxScanItem.scan_id == scan.id, InboxScanItem.status == "pending")
            .values(status="error", error="stopped")
        )

    # ---------- the shell's queue ----------

    def _wait_reason(self, session, scan: InboxScan, now) -> str:
        """Why the next thread cannot be read now; empty when it can."""
        return self._sender_wait(session, scan.sender_account_id, now)

    def _sender_wait(self, session, sender: str, now) -> str:
        from ..instagram_warmup import warming_profiles

        if sender in warming_profiles(session):
            return "В окне аккаунта идёт прогрев: чтение продолжится после него."
        if not self.window_open(sender):
            return "Откройте окно аккаунта, чтобы читать Директ."
        busy = session.scalar(
            select(BrowserQueue.profile_id)
            .join(SearchJob, SearchJob.id == BrowserQueue.job_id)
            .where(SearchJob.status == "running", BrowserQueue.profile_id == sender)
        )
        if busy:
            return "В окне аккаунта идёт парсинг: чтение продолжится после него."
        health = current_status(session, sender, now)
        if health.status != "active":
            return "Аккаунт недоступен для работы: " + (health.reason or health.status)
        return ""

    def next_job(self) -> dict | None:
        now = self.now()
        with self.sessions.begin() as session:
            scan = session.scalar(select(InboxScan).where(InboxScan.status == "running"))
            if scan is None:
                return self._watch_job(session, now)
            for item in session.scalars(
                select(InboxScanItem).where(
                    InboxScanItem.scan_id == scan.id,
                    InboxScanItem.status == "reading",
                    InboxScanItem.claimed_at < now - STALE_AFTER,
                )
            ):
                item.status, item.error = "error", "lost"
                scan.done += 1
                scan.errors += 1
            if scan.next_at and as_utc(scan.next_at) > now:
                return None
            if session.scalar(
                select(InboxScanItem.id).where(
                    InboxScanItem.scan_id == scan.id, InboxScanItem.status == "reading"
                )
            ):
                return None
            item = session.scalar(
                select(InboxScanItem)
                .where(InboxScanItem.scan_id == scan.id, InboxScanItem.status == "pending")
                .order_by(InboxScanItem.id)
            )
            if item is None:
                self._finish(session, scan, "done")
                return None
            if self._wait_reason(session, scan, now):
                return None
            conversation = session.get(Conversation, item.conversation_id)
            lead = session.get(Lead, conversation.lead_id) if conversation else None
            if lead is None:
                item.status, item.error = "error", "gone"
                scan.done += 1
                return None
            item.status, item.claimed_at = "reading", now
            outbound = session.scalars(
                select(Message.body)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "outbound",
                )
                .order_by(Message.sent_at)
                .limit(20)
            ).all()
            thread = conversation.platform_thread_id or ""
            return {
                "profile_id": scan.sender_account_id,
                "item_id": item.id,
                "username": lead.username,
                "url": THREAD_URL.format(thread) if thread else PROFILE_URL.format(lead.username),
                "outbound": [body for body in outbound if body],
            }

    def commit(self, params: dict) -> dict:
        result = params.get("result") if isinstance(params.get("result"), dict) else {}
        settings = inbox_settings(self.settings())
        now = self.now()
        if params.get("kind") == "watch":
            return self._commit_watch(int(params["item_id"]), result, now)
        with self.sessions.begin() as session:
            item = session.get(InboxScanItem, int(params["item_id"]))
            if item is None or item.status != "reading":
                return {"ok": False}
            scan = session.get(InboxScan, item.scan_id)
            conversation = session.get(Conversation, item.conversation_id)
            item.status = "done"
            scan.done += 1
            pause = self.jitter(
                min(settings.inbox_delay_min_seconds, settings.inbox_delay_max_seconds),
                max(settings.inbox_delay_min_seconds, settings.inbox_delay_max_seconds),
            )
            scan.next_at = now + timedelta(seconds=pause)
            if result.get("outcome") != "read":
                error = str(result.get("error") or "browser")[:40]
                item.status, item.error = "error", error
                scan.errors += 1
                if error in STOPPING:
                    self._finish(session, scan, "stopped", STOPPING[error])
                return {"ok": True}
            if conversation is None:
                return {"ok": True}
            thread = str(result.get("thread_id") or "")[:80]
            if thread and not conversation.platform_thread_id:
                conversation.platform_thread_id = thread
            replies = [
                str(message.get("text") or "")
                for message in result.get("messages") or []
                if isinstance(message, dict) and message.get("side") == "in"
            ]
            if replies:
                scan.replied += 1
                self._stop_chains(session, conversation, result.get("messages") or [], now)
            scan.found += self._store(session, scan, conversation, replies, settings)
            if scan.done >= scan.total and scan.status == "running":
                self._finish(session, scan, "done")
        return {"ok": True}

    # ---------- watching threads that wait for a follow-up ----------

    def _watch_job(self, session, now) -> dict | None:
        """The thread of an active chain read longest ago, when it is due for a read."""
        if self.watch_next_at is not None and self.watch_next_at > now:
            return None
        settings = inbox_settings(self.settings())
        since = now - timedelta(hours=settings.inbox_watch_hours)
        rows = session.execute(
            select(FollowUpRun, Conversation, Lead)
            .join(Conversation, Conversation.id == FollowUpRun.conversation_id)
            .join(Lead, Lead.id == Conversation.lead_id)
            .where(
                FollowUpRun.status == "active",
                Conversation.status == "waiting_reply",
                or_(Conversation.reply_checked_at.is_(None), Conversation.reply_checked_at < since),
            )
            .order_by(Conversation.reply_checked_at.is_not(None), Conversation.reply_checked_at)
            .limit(50)
        ).all()
        for run, conversation, lead in rows:
            if self._sender_wait(session, run.sender_account_id, now):
                continue
            conversation.reply_checked_at = now
            pause = self.jitter(
                min(settings.inbox_delay_min_seconds, settings.inbox_delay_max_seconds),
                max(settings.inbox_delay_min_seconds, settings.inbox_delay_max_seconds),
            )
            self.watch_next_at = now + timedelta(seconds=pause)
            thread = conversation.platform_thread_id or ""
            return {
                "kind": "watch",
                "profile_id": run.sender_account_id,
                "item_id": run.id,
                "username": lead.username,
                "url": THREAD_URL.format(thread) if thread else PROFILE_URL.format(lead.username),
                "outbound": self._ours(session, conversation),
            }
        return None

    @staticmethod
    def _ours(session, conversation) -> list[str]:
        rows = session.scalars(
            select(Message.body)
            .where(Message.conversation_id == conversation.id, Message.direction == "outbound")
            .order_by(Message.sent_at)
            .limit(20)
        )
        return [body for body in rows if body]

    def _commit_watch(self, run_id: int, result: dict, now) -> dict:
        with self.sessions.begin() as session:
            run = session.get(FollowUpRun, run_id)
            if run is None:
                return {"ok": False}
            conversation = session.get(Conversation, run.conversation_id)
            if result.get("outcome") != "read":
                error = str(result.get("error") or "")
                if error in ACCOUNT_STATUS:
                    limited = ACCOUNT_STATUS[error] == "rate_limited"
                    set_status(
                        session,
                        run.sender_account_id,
                        ACCOUNT_STATUS[error],
                        STOPPING[error],
                        now + LIMIT_PAUSE if limited else None,
                    )
                return {"ok": True}
            thread = str(result.get("thread_id") or "")[:80]
            if thread and not conversation.platform_thread_id:
                conversation.platform_thread_id = thread
            replied = self._stop_chains(session, conversation, result.get("messages") or [], now)
            return {"ok": True, "replied": replied}

    def _stop_chains(self, session, conversation, messages: list, now) -> bool:
        """A new message from the person after our first message of an open chain stops
        every chain of the lead. Our own messages, notices and replies already recorded
        are not new answers."""
        if conversation is None or conversation.status != "waiting_reply":
            return False
        run = session.scalar(
            select(FollowUpRun)
            .where(FollowUpRun.conversation_id == conversation.id, FollowUpRun.status.in_(RUN_OPEN))
            .order_by(FollowUpRun.id.desc())
        )
        if run is None:
            return False
        ours = [session.get(Message, run.origin_message_id).body]
        ours += [run.steps[i]["text"] for i in range(min(run.next_step, len(run.steps)))]
        known = session.scalars(
            select(Message.body).where(
                Message.conversation_id == conversation.id, Message.direction == "inbound"
            )
        ).all()
        found = new_replies([m for m in messages if isinstance(m, dict)], ours, list(known))
        if not found:
            return False
        bodies = [text[:4000] for text in found[:5]]
        record_reply(session, conversation, bodies, now, self.followups)
        log.info("inbox_reply_stopped_followups", extra={"reason": reasons.REPLY_RECEIVED})
        return True

    def _store(self, session, scan, conversation, replies: list[str], settings) -> int:
        lead = session.get(Lead, conversation.lead_id)
        known = {
            (row.kind, row.value)
            for row in session.scalars(
                select(InboxFinding).where(InboxFinding.lead_id == conversation.lead_id)
            )
        }
        added = 0
        for text in replies:
            for found in find_contacts(text, settings.inbox_region):
                if (found.kind, found.value) in known:
                    continue
                known.add((found.kind, found.value))
                session.add(
                    InboxFinding(
                        lead_id=conversation.lead_id,
                        conversation_id=conversation.id,
                        sender_account_id=scan.sender_account_id,
                        username=lead.username,
                        kind=found.kind,
                        value=found.value[:200],
                        raw=found.raw[:200],
                        snippet=found.snippet,
                        guessed=found.guessed,
                        found_at=self.now(),
                    )
                )
                added += 1
        return added

    # ---------- review ----------

    def state(self, params: dict) -> dict:
        names = self.sender_names()
        now = self.now()
        with self.sessions.begin() as session:
            scan = session.scalar(select(InboxScan).order_by(InboxScan.id.desc()))
            findings = session.scalars(
                select(InboxFinding)
                .where(InboxFinding.status == "new")
                .order_by(InboxFinding.found_at.desc(), InboxFinding.id)
                .limit(MAX_FINDINGS)
            ).all()
            counts = dict(
                session.execute(
                    select(InboxFinding.status, func.count()).group_by(InboxFinding.status)
                ).all()
            )
            waiting = (
                self._wait_reason(session, scan, now) if scan and scan.status == "running" else ""
            )
            return {
                "scan": self._scan_dict(scan, names, waiting) if scan else None,
                "findings": [
                    {
                        "id": row.id,
                        "username": row.username,
                        "kind": row.kind,
                        "value": row.value,
                        "raw": row.raw,
                        "snippet": row.snippet,
                        "guessed": row.guessed,
                        "sender": names.get(row.sender_account_id, ""),
                        "found_at": as_utc(row.found_at).isoformat(),
                    }
                    for row in findings
                ],
                "counts": {key: counts.get(key, 0) for key in ("new", "added", "hidden")},
            }

    @staticmethod
    def _scan_dict(scan: InboxScan, names: dict, waiting: str) -> dict:
        return {
            "id": scan.id,
            "sender": scan.sender_account_id,
            "sender_name": names.get(scan.sender_account_id, ""),
            "status": scan.status,
            "days": scan.days,
            "total": scan.total,
            "done": scan.done,
            "replied": scan.replied,
            "found": scan.found,
            "errors": scan.errors,
            "reason": scan.reason,
            "waiting": waiting,
            "started_at": as_utc(scan.started_at).isoformat(),
            "finished_at": as_utc(scan.finished_at).isoformat() if scan.finished_at else None,
        }

    def _picked(self, session, params: dict) -> list[InboxFinding]:
        ids = {int(value) for value in params.get("ids") or []}
        if not ids:
            raise UserError("Выберите контакты.")
        return list(
            session.scalars(
                select(InboxFinding).where(InboxFinding.id.in_(ids), InboxFinding.status == "new")
            )
        )

    def add_to_crm(self, params: dict) -> dict:
        """Picked phones and emails go to the iMessage CRM, one contact per person: their
        Instagram, the phone first, a status and the words they wrote it in."""
        with self.sessions.begin() as session:
            picked = self._picked(session, params)
            people: dict[int, list[InboxFinding]] = {}
            for row in picked:
                people.setdefault(row.lead_id, []).append(row)
            items = []
            for lead_id, rows in people.items():
                rows.sort(key=lambda row: row.kind != "phone")
                items.append(
                    {
                        "name": rows[0].username,
                        "channels": [{"kind": row.kind, "value": row.value} for row in rows]
                        + [{"kind": "instagram", "value": rows[0].username}],
                        "statuses": [LABEL_PHONE if rows[0].kind == "phone" else LABEL_EMAIL],
                        "notes": "Из Директа: " + " / ".join(row.snippet for row in rows),
                        "lead_id": lead_id,
                    }
                )
            ids = [row.id for row in picked]
        result = self.crm_merge("imessage", items) if items else {"added": 0, "merged": 0}
        with self.sessions.begin() as session:
            session.execute(
                update(InboxFinding).where(InboxFinding.id.in_(ids)).values(status="added")
            )
        return {**result, "state": self.state({})}

    def hide(self, params: dict) -> dict:
        with self.sessions.begin() as session:
            for row in self._picked(session, params):
                row.status = "hidden"
        return self.state({})
