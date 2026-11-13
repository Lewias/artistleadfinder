"""Warm-up: the mailboxes write short personal notes to each other and to Gmail partners
opened in Nstbrowser; the receiving side finds each note, takes it out of spam, reads it,
sometimes stars it and answers. Gmail learns that people talk with the mailbox, and every
note tells where it landed (inbox or spam): the placement share is the health of the box.

A conversation is at most three notes: a mailbox writes (depth 0), the other side answers
(1), the mailbox answers back (2). Only mailboxes start conversations; partners never send
anything but answers. The volume per mailbox grows from WARM_START by WARM_STEP a day up
to its target, spread over the waking hours of the campaign time zone.
"""

import base64
import logging
import random
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .. import secret_box
from ..errors import UserError
from ..models import (
    EmailAccount,
    EmailWarmupMessage,
    EmailWarmupPartner,
    EmailWorkspace,
    Setting,
    utcnow,
)
from . import addresses, warm_texts
from .clock import as_utc, in_window, iso, local_hour
from .mailer import GmailTransport, MailError, Outgoing
from .nst import DEFAULT_URL, NstClient, NstError, NstSession

log = logging.getLogger(__name__)

WARM_START = 3
WARM_STEP = 2
WARM_MAX = 40
# Waking hours in the campaign time zone: people write in the day and in the evening.
WARM_HOURS = (8, 22)
TICK_SECONDS = 20
HANDLE_DELAY_MINUTES = (8, 90)
REPLY_CHANCE = {0: 0.6, 1: 0.35}
STAR_CHANCE = 0.25
MAX_ATTEMPTS = 4
RETRY_MISSING = timedelta(minutes=20)
RETRY_OFFLINE = timedelta(minutes=15)
MAILBOX_TASKS_PER_TICK = 3
PARTNER_TASKS_PER_SESSION = 5
NST_SETTING = "email_warmup_nst"
LONG_WAIT = timedelta(minutes=30)


def warm_cap(account: EmailAccount, now: datetime) -> int:
    started = as_utc(account.warm_started_at) or now
    days = max(0, (now - started).days)
    return min(account.warm_target, WARM_START + WARM_STEP * days)


class WarmupService:
    def __init__(
        self,
        sessions,
        transport: Callable[[str, str], GmailTransport] = GmailTransport,
        nst_client: Callable[[str, str], NstClient] = NstClient,
        nst_session: Callable[[NstClient, str], NstSession] = NstSession,
        now: Callable[[], datetime] = utcnow,
        rng: random.Random | None = None,
        set_status: Callable[..., None] | None = None,
    ):
        self.sessions = sessions
        self.transport = transport
        self.nst_client = nst_client
        self.nst_session = nst_session
        self.now = now
        self.rng = rng or random.Random()
        # EmailService._set_status: a mailbox the warm-up finds broken stops for campaigns too.
        self.set_status = set_status or (lambda *args, **kwargs: None)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.thread: threading.Thread | None = None
        self.detect = {"running": False, "done": 0, "total": 0, "found": 0}
        self.detect_requested = False
        self.nst_note = ""

    # ---------- RPC ----------

    def call(self, method: str, params: dict):
        handlers = {
            "email.warmup_state": lambda p: self.state(),
            "email.warmup_update": self.update,
            "email.warmup_nst_save": self.nst_save,
            "email.warmup_nst_test": lambda p: self.nst_test(),
            "email.warmup_partners_load": lambda p: self.partners_load(),
            "email.warmup_partner_update": self.partner_update,
            "email.warmup_partner_remove": self.partner_remove,
            "email.warmup_detect": lambda p: self.request_detect(),
        }
        handler = handlers.get(method)
        if handler is None:
            raise UserError("Неизвестный метод приложения.")
        return handler(params or {})

    # ---------- lifecycle ----------

    def restore(self) -> None:
        with self.sessions() as session:
            busy = session.scalar(
                select(func.count())
                .select_from(EmailAccount)
                .where(EmailAccount.pool_warmup.is_(True))
            )
        if busy:
            self._ensure_thread()

    def shutdown(self) -> None:
        self.stopping.set()
        self.wakeup.set()

    def _ensure_thread(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.wakeup.set()
                return
            self.stopping.clear()
            self.thread = threading.Thread(target=self._run, name="email-warmup", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as error:  # the warm-up must survive any single failure
                log.warning(
                    "email_warmup_tick_failed",
                    extra={"error_type": type(error).__name__, "where": "email.warmup"},
                )
            self.wakeup.wait(TICK_SECONDS)
            self.wakeup.clear()

    def tick(self) -> None:
        if self.detect_requested:
            self.detect_requested = False
            self.detect_addresses()
        self.send_next()
        self.handle_mailboxes()
        self.handle_partners()

    # ---------- settings ----------

    def update(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            account = session.get(EmailAccount, int(params.get("id") or 0))
            if account is None:
                raise UserError("Ящик не найден.")
            if "warm_target" in params:
                try:
                    target = int(params["warm_target"])
                except (TypeError, ValueError) as error:
                    raise UserError("Цель прогрева — число писем в день.") from error
                if not 1 <= target <= WARM_MAX:
                    raise UserError(f"Прогрев: от 1 до {WARM_MAX} писем в день с ящика.")
                account.warm_target = target
            if "pool_warmup" in params:
                enabled = bool(params["pool_warmup"])
                if enabled and not account.pool_warmup:
                    account.warm_started_at = self.now()
                    account.warm_next_at = None
                account.pool_warmup = enabled
        self._ensure_thread()
        return self.state()

    def _nst_settings(self, session) -> dict:
        row = session.get(Setting, NST_SETTING)
        return dict(row.value) if row is not None and isinstance(row.value, dict) else {}

    def _nst(self) -> NstClient | None:
        with self.sessions() as session:
            stored = self._nst_settings(session)
        if not stored.get("key"):
            return None
        key = secret_box.protect(base64.b64decode(stored["key"]), decrypt=True).decode()
        return self.nst_client(key, stored.get("url") or DEFAULT_URL)

    def nst_save(self, params: dict) -> dict:
        url = str(params.get("url") or DEFAULT_URL).strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise UserError("Адрес API Nstbrowser начинается с http://")
        key = str(params.get("api_key") or "").strip()
        with self.lock, self.sessions.begin() as session:
            stored = self._nst_settings(session)
            stored["url"] = url
            if key:
                stored["key"] = base64.b64encode(secret_box.protect(key.encode())).decode()
            if not stored.get("key"):
                raise UserError("Вставьте API-ключ Nstbrowser.")
            row = session.get(Setting, NST_SETTING)
            if row is None:
                session.add(Setting(key=NST_SETTING, value=stored))
            else:
                row.value = stored
        return self.state()

    def nst_test(self) -> dict:
        client = self._require_nst()
        try:
            count = len(client.profiles())
        except NstError as error:
            raise UserError(error.detail) from error
        self.nst_note = ""
        return {"ok": True, "message": f"Nstbrowser на связи, профилей: {count}."}

    def _require_nst(self) -> NstClient:
        client = self._nst()
        if client is None:
            raise UserError("Сначала сохраните API-ключ Nstbrowser.")
        return client

    def partners_load(self) -> dict:
        """Every Nstbrowser profile becomes a partner; addresses are found separately."""
        client = self._require_nst()
        try:
            profiles = client.profiles()
        except NstError as error:
            raise UserError(error.detail) from error
        with self.lock, self.sessions.begin() as session:
            known = {row.profile_id: row for row in session.scalars(select(EmailWarmupPartner))}
            added = 0
            for profile in profiles:
                row = known.get(profile.id)
                if row is None:
                    session.add(
                        EmailWarmupPartner(profile_id=profile.id, profile_name=profile.name)
                    )
                    added += 1
                else:
                    row.profile_name = profile.name
        return {"added": added, "total": len(profiles), "state": self.state()}

    def partner_update(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            row = session.get(EmailWarmupPartner, int(params.get("id") or 0))
            if row is None:
                raise UserError("Партнёр не найден.")
            if "address" in params:
                value = str(params["address"] or "").strip()
                address = addresses.normalize(value) if value else ""
                if value and not address:
                    raise UserError("Это не похоже на адрес Gmail.")
                own = session.scalar(select(EmailAccount.id).where(EmailAccount.address == address))
                if address and own:
                    raise UserError("Этот адрес уже рабочий ящик: партнёр должен быть другим.")
                row.address = address
                if address and row.status == "new":
                    row.status = "ok"
            if "enabled" in params:
                row.enabled = bool(params["enabled"])
                if row.enabled and row.status == "error":
                    row.status, row.note = "new", ""
        self._ensure_thread()
        return self.state()

    def partner_remove(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            row = session.get(EmailWarmupPartner, int(params.get("id") or 0))
            if row is not None:
                session.delete(row)
        return self.state()

    def request_detect(self) -> dict:
        """Opening the profiles takes minutes: the worker does it and the state shows it."""
        self._require_nst()
        self.detect_requested = True
        self.detect = {"running": True, "done": 0, "total": 0, "found": 0}
        self._ensure_thread()
        return self.state()

    def detect_addresses(self) -> None:
        client = self._nst()
        with self.sessions() as session:
            rows = [
                (row.id, row.profile_id)
                for row in session.scalars(
                    select(EmailWarmupPartner).where(EmailWarmupPartner.address == "")
                )
            ]
        self.detect = {"running": True, "done": 0, "total": len(rows), "found": 0}
        try:
            for partner_id, profile_id in rows:
                if self.stopping.is_set() or client is None:
                    break
                address, note = None, ""
                try:
                    with self.nst_session(client, profile_id) as gmail:
                        address = gmail.address()
                    if not address:
                        note = "Не нашёл адрес Gmail в профиле"
                except NstError as error:
                    note = error.detail
                    if error.kind == "offline":
                        self.nst_note = error.detail
                        break
                except Exception as error:
                    note = f"Не удалось открыть Gmail: {type(error).__name__}"
                with self.lock, self.sessions.begin() as session:
                    row = session.get(EmailWarmupPartner, partner_id)
                    if row is not None:
                        own = address and session.scalar(
                            select(EmailAccount.id).where(EmailAccount.address == address)
                        )
                        if address and not own:
                            row.address, row.status, row.note = address, "ok", ""
                            self.detect["found"] += 1
                        else:
                            row.status = "error"
                            row.note = (note or "Это рабочий ящик, не партнёр")[:300]
                self.detect["done"] += 1
        finally:
            self.detect["running"] = False

    # ---------- sending ----------

    def _zone(self, session) -> str:
        row = session.get(EmailWorkspace, 1)
        return row.window_zone if row is not None else "America/New_York"

    def _sent_last_day(self, session, account_id: int, now: datetime) -> int:
        """First notes of the last 24 hours: the daily goal counts them; answers come on top."""
        return session.scalar(
            select(func.count())
            .select_from(EmailWarmupMessage)
            .where(
                EmailWarmupMessage.sender_account_id == account_id,
                EmailWarmupMessage.depth == 0,
                EmailWarmupMessage.sent_at > now - timedelta(hours=24),
            )
        )

    def _partners_for(self, session, account: EmailAccount) -> list[tuple[str, int, str]]:
        """(kind, id, address) of everyone this mailbox may write to."""
        found = [
            ("mailbox", other.id, other.address)
            for other in session.scalars(
                select(EmailAccount).where(
                    EmailAccount.id != account.id,
                    EmailAccount.pool_warmup.is_(True),
                    EmailAccount.status.in_(("active", "limited", "paused", "bounces")),
                )
            )
        ]
        found += [
            ("partner", partner.id, partner.address)
            for partner in session.scalars(
                select(EmailWarmupPartner).where(
                    EmailWarmupPartner.enabled.is_(True),
                    EmailWarmupPartner.address != "",
                    EmailWarmupPartner.status != "error",
                )
            )
        ]
        return found

    def _pick(self, session, account: EmailAccount, now: datetime):
        candidates = self._partners_for(session, account)
        if not candidates:
            return None
        day = now - timedelta(hours=24)
        counts: dict[tuple[str, int], int] = {}
        for to_account, to_partner, count in session.execute(
            select(EmailWarmupMessage.to_account_id, EmailWarmupMessage.to_partner_id, func.count())
            .where(
                EmailWarmupMessage.sender_account_id == account.id,
                EmailWarmupMessage.depth == 0,
                EmailWarmupMessage.sent_at > day,
            )
            .group_by(EmailWarmupMessage.to_account_id, EmailWarmupMessage.to_partner_id)
        ):
            key = ("mailbox", to_account) if to_account else ("partner", to_partner)
            counts[key] = count

        def used(item) -> int:
            return counts.get(item[:2], 0)

        fewest = min(used(item) for item in candidates)
        return self.rng.choice([item for item in candidates if used(item) == fewest])

    def _spacing(self, zone: str, now: datetime, left: int) -> timedelta:
        hours_left = max(1, WARM_HOURS[1] - local_hour(zone, now))
        average = hours_left * 3600 / max(1, left)
        return timedelta(seconds=max(600, average * self.rng.uniform(0.6, 1.4)))

    def send_next(self) -> bool:
        """One first note from the mailbox that is due the soonest; True if one went out."""
        now = self.now()
        with self.lock, self.sessions.begin() as session:
            zone = self._zone(session)
            if not in_window(zone, WARM_HOURS[0], WARM_HOURS[1], now):
                return False
            chosen = None
            for account in session.scalars(
                select(EmailAccount)
                .where(EmailAccount.pool_warmup.is_(True), EmailAccount.status == "active")
                .order_by(EmailAccount.warm_next_at.is_not(None), EmailAccount.warm_next_at)
            ):
                if account.warm_next_at and as_utc(account.warm_next_at) > now:
                    continue
                left = warm_cap(account, now) - self._sent_last_day(session, account.id, now)
                if left <= 0:
                    account.warm_next_at = now + LONG_WAIT
                    continue
                partner = self._pick(session, account, now)
                if partner is None:
                    account.warm_next_at = now + LONG_WAIT
                    continue
                chosen = (account, partner, left)
                break
            if chosen is None:
                return False
            account, (kind, target, address), left = chosen
            subject, body = warm_texts.note(self.rng, address)
            outgoing = Outgoing(
                sender=account.address,
                sender_name=account.display_name,
                to=address,
                subject=subject,
                body=body,
                unsubscribe=False,
            )
            password = secret_box.protect(account.password_box, decrypt=True).decode()
            account_id = account.id
            account.warm_next_at = now + self._spacing(zone, now, left)
        try:
            message_id = self.transport(account.address, password).send(outgoing)
        except MailError as error:
            self._send_failed(account_id, error)
            return False
        with self.lock, self.sessions.begin() as session:
            session.add(
                EmailWarmupMessage(
                    sender_account_id=account_id,
                    to_account_id=target if kind == "mailbox" else None,
                    to_partner_id=target if kind == "partner" else None,
                    depth=0,
                    subject=subject,
                    message_id=message_id,
                    references=[],
                    sent_at=now,
                    handle_at=now + self._delay(),
                )
            )
        log.info("email_warmup_sent")
        return True

    def _delay(self) -> timedelta:
        return timedelta(minutes=self.rng.uniform(*HANDLE_DELAY_MINUTES))

    def _send_failed(self, account_id: int, error: MailError) -> None:
        if error.kind == "auth":
            self.set_status(account_id, "auth_failed", error.detail)
        elif error.kind == "limited":
            self.set_status(
                account_id,
                "limited",
                "Gmail ограничил отправку, перерыв на сутки",
                self.now() + timedelta(hours=24),
            )
        log.info("email_warmup_send_failed", extra={"reason": error.kind})

    # ---------- the receiving side ----------

    def _reply_subject(self, subject: str) -> str:
        return subject if subject.lower().startswith("re:") else f"Re: {subject}"

    def handle_mailboxes(self) -> int:
        """Notes that reached our mailboxes: found and handled over IMAP."""
        now = self.now()
        with self.sessions() as session:
            due = [
                row.id
                for row in session.scalars(
                    select(EmailWarmupMessage)
                    .where(
                        EmailWarmupMessage.status == "pending",
                        EmailWarmupMessage.to_account_id.is_not(None),
                        EmailWarmupMessage.handle_at <= now,
                    )
                    .order_by(EmailWarmupMessage.handle_at)
                    .limit(MAILBOX_TASKS_PER_TICK)
                )
            ]
        for row_id in due:
            self._handle_mailbox(row_id, now)
        return len(due)

    def _handle_mailbox(self, row_id: int, now: datetime) -> None:
        with self.sessions() as session:
            row = session.get(EmailWarmupMessage, row_id)
            box = session.get(EmailAccount, row.to_account_id)
            if box is None or box.status == "auth_failed":
                self._finish(row_id, "failed", None, "Ящик получателя недоступен")
                return
            box_id, address = box.id, box.address
            password = secret_box.protect(box.password_box, decrypt=True).decode()
            message_id, parent, depth = row.message_id, row.parent_message_id, row.depth
        star = self.rng.random() < STAR_CHANCE
        try:
            result = self.transport(address, password).warm_handle(message_id, parent, star)
        except MailError as error:
            if error.kind == "auth":
                self.set_status(box_id, "auth_failed", error.detail)
            self._retry(row_id, now, error.detail)
            return
        if result["placement"] == "missing":
            self._retry(row_id, now, "Письмо ещё не пришло")
            return
        found = result.get("message")
        answer = depth < 2 and self.rng.random() < REPLY_CHANCE.get(depth, 0)
        self._finish(row_id, "done", result["placement"], "", answered=answer)
        if answer:
            self._answer_from_mailbox(row_id, found.message_id if found else message_id)

    def _answer_from_mailbox(self, row_id: int, replied_id: str | None) -> None:
        now = self.now()
        with self.sessions() as session:
            row = session.get(EmailWarmupMessage, row_id)
            box = session.get(EmailAccount, row.to_account_id)
            if row.sender_account_id:
                other = session.get(EmailAccount, row.sender_account_id)
                to = other.address if other else None
            else:
                other = session.get(EmailWarmupPartner, row.sender_partner_id)
                to = other.address if other else None
            if box is None or not to or not replied_id:
                return
            references = [*row.references, replied_id]
            outgoing = Outgoing(
                sender=box.address,
                sender_name=box.display_name,
                to=to,
                subject=self._reply_subject(row.subject),
                body=warm_texts.reply(self.rng),
                in_reply_to=replied_id,
                references=list(row.references),
                unsubscribe=False,
            )
            password = secret_box.protect(box.password_box, decrypt=True).decode()
            box_id = box.id
            back = dict(
                sender_account_id=box.id,
                to_account_id=row.sender_account_id,
                to_partner_id=row.sender_partner_id,
                depth=row.depth + 1,
                subject=outgoing.subject,
                references=references,
            )
        try:
            message_id = self.transport(box.address, password).send(outgoing)
        except MailError as error:
            self._send_failed(box_id, error)
            return
        with self.lock, self.sessions.begin() as session:
            session.add(
                EmailWarmupMessage(
                    **back,
                    message_id=message_id,
                    sent_at=now,
                    handle_at=now + self._delay(),
                )
            )

    def _retry(self, row_id: int, now: datetime, note: str) -> None:
        with self.lock, self.sessions.begin() as session:
            row = session.get(EmailWarmupMessage, row_id)
            row.attempts += 1
            if row.attempts >= MAX_ATTEMPTS:
                row.status, row.placement = "failed", "missing"
                row.note = f"Не найдено после {row.attempts} попыток: {note}"[:300]
            else:
                row.handle_at, row.note = now + RETRY_MISSING, note[:300]

    def _finish(self, row_id, status, placement, note, answered=False) -> None:
        with self.lock, self.sessions.begin() as session:
            row = session.get(EmailWarmupMessage, row_id)
            row.status, row.placement, row.note = status, placement, note[:300]
            row.answered = answered
            row.attempts += 1

    def handle_partners(self) -> int:
        """One Nstbrowser profile per tick handles every note waiting for it."""
        now = self.now()
        with self.sessions() as session:
            first = session.scalar(
                select(EmailWarmupMessage)
                .where(
                    EmailWarmupMessage.status == "pending",
                    EmailWarmupMessage.to_partner_id.is_not(None),
                    EmailWarmupMessage.handle_at <= now,
                )
                .order_by(EmailWarmupMessage.handle_at)
                .limit(1)
            )
            if first is None:
                return 0
            partner = session.get(EmailWarmupPartner, first.to_partner_id)
            rows = list(
                session.scalars(
                    select(EmailWarmupMessage)
                    .where(
                        EmailWarmupMessage.status == "pending",
                        EmailWarmupMessage.to_partner_id == partner.id,
                        EmailWarmupMessage.handle_at <= now,
                    )
                    .order_by(EmailWarmupMessage.handle_at)
                    .limit(PARTNER_TASKS_PER_SESSION)
                )
            )
            tasks = [(row.id, row.message_id, row.depth) for row in rows]
            partner_id, profile_id = partner.id, partner.profile_id
        client = self._nst()
        if client is None:
            for row_id, _, _ in tasks:
                self._postpone(row_id, now, "Nstbrowser не настроен")
            return 0
        try:
            with self.nst_session(client, profile_id) as gmail:
                for row_id, message_id, depth in tasks:
                    answer = depth < 2 and self.rng.random() < REPLY_CHANCE.get(depth, 0)
                    text = warm_texts.reply(self.rng) if answer else None
                    star = self.rng.random() < STAR_CHANCE
                    try:
                        placement = gmail.handle(message_id or "", star, text)
                    except NstError:
                        raise
                    except Exception as error:
                        self._retry(row_id, now, f"Gmail: {type(error).__name__}")
                        continue
                    if placement == "missing":
                        self._retry(row_id, now, "Письмо ещё не пришло")
                        continue
                    self._finish(row_id, "done", placement, "", answered=bool(text))
                    if text:
                        self._partner_answered(row_id, now)
        except NstError as error:
            if error.kind == "offline":
                self.nst_note = error.detail
                for row_id, _, _ in tasks:
                    self._postpone(row_id, now, error.detail)
                return 0
            with self.lock, self.sessions.begin() as session:
                row = session.get(EmailWarmupPartner, partner_id)
                if row is not None:
                    row.status, row.note = "error", error.detail[:300]
                for row_id, _, _ in tasks:
                    message = session.get(EmailWarmupMessage, row_id)
                    if message is not None and message.status == "pending":
                        message.status, message.note = "failed", error.detail[:300]
            return 0
        self.nst_note = ""
        with self.lock, self.sessions.begin() as session:
            row = session.get(EmailWarmupPartner, partner_id)
            if row is not None:
                row.status, row.note, row.last_used_at = "ok", "", now
        return len(tasks)

    def _postpone(self, row_id: int, now: datetime, note: str) -> None:
        with self.lock, self.sessions.begin() as session:
            row = session.get(EmailWarmupMessage, row_id)
            row.handle_at, row.note = now + RETRY_OFFLINE, note[:300]

    def _partner_answered(self, row_id: int, now: datetime) -> None:
        """The partner's answer went from the browser: our mailbox finds it by In-Reply-To."""
        with self.lock, self.sessions.begin() as session:
            row = session.get(EmailWarmupMessage, row_id)
            session.add(
                EmailWarmupMessage(
                    sender_partner_id=row.to_partner_id,
                    to_account_id=row.sender_account_id,
                    depth=row.depth + 1,
                    subject=self._reply_subject(row.subject),
                    message_id=None,
                    parent_message_id=row.message_id,
                    references=[*row.references, row.message_id],
                    sent_at=now,
                    handle_at=now + self._delay(),
                )
            )

    # ---------- reading ----------

    def addresses(self, session) -> set[str]:
        """Everyone taking part in the warm-up: their mail is never a campaign reply."""
        partners = set(
            session.scalars(
                select(EmailWarmupPartner.address).where(EmailWarmupPartner.address != "")
            )
        )
        boxes = set(session.scalars(select(EmailAccount.address)))
        return partners | boxes

    def state(self) -> dict:
        now = self.now()
        week = now - timedelta(days=7)
        with self.sessions() as session:
            placements = {}
            for account_id, placement, count in session.execute(
                select(
                    EmailWarmupMessage.sender_account_id,
                    EmailWarmupMessage.placement,
                    func.count(),
                )
                .where(
                    EmailWarmupMessage.sender_account_id.is_not(None),
                    EmailWarmupMessage.sent_at > week,
                    EmailWarmupMessage.placement.is_not(None),
                )
                .group_by(EmailWarmupMessage.sender_account_id, EmailWarmupMessage.placement)
            ):
                placements.setdefault(account_id, {})[placement] = count
            accounts = []
            for account in session.scalars(select(EmailAccount).order_by(EmailAccount.id)):
                stats = placements.get(account.id, {})
                inbox, spam = stats.get("inbox", 0), stats.get("spam", 0)
                accounts.append(
                    {
                        "id": account.id,
                        "address": account.address,
                        "status": account.status,
                        "pool_warmup": account.pool_warmup,
                        "warm_target": account.warm_target,
                        "cap_today": warm_cap(account, now) if account.pool_warmup else 0,
                        "sent_24h": self._sent_last_day(session, account.id, now),
                        "next_at": iso(account.warm_next_at) if account.pool_warmup else None,
                        "started_at": iso(account.warm_started_at),
                        "inbox_7d": inbox,
                        "spam_7d": spam,
                        "missing_7d": stats.get("missing", 0),
                        "inbox_rate": round(100 * inbox / (inbox + spam)) if inbox + spam else None,
                    }
                )
            partners = []
            handled = dict(
                session.execute(
                    select(EmailWarmupMessage.to_partner_id, func.count())
                    .where(
                        EmailWarmupMessage.to_partner_id.is_not(None),
                        EmailWarmupMessage.status == "done",
                        EmailWarmupMessage.sent_at > week,
                    )
                    .group_by(EmailWarmupMessage.to_partner_id)
                ).all()
            )
            for partner in session.scalars(
                select(EmailWarmupPartner).order_by(EmailWarmupPartner.id)
            ):
                partners.append(
                    {
                        "id": partner.id,
                        "profile_id": partner.profile_id,
                        "profile_name": partner.profile_name,
                        "address": partner.address,
                        "enabled": partner.enabled,
                        "status": partner.status,
                        "note": partner.note,
                        "last_used_at": iso(partner.last_used_at),
                        "handled_7d": handled.get(partner.id, 0),
                    }
                )
            names = {row.id: row.address for row in session.scalars(select(EmailAccount))}
            partner_names = {
                item["id"]: item["address"] or item["profile_name"] for item in partners
            }
            recent = []
            for row in session.scalars(
                select(EmailWarmupMessage).order_by(EmailWarmupMessage.id.desc()).limit(40)
            ):
                recent.append(
                    {
                        "id": row.id,
                        "at": iso(row.sent_at),
                        "from": names.get(row.sender_account_id)
                        or partner_names.get(row.sender_partner_id, ""),
                        "to": names.get(row.to_account_id)
                        or partner_names.get(row.to_partner_id, ""),
                        "depth": row.depth,
                        "status": row.status,
                        "placement": row.placement,
                        "answered": row.answered,
                        "note": row.note,
                    }
                )
            pending = session.scalar(
                select(func.count())
                .select_from(EmailWarmupMessage)
                .where(EmailWarmupMessage.status == "pending")
            )
            stored = self._nst_settings(session)
            zone = self._zone(session)
            waiting_partners = session.scalar(
                select(func.count())
                .select_from(EmailWarmupMessage)
                .where(
                    EmailWarmupMessage.status == "pending",
                    EmailWarmupMessage.to_partner_id.is_not(None),
                )
            )
        return {
            "accounts": accounts,
            "partners": partners,
            "recent": recent,
            "pending": pending,
            "pending_partners": waiting_partners,
            "nst": {
                "configured": bool(stored.get("key")),
                "url": stored.get("url") or DEFAULT_URL,
                "note": self.nst_note,
            },
            "detect": dict(self.detect),
            "hours": {
                "zone": zone,
                "start": WARM_HOURS[0],
                "end": WARM_HOURS[1],
                "open_now": in_window(zone, WARM_HOURS[0], WARM_HOURS[1], now),
            },
            "limits": {"start": WARM_START, "step": WARM_STEP, "max": WARM_MAX},
        }
