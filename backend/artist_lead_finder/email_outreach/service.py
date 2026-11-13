"""«Почта»: mailboxes, the email list, campaigns and the send queue.

One background thread sends one email at a time: from a mailbox that is active, under
its warm-up limit for 24 hours and past its random pause, inside the campaign's sending
hours. Every few minutes it reads each mailbox over IMAP: a reply stops the follow-ups,
joins the contact's thread and goes to the email CRM, a bounce or «unsubscribe» puts the
address on the suppression list. An email that may have gone out is never sent again.
From the CRM the conversation goes on in the same thread (email.thread / email.reply).
The warm-up of the mailboxes runs in its own thread (warmup.py).
"""

import logging
import random
import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, update

from .. import secret_box
from ..crm import sheets
from ..errors import UserError
from ..models import (
    EmailAccount,
    EmailCampaign,
    EmailJob,
    EmailMessage,
    EmailSuppression,
    EmailWorkspace,
    Lead,
    LeadScoutProfile,
    utcnow,
)
from ..outreach.renderer import LeadVariables, MessageTemplateRenderer, template_errors
from . import addresses
from .clock import ZONE_OFFSETS, as_utc, in_window, local_hour  # noqa: F401
from .clock import iso as _iso
from .mailer import GmailTransport, Incoming, MailError, Outgoing
from .warmup import WarmupService

log = logging.getLogger(__name__)

MAX_RECIPIENTS = 5000
MAX_VARIANTS = 10
MAX_FOLLOWUPS = 3
MAX_ACCOUNTS = 50
MAX_SUBJECT = 200
MAX_BODY = 4000
# Warm-up: the first day allows WARMUP_START emails, each next day WARMUP_STEP more.
WARMUP_START = 5
WARMUP_STEP = 3
GMAIL_DAILY_MAX = 50
MAX_ATTEMPTS = 3
TICK_SECONDS = 5
INBOX_EVERY = timedelta(minutes=10)
# The inbox is read while any email of the last month may still get a reply.
INBOX_HORIZON = timedelta(days=30)
LIMIT_PAUSE = timedelta(hours=24)
# A mailbox stops after this many bounces in 7 days if they are also this share of sends.
BOUNCE_PAUSE_COUNT = 3
BOUNCE_PAUSE_SHARE = 0.05
UNSUBSCRIBE = re.compile(
    r"\b(unsubscribe|remove me|take me off|stop (?:emailing|messaging|sending)|"
    r"do not (?:email|contact)|don'?t (?:email|contact)|отпиш|не пишите)\b",
    re.IGNORECASE,
)
REPLY_STATUS = "Ответил"
ACCOUNT_LABELS = {
    "active": "Работает",
    "paused": "На паузе",
    "auth_failed": "Нет входа",
    "limited": "Ограничение Gmail",
    "bounces": "Много возвратов",
}


def daily_cap(account: EmailAccount, now: datetime) -> int:
    if not account.warmup:
        return account.daily_limit
    days = max(0, (now - as_utc(account.warmup_started_at)).days)
    return min(account.daily_limit, WARMUP_START + WARMUP_STEP * days)


def sent_last_day(session, account_id: int, now: datetime) -> int:
    return session.scalar(
        select(func.count())
        .select_from(EmailJob)
        .where(
            EmailJob.account_id == account_id,
            EmailJob.status.in_(("sent", "uncertain")),
            EmailJob.sent_at > now - timedelta(hours=24),
        )
    )


def lead_variables(name: str, lead: Lead | None, scout: LeadScoutProfile | None):
    if lead is None:
        return LeadVariables(username="", display_name=name)
    return LeadVariables(
        username=lead.username,
        display_name=lead.display_name or name,
        followers=lead.followers or None,
        source_username=scout.source_username if scout else None,
        profile_type=scout.profile_type if scout else None,
    )


class EmailService:
    def __init__(
        self,
        sessions,
        crm_merge: Callable[[str, list[dict]], dict] | None = None,
        transport: Callable[[str, str], GmailTransport] = GmailTransport,
        now: Callable[[], datetime] = utcnow,
        rng: random.Random | None = None,
        **warmup_options,
    ):
        self.sessions = sessions
        self.crm_merge = crm_merge
        self.transport = transport
        self.now = now
        self.rng = rng or random.Random()
        self.renderer = MessageTemplateRenderer(max_length=MAX_BODY + 1000)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.thread: threading.Thread | None = None
        self.inbox_checked_at: datetime | None = None
        # «Проверить ответы» asks the worker for a check of every mailbox; the result of
        # the last check is shown with the state.
        self.inbox_requested = False
        self.last_inbox: dict | None = None
        self.warmup = WarmupService(
            sessions,
            transport=transport,
            now=now,
            rng=self.rng,
            set_status=self._set_status,
            **warmup_options,
        )

    # ---------- RPC ----------

    def call(self, method: str, params: dict):
        if method.startswith("email.warmup_"):
            return self.warmup.call(method, params)
        handlers = {
            "email.state": lambda p: self.state(),
            "email.account_add": self.account_add,
            "email.account_update": self.account_update,
            "email.account_remove": self.account_remove,
            "email.account_control": self.account_control,
            "email.workspace_update": self.workspace_update,
            "email.add_text": self.add_text,
            "email.import_file": self.import_file,
            "email.add_leads": self.add_leads,
            "email.preview": self.preview,
            "email.start": self.start,
            "email.control": self.control,
            "email.check_inbox": lambda p: self.request_inbox_check(),
            "email.campaigns": lambda p: self.campaigns(),
            "email.suppress": self.suppress,
            "email.thread": self.conversation,
            "email.reply": self.reply,
        }
        handler = handlers.get(method)
        if handler is None:
            raise UserError("Неизвестный метод приложения.")
        return handler(params or {})

    # ---------- lifecycle ----------

    def restore(self) -> None:
        """After a restart: sends cut off midway are uncertain; the worker comes back when
        there is something to send or read."""
        with self.lock, self.sessions.begin() as session:
            for job in session.scalars(select(EmailJob).where(EmailJob.status == "sending")):
                job.status = "uncertain"
                job.note = "Приложение закрылось во время отправки: проверьте «Отправленные»"
                self._cancel_followups(session, job)
            busy = session.scalar(
                select(func.count())
                .select_from(EmailAccount)
                .where(EmailAccount.status != "paused")
            )
        if busy:
            self._ensure_thread()
        self.warmup.restore()

    def shutdown(self) -> None:
        self.stopping.set()
        self.wakeup.set()
        self.warmup.shutdown()

    def _ensure_thread(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.wakeup.set()
                return
            self.stopping.clear()
            self.thread = threading.Thread(target=self._run, name="email-outreach", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as error:  # the queue must survive any single failure
                log.warning(
                    "email_tick_failed",
                    extra={"error_type": type(error).__name__, "where": "email.tick"},
                )
            self.wakeup.wait(TICK_SECONDS)
            self.wakeup.clear()

    def tick(self) -> None:
        """One step of the worker: maybe read the inboxes, then send at most one email."""
        now = self.now()
        forced, self.inbox_requested = self.inbox_requested, False
        if forced or self.inbox_checked_at is None or now - self.inbox_checked_at >= INBOX_EVERY:
            self.check_inbox(force=forced)
        self.send_next()

    def request_inbox_check(self) -> dict:
        """The check runs in the worker: reading many mailboxes can outlast an RPC call."""
        self.inbox_requested = True
        self._ensure_thread()
        return self.state()

    # ---------- mailboxes ----------

    def _account(self, session, params: dict) -> EmailAccount:
        account = session.get(EmailAccount, int(params.get("id") or 0))
        if account is None:
            raise UserError("Ящик не найден.")
        return account

    def _password(self, account: EmailAccount) -> str:
        return secret_box.protect(account.password_box, decrypt=True).decode()

    def account_add(self, params: dict) -> dict:
        address = addresses.normalize(str(params.get("address") or ""))
        if not address:
            raise UserError("Введите адрес Gmail, например name@gmail.com.")
        password = re.sub(r"\s+", "", str(params.get("password") or ""))
        if len(password) != 16 or not password.isalpha():
            raise UserError(
                "Нужен пароль приложения Google: 16 букв. Обычный пароль от Gmail не подойдёт."
            )
        limit = self._daily_limit(params.get("daily_limit", 20))
        with self.sessions() as session:
            if session.scalar(select(EmailAccount).where(EmailAccount.address == address)):
                raise UserError("Этот ящик уже добавлен.")
            count = session.scalar(select(func.count()).select_from(EmailAccount))
            if count >= MAX_ACCOUNTS:
                raise UserError(f"Не больше {MAX_ACCOUNTS} ящиков.")
        try:
            self.transport(address, password).verify()
        except MailError as error:
            raise UserError(error.detail) from error
        with self.lock, self.sessions.begin() as session:
            account = EmailAccount(
                address=address,
                display_name=str(params.get("display_name") or "").strip()[:120],
                password_box=secret_box.protect(password.encode()),
                daily_limit=limit,
                warmup=bool(params.get("warmup", True)),
                warmup_started_at=self.now(),
            )
            session.add(account)
        log.info("email_account_added")
        self._ensure_thread()
        return self.state()

    def _daily_limit(self, value) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError) as error:
            raise UserError("Лимит в день — число.") from error
        if not 1 <= limit <= GMAIL_DAILY_MAX:
            raise UserError(f"Лимит в день: от 1 до {GMAIL_DAILY_MAX} писем с одного Gmail.")
        return limit

    def account_update(self, params: dict) -> dict:
        password = re.sub(r"\s+", "", str(params.get("password") or ""))
        if password:
            with self.sessions() as session:
                address = self._account(session, params).address
            try:
                self.transport(address, password).verify()
            except MailError as error:
                raise UserError(error.detail) from error
        with self.lock, self.sessions.begin() as session:
            account = self._account(session, params)
            if "display_name" in params:
                account.display_name = str(params["display_name"] or "").strip()[:120]
            if "daily_limit" in params:
                account.daily_limit = self._daily_limit(params["daily_limit"])
            if "warmup" in params:
                if params["warmup"] and not account.warmup:
                    account.warmup_started_at = self.now()
                account.warmup = bool(params["warmup"])
            if password:
                account.password_box = secret_box.protect(password.encode())
                if account.status == "auth_failed":
                    account.status, account.reason = "active", ""
        self._ensure_thread()
        return self.state()

    def account_remove(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            account = self._account(session, params)
            # Follow-ups of its threads cannot go from another mailbox.
            for job in session.scalars(
                select(EmailJob).where(
                    EmailJob.account_id == account.id, EmailJob.status == "pending"
                )
            ):
                job.status, job.note = "cancelled", "Ящик удалён"
            session.delete(account)
        return self.state()

    def account_control(self, params: dict) -> dict:
        action = params.get("action")
        if action == "test":
            with self.sessions() as session:
                account = self._account(session, params)
                address, password = account.address, self._password(account)
            try:
                self.transport(address, password).verify()
            except MailError as error:
                if error.kind == "auth":
                    self._set_status(int(params["id"]), "auth_failed", error.detail)
                raise UserError(error.detail) from error
            return {"ok": True, "message": "Gmail принял вход: SMTP и IMAP работают."}
        if action not in ("pause", "resume"):
            raise UserError("Неизвестное действие.")
        with self.lock, self.sessions.begin() as session:
            account = self._account(session, params)
            if action == "pause":
                account.status, account.reason = "paused", ""
            else:
                account.status, account.reason, account.until = "active", "", None
        self._ensure_thread()
        return self.state()

    def _set_status(self, account_id: int, status: str, reason: str, until=None) -> None:
        with self.lock, self.sessions.begin() as session:
            account = session.get(EmailAccount, account_id)
            if account is not None:
                account.status, account.reason, account.until = status, reason[:300], until
        log.info("email_account_status", extra={"reason": status})

    # ---------- the list ----------

    def _workspace(self, session) -> EmailWorkspace:
        row = session.get(EmailWorkspace, 1)
        if row is None:
            row = EmailWorkspace(id=1)
            session.add(row)
            session.flush()
        return row

    def workspace_update(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            row = self._workspace(session)
            if "recipients" in params:
                row.recipients = self._clean_recipients(params["recipients"] or [])
            if "subjects" in params:
                row.subjects = self._variants(params["subjects"], MAX_SUBJECT, "Тема")
            if "bodies" in params:
                row.bodies = self._variants(params["bodies"], MAX_BODY, "Текст")
            if "followups" in params:
                row.followups = self._followups(params["followups"] or [])
            if "signature" in params:
                signature = str(params["signature"] or "").strip()
                if len(signature) > 1000:
                    raise UserError("Подпись длиннее 1000 символов.")
                row.signature = signature
            if "interval_min_seconds" in params or "interval_max_seconds" in params:
                low = int(params.get("interval_min_seconds", row.interval_min_seconds))
                high = int(params.get("interval_max_seconds", row.interval_max_seconds))
                if not 60 <= low <= high <= 3600:
                    raise UserError("Пауза между письмами: от 60 до 3600 секунд, «от» ≤ «до».")
                row.interval_min_seconds, row.interval_max_seconds = low, high
            if "window_zone" in params:
                zone = str(params["window_zone"])
                if zone not in ZONE_OFFSETS:
                    raise UserError("Неизвестный часовой пояс.")
                row.window_zone = zone
            if "window_start_hour" in params or "window_end_hour" in params:
                start = int(params.get("window_start_hour", row.window_start_hour))
                end = int(params.get("window_end_hour", row.window_end_hour))
                if not 0 <= start < end <= 24:
                    raise UserError("Часы отправки: начало раньше конца, от 0 до 24.")
                row.window_start_hour, row.window_end_hour = start, end
            if "skip_previously_emailed" in params:
                row.skip_previously_emailed = bool(params["skip_previously_emailed"])
        return self.state()

    def _clean_recipients(self, items: list) -> list[dict]:
        seen: set[str] = set()
        cleaned = []
        for item in items:
            address = addresses.normalize(str(item.get("email") or ""))
            if not address or address in seen:
                continue
            seen.add(address)
            lead_id = item.get("lead_id")
            cleaned.append(
                {
                    "email": address,
                    "name": str(item.get("name") or "").strip()[:160],
                    "lead_id": int(lead_id) if lead_id else None,
                }
            )
        if len(cleaned) > MAX_RECIPIENTS:
            raise UserError(f"В списке не больше {MAX_RECIPIENTS} адресов.")
        return cleaned

    def _variants(self, values, limit: int, label: str) -> list[str]:
        variants = [str(value).strip() for value in values or [] if str(value).strip()]
        if len(variants) > MAX_VARIANTS:
            raise UserError(f"{label}: не больше {MAX_VARIANTS} вариантов.")
        for value in variants:
            if len(value) > limit:
                raise UserError(f"{label} длиннее {limit} символов.")
            errors = template_errors(value)
            if errors:
                raise UserError(f"{label}: {errors[0]}")
        return variants

    def _followups(self, values: list) -> list[dict]:
        if len(values) > MAX_FOLLOWUPS:
            raise UserError(f"Не больше {MAX_FOLLOWUPS} фоллоу-апов.")
        cleaned = []
        for value in values:
            days = int(value.get("days") or 0)
            body = str(value.get("body") or "").strip()
            if not 1 <= days <= 30:
                raise UserError("Фоллоу-ап: через 1–30 дней после прошлого письма.")
            if not body:
                raise UserError("Фоллоу-ап без текста.")
            self._variants([body], MAX_BODY, "Фоллоу-ап")
            cleaned.append({"days": days, "body": body})
        return cleaned

    def _add(self, incoming: list[dict]) -> dict:
        with self.lock, self.sessions.begin() as session:
            row = self._workspace(session)
            known = {item["email"] for item in row.recipients}
            blocked = self._blocked(session, [item["email"] for item in incoming], row)
            added = skipped = 0
            recipients = list(row.recipients)
            for item in incoming:
                address = addresses.normalize(item.get("email") or "")
                if not address or address in known:
                    continue
                if address in blocked:
                    skipped += 1
                    continue
                known.add(address)
                recipients.append(
                    {
                        "email": address,
                        "name": (item.get("name") or "")[:160],
                        "lead_id": item.get("lead_id"),
                    }
                )
                added += 1
            if len(recipients) > MAX_RECIPIENTS:
                raise UserError(f"В списке не больше {MAX_RECIPIENTS} адресов.")
            row.recipients = recipients
        return {"added": added, "skipped": skipped, "state": self.state()}

    def _blocked(self, session, emails: list[str], row: EmailWorkspace) -> dict[str, str]:
        """Addresses that will not get a first email, with the reason."""
        emails = [value for value in emails if value]
        blocked: dict[str, str] = {}
        for chunk in range(0, len(emails), 500):
            part = emails[chunk : chunk + 500]
            for item in session.scalars(
                select(EmailSuppression).where(EmailSuppression.address.in_(part))
            ):
                blocked[item.address] = SUPPRESSION_LABELS.get(item.reason, "В стоп-листе")
            replied = select(EmailJob.address).where(
                EmailJob.address.in_(part), EmailJob.replied_at.is_not(None)
            )
            for address in session.scalars(replied):
                blocked.setdefault(address, "Уже ответил")
            if row.skip_previously_emailed:
                emailed = select(EmailJob.address).where(
                    EmailJob.address.in_(part),
                    EmailJob.status.in_(("sent", "uncertain", "sending", "pending")),
                    EmailJob.step == 0,
                )
                for address in session.scalars(emailed):
                    blocked.setdefault(address, "Уже писали или стоит в очереди")
        return blocked

    def add_values(self, values: list[str]) -> int:
        """«Написать» in the email CRM: the addresses join the list of the next campaign."""
        return self._add([{"email": value} for value in values])["added"]

    def add_text(self, params: dict) -> dict:
        found = addresses.from_text(str(params.get("text") or ""))
        if not found:
            raise UserError("Не нашёл ни одного email в тексте.")
        return self._add(found)

    def import_file(self, params: dict) -> dict:
        rows = sheets.read_table(Path(str(params.get("path") or "")))
        found = addresses.from_table(rows)
        if not found:
            raise UserError("В файле нет email-адресов: нужна колонка «email».")
        return self._add(found)

    def add_leads(self, params: dict) -> dict:
        """Emails from the leads' bios: the given leads, or every lead that has one."""
        with self.sessions() as session:
            query = (
                select(Lead, LeadScoutProfile)
                .join(LeadScoutProfile, LeadScoutProfile.lead_id == Lead.id)
                .where(Lead.do_not_contact.is_(False))
            )
            ids = [int(value) for value in params.get("lead_ids") or []]
            if ids:
                query = query.where(Lead.id.in_(ids))
            elif not params.get("all"):
                raise UserError("Выберите артистов.")
            incoming = []
            for lead, scout in session.execute(query):
                for value in scout.emails or []:
                    incoming.append(
                        {"email": value, "name": lead.display_name or "", "lead_id": lead.id}
                    )
                    break
        if not incoming:
            raise UserError("У выбранных артистов нет email в биографии.")
        return self._add(incoming)

    # ---------- campaigns ----------

    def _render(self, template: str, variables: LeadVariables) -> str:
        result = self.renderer.render(template, variables)
        if not result.valid:
            raise UserError(result.errors[0])
        return result.text

    def _compose(self, body: str, signature: str, variables: LeadVariables) -> str:
        text = self._render(body, variables)
        if signature:
            text = f"{text}\n\n{self._render(signature, variables)}"
        return text

    def preview(self, params: dict) -> dict:
        with self.sessions() as session:
            row = self._workspace(session)
            if not row.subjects or not row.bodies:
                raise UserError("Добавьте хотя бы одну тему и один текст.")
            index = int(params.get("index") or 0)
            item = row.recipients[index] if index < len(row.recipients) else {"name": "Jay"}
            lead = session.get(Lead, item["lead_id"]) if item.get("lead_id") else None
            scout = session.get(LeadScoutProfile, lead.id) if lead else None
            variables = lead_variables(item.get("name") or "", lead, scout)
            return {
                "to": item.get("email") or "artist@example.com",
                "subject": self._render(row.subjects[index % len(row.subjects)], variables),
                "body": self._compose(
                    row.bodies[index % len(row.bodies)], row.signature, variables
                ),
                "followups": [
                    {
                        "days": step["days"],
                        "body": self._compose(step["body"], row.signature, variables),
                    }
                    for step in row.followups
                ],
            }

    def start(self, params: dict) -> dict:
        now = self.now()
        with self.lock, self.sessions.begin() as session:
            row = self._workspace(session)
            if not row.recipients:
                raise UserError("Список адресов пуст.")
            if not row.subjects or not row.bodies:
                raise UserError("Добавьте хотя бы одну тему и один текст письма.")
            active = session.scalar(
                select(func.count())
                .select_from(EmailAccount)
                .where(EmailAccount.status.in_(("active", "limited")))
            )
            if not active:
                raise UserError("Нет рабочих ящиков: добавьте Gmail в «Ящики».")
            blocked = self._blocked(session, [item["email"] for item in row.recipients], row)
            campaign = EmailCampaign(
                followups=list(row.followups),
                signature=row.signature,
                interval_min_seconds=row.interval_min_seconds,
                interval_max_seconds=row.interval_max_seconds,
                window_zone=row.window_zone,
                window_start_hour=row.window_start_hour,
                window_end_hour=row.window_end_hour,
            )
            session.add(campaign)
            session.flush()
            skipped = []
            queued = 0
            for item in row.recipients:
                address = item["email"]
                lead = session.get(Lead, item["lead_id"]) if item.get("lead_id") else None
                if address in blocked:
                    skipped.append({"email": address, "reason": blocked[address]})
                    continue
                if lead is not None and lead.do_not_contact:
                    skipped.append({"email": address, "reason": "Отмечен «Не связываться»"})
                    continue
                scout = session.get(LeadScoutProfile, lead.id) if lead else None
                variables = lead_variables(item.get("name") or "", lead, scout)
                subject = self._render(row.subjects[queued % len(row.subjects)], variables)
                body = self._compose(row.bodies[queued % len(row.bodies)], row.signature, variables)
                session.add(
                    EmailJob(
                        campaign_id=campaign.id,
                        position=queued,
                        step=0,
                        address=address,
                        name=item.get("name") or "",
                        lead_id=lead.id if lead else None,
                        subject=subject,
                        body=body,
                        due_at=now,
                    )
                )
                reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
                for index, step in enumerate(row.followups, start=1):
                    session.add(
                        EmailJob(
                            campaign_id=campaign.id,
                            position=queued,
                            step=index,
                            address=address,
                            name=item.get("name") or "",
                            lead_id=lead.id if lead else None,
                            subject=reply_subject,
                            body=self._compose(step["body"], row.signature, variables),
                        )
                    )
                queued += 1
            if not queued:
                raise UserError(
                    "Некому писать: все адреса уже в работе, ответили или в стоп-листе."
                )
            campaign.total = queued
            row.recipients = []
            campaign_id = campaign.id
        log.info("email_campaign_started", extra={"campaign_id": campaign_id})
        self._ensure_thread()
        return {"campaign_id": campaign_id, "queued": queued, "skipped": skipped}

    def control(self, params: dict) -> dict:
        action = params.get("action")
        with self.lock, self.sessions.begin() as session:
            campaign = session.get(EmailCampaign, int(params.get("campaign_id") or 0))
            if campaign is None:
                raise UserError("Кампания не найдена.")
            if campaign.status in ("stopped", "finished"):
                raise UserError("Кампания уже завершена.")
            if action == "pause":
                campaign.status = "paused"
            elif action == "resume":
                campaign.status = "running"
            elif action == "stop":
                campaign.status, campaign.finished_at = "stopped", self.now()
                session.execute(
                    update(EmailJob)
                    .where(EmailJob.campaign_id == campaign.id, EmailJob.status == "pending")
                    .values(status="cancelled", note="Кампания остановлена")
                )
            else:
                raise UserError("Неизвестное действие.")
        self._ensure_thread()
        return self.state()

    def suppress(self, params: dict) -> dict:
        found = addresses.from_text(str(params.get("text") or ""))
        with self.lock, self.sessions.begin() as session:
            for item in found:
                self._suppress(session, item["email"], "manual")
        return self.state()

    def _suppress(self, session, address: str, reason: str) -> None:
        if session.get(EmailSuppression, address) is None:
            session.add(EmailSuppression(address=address, reason=reason, created_at=self.now()))
        for job in session.scalars(
            select(EmailJob).where(EmailJob.address == address, EmailJob.status == "pending")
        ):
            job.status, job.note = "cancelled", SUPPRESSION_LABELS.get(reason, "В стоп-листе")

    # ---------- sending ----------

    def _available(self, session, now: datetime) -> list[EmailAccount]:
        """Mailboxes that may send right now, the least used first."""
        ready = []
        for account in session.scalars(select(EmailAccount).order_by(EmailAccount.id)):
            if account.status == "limited" and account.until and as_utc(account.until) <= now:
                account.status, account.reason, account.until = "active", "", None
            if account.status != "active":
                continue
            if account.next_send_at and as_utc(account.next_send_at) > now:
                continue
            used = sent_last_day(session, account.id, now)
            if used < daily_cap(account, now):
                ready.append((used, account.id, account))
        return [account for _, _, account in sorted(ready, key=lambda item: item[:2])]

    def send_next(self) -> bool:
        """Sends one due email if a mailbox can; True when something was sent or failed."""
        now = self.now()
        with self.lock, self.sessions.begin() as session:
            accounts = self._available(session, now)
            if not accounts:
                self._finish_campaigns(session, now)
                return False
            ready_ids = {account.id for account in accounts}
            due = session.execute(
                select(EmailJob, EmailCampaign)
                .join(EmailCampaign, EmailCampaign.id == EmailJob.campaign_id)
                .where(
                    EmailCampaign.status == "running",
                    EmailJob.status == "pending",
                    EmailJob.due_at.is_not(None),
                    EmailJob.due_at <= now,
                )
                .order_by(EmailJob.due_at, EmailJob.campaign_id, EmailJob.position)
                .limit(200)
            ).all()
            chosen = None
            for job, campaign in due:
                if not in_window(
                    campaign.window_zone, campaign.window_start_hour, campaign.window_end_hour, now
                ):
                    continue
                reason = self._stop_reason(session, job)
                if reason:
                    job.status, job.note = "skipped", reason
                    self._cancel_followups(session, job)
                    continue
                if job.step > 0:
                    if job.account_id not in ready_ids:
                        continue
                    account = next(item for item in accounts if item.id == job.account_id)
                else:
                    account = accounts[0]
                chosen = (job, campaign, account)
                break
            if chosen is None:
                self._finish_campaigns(session, now)
                return False
            job, campaign, account = chosen
            job.status, job.account_id = "sending", account.id
            job.attempts += 1
            thread = self._thread_of(session, job)
            outgoing = Outgoing(
                sender=account.address,
                sender_name=account.display_name,
                to=job.address,
                subject=job.subject,
                body=job.body,
                in_reply_to=thread[-1] if thread else None,
                references=thread[:-1],
            )
            password = self._password(account)
            job_id, account_id = job.id, account.id
            pause = self.rng.uniform(campaign.interval_min_seconds, campaign.interval_max_seconds)
        try:
            message_id = self.transport(outgoing.sender, password).send(outgoing)
        except MailError as error:
            self._failed(job_id, account_id, error)
            return True
        with self.lock, self.sessions.begin() as session:
            job = session.get(EmailJob, job_id)
            job.status, job.message_id, job.sent_at, job.note = "sent", message_id, now, ""
            account = session.get(EmailAccount, account_id)
            if account is not None:
                account.last_sent_at = now
                account.next_send_at = now + timedelta(seconds=pause)
            campaign = session.get(EmailCampaign, job.campaign_id)
            followup = session.scalar(
                select(EmailJob).where(
                    EmailJob.campaign_id == job.campaign_id,
                    EmailJob.address == job.address,
                    EmailJob.step == job.step + 1,
                    EmailJob.status == "pending",
                )
            )
            if followup is not None:
                days = campaign.followups[job.step]["days"]
                followup.due_at = now + timedelta(days=days)
                followup.account_id = account_id
        log.info("email_sent", extra={"campaign_id": campaign.id, "recipient_id": job_id})
        return True

    def _thread_of(self, session, job: EmailJob) -> list[str]:
        """Message-IDs of the earlier emails of this thread, oldest first."""
        if job.step == 0:
            return []
        return list(
            session.scalars(
                select(EmailJob.message_id)
                .where(
                    EmailJob.campaign_id == job.campaign_id,
                    EmailJob.address == job.address,
                    EmailJob.step < job.step,
                    EmailJob.message_id.is_not(None),
                )
                .order_by(EmailJob.step)
            )
        )

    def _stop_reason(self, session, job: EmailJob) -> str | None:
        """Checked again right before a send: the state may have changed since queueing."""
        suppressed = session.get(EmailSuppression, job.address)
        if suppressed is not None:
            return SUPPRESSION_LABELS.get(suppressed.reason, "В стоп-листе")
        replied = select(EmailJob.id).where(
            EmailJob.address == job.address, EmailJob.replied_at.is_not(None)
        )
        if session.scalar(replied.limit(1)) is not None:
            return "Уже ответил"
        if job.lead_id:
            lead = session.get(Lead, job.lead_id)
            if lead is not None and lead.do_not_contact:
                return "Отмечен «Не связываться»"
        return None

    def _failed(self, job_id: int, account_id: int, error: MailError) -> None:
        now = self.now()
        with self.lock, self.sessions.begin() as session:
            job = session.get(EmailJob, job_id)
            account = session.get(EmailAccount, account_id)
            if error.kind in ("auth", "limited"):
                # The mailbox, not the address: the email waits for it.
                job.status, job.note = "pending", error.detail[:300]
                job.attempts -= 1
                if account is not None:
                    if error.kind == "auth":
                        account.status, account.reason = "auth_failed", error.detail[:300]
                    else:
                        account.status = "limited"
                        account.reason = "Gmail ограничил отправку, перерыв на сутки"
                        account.until = now + LIMIT_PAUSE
            elif error.kind == "temporary" and job.attempts < MAX_ATTEMPTS:
                job.status, job.note = "pending", error.detail[:300]
                job.due_at = now + timedelta(minutes=5 * job.attempts)
                if account is not None:
                    account.next_send_at = now + timedelta(seconds=60)
            elif error.kind == "uncertain":
                job.status, job.note, job.sent_at = "uncertain", error.detail[:300], now
                self._cancel_followups(session, job)
            else:
                job.status = "failed"
                job.note = ("Адрес не существует" if error.kind == "recipient" else error.detail)[
                    :300
                ]
                if error.kind == "recipient":
                    self._suppress(session, job.address, "invalid")
                self._cancel_followups(session, job)
        log.info("email_send_failed", extra={"reason": error.kind, "recipient_id": job_id})

    def _cancel_followups(self, session, job: EmailJob, note: str | None = None) -> None:
        for later in session.scalars(
            select(EmailJob).where(
                EmailJob.campaign_id == job.campaign_id,
                EmailJob.address == job.address,
                EmailJob.step > job.step,
                EmailJob.status == "pending",
            )
        ):
            later.status = "cancelled"
            later.note = note or "Прошлое письмо не ушло"

    def _finish_campaigns(self, session, now: datetime) -> None:
        for campaign in session.scalars(
            select(EmailCampaign).where(EmailCampaign.status.in_(("running", "paused")))
        ):
            left = session.scalar(
                select(func.count())
                .select_from(EmailJob)
                .where(
                    EmailJob.campaign_id == campaign.id,
                    EmailJob.status.in_(("pending", "sending")),
                )
            )
            if not left:
                campaign.status, campaign.finished_at = "finished", now

    # ---------- inbox ----------

    def check_inbox(self, force: bool = False) -> dict:
        """Reads new mail of every mailbox that sent in the last month."""
        now = self.now()
        self.inbox_checked_at = now
        with self.sessions() as session:
            accounts = [
                (account.id, account.address, self._password(account), account)
                for account in session.scalars(
                    select(EmailAccount).where(EmailAccount.status != "auth_failed")
                )
                if force
                or (account.last_sent_at and as_utc(account.last_sent_at) > now - INBOX_HORIZON)
            ]
        totals = {"replies": 0, "bounces": 0, "unsubscribed": 0, "errors": []}
        for account_id, address, password, account in accounts:
            since = as_utc(account.created_at) - timedelta(days=1)
            try:
                validity, messages = self.transport(address, password).fetch(
                    account.imap_uidvalidity, account.imap_last_uid, since
                )
            except MailError as error:
                if error.kind == "auth":
                    self._set_status(account_id, "auth_failed", error.detail)
                totals["errors"].append(f"{address}: {error.detail}")
                continue
            # CRM contacts are written after this transaction: the CRM opens its own.
            to_crm: list[dict] = []
            with self.lock, self.sessions.begin() as session:
                skip = self.warmup.addresses(session)
                for message in messages:
                    kind = self._read(session, account_id, message, now, skip, to_crm)
                    if kind:
                        totals[kind] += 1
                row = session.get(EmailAccount, account_id)
                if row is not None:
                    row.imap_uidvalidity = validity
                    if messages:
                        row.imap_last_uid = max(
                            row.imap_last_uid, max(item.uid for item in messages)
                        )
                    row.checked_at = now
                    self._check_bounces(session, row, now)
            if to_crm:
                self._crm(to_crm)
        self.last_inbox = {**totals, "at": _iso(now)}
        return totals

    def _read(
        self,
        session,
        account_id: int,
        message: Incoming,
        now: datetime,
        skip: set[str],
        to_crm: list[dict],
    ) -> str | None:
        if message.sender in skip:
            # Warm-up mail between our mailboxes and partners, handled by the warm-up.
            return None
        if message.bounce:
            hit = False
            for address in message.failed_recipients:
                jobs = list(
                    session.scalars(
                        select(EmailJob).where(
                            EmailJob.address == address,
                            EmailJob.account_id == account_id,
                            EmailJob.status == "sent",
                        )
                    )
                )
                for job in jobs:
                    job.bounced_at = job.bounced_at or now
                    job.note = "Возврат: адрес не принимает почту"
                    self._cancel_followups(session, job, "Возврат")
                if jobs:
                    self._suppress(session, address, "bounced")
                    hit = True
            return "bounces" if hit else None
        if message.auto_reply:
            return None
        ours = select(EmailJob).where(
            EmailJob.account_id == account_id, EmailJob.status.in_(("sent", "uncertain"))
        )
        refs = [ref for ref in [message.in_reply_to, *message.references] if ref]
        job = None
        if refs:
            job = session.scalar(ours.where(EmailJob.message_id.in_(refs)).limit(1))
        if job is None:
            job = session.scalar(
                ours.where(EmailJob.address == message.sender).order_by(EmailJob.id.desc()).limit(1)
            )
        reply = message.text.split("\nOn ", 1)[0][:4000]
        if job is None:
            # An answer to a message written from the CRM.
            answered = select(EmailMessage).where(
                EmailMessage.account_id == account_id, EmailMessage.direction == "out"
            )
            earlier = None
            if refs:
                earlier = session.scalar(answered.where(EmailMessage.message_id.in_(refs)).limit(1))
            if earlier is None:
                earlier = session.scalar(
                    answered.where(EmailMessage.address == message.sender).limit(1)
                )
            if earlier is None:
                return None
            if self._remember(session, earlier.address, account_id, message, reply, now):
                to_crm.append(self._touch(earlier.address, now))
            return "replies"
        first = session.scalar(
            select(EmailJob).where(
                EmailJob.campaign_id == job.campaign_id,
                EmailJob.address == job.address,
                EmailJob.step == 0,
            )
        )
        already = first is not None and first.replied_at is not None
        for item in session.scalars(
            select(EmailJob).where(
                EmailJob.campaign_id == job.campaign_id, EmailJob.address == job.address
            )
        ):
            if item.status in ("sent", "uncertain") and item.replied_at is None:
                item.replied_at = now
            elif item.status == "pending":
                item.status, item.note = "cancelled", "Ответил"
        fresh = self._remember(session, job.address, account_id, message, reply, now)
        if UNSUBSCRIBE.search(reply) or UNSUBSCRIBE.search(message.subject):
            self._suppress(session, job.address, "unsubscribed")
            return "unsubscribed"
        if not fresh:
            return None
        to_crm.append(
            self._touch(job.address, now) if already else self._contact(session, job, reply, now)
        )
        return "replies"

    def _remember(
        self, session, address: str, account_id: int, message: Incoming, text: str, now
    ) -> bool:
        """Adds the email to the contact's thread; False when it is already there."""
        if message.message_id and session.scalar(
            select(EmailMessage.id).where(EmailMessage.message_id == message.message_id).limit(1)
        ):
            return False
        session.add(
            EmailMessage(
                address=address,
                account_id=account_id,
                direction="in",
                subject=message.subject[:300],
                body=text.strip()[:5000],
                message_id=message.message_id or None,
                sent_at=now,
            )
        )
        return True

    def _crm(self, contacts: list[dict]) -> None:
        if self.crm_merge is None:
            return
        try:
            self.crm_merge("email", contacts)
        except Exception as error:  # the reply is still recorded on the job and the thread
            log.warning("email_crm_failed", extra={"error_type": type(error).__name__})

    @staticmethod
    def _touch(address: str, now: datetime) -> dict:
        """A later email of a known conversation: only the last contact moves."""
        return {
            "name": address,
            "channels": [{"kind": "email", "value": address}],
            "last_contact_at": _naive(now),
        }

    def _contact(self, session, job: EmailJob, reply: str, now: datetime) -> dict:
        channels = [{"kind": "email", "value": job.address}]
        lead = session.get(Lead, job.lead_id) if job.lead_id else None
        if lead is not None and lead.platform == "instagram":
            channels.append({"kind": "instagram", "value": lead.username})
        return {
            "name": job.name or (lead.display_name if lead else "") or job.address,
            "channels": channels,
            "statuses": [REPLY_STATUS],
            "notes": f"Ответ на письмо «{job.subject}»:\n{reply.strip()}"[:5000],
            "last_contact_at": _naive(now),
            "lead_id": job.lead_id,
        }

    # ---------- the thread ----------

    def _history(self, session, address: str) -> list[dict]:
        """Every email with the contact, oldest first: campaign emails and the thread."""
        names = {row.id: row.address for row in session.scalars(select(EmailAccount))}
        items = [
            {
                "direction": "out",
                "kind": "campaign" if job.step == 0 else "followup",
                "subject": job.subject,
                "body": job.body,
                "at": job.sent_at,
                "account_id": job.account_id,
                "account": names.get(job.account_id, ""),
                "message_id": job.message_id,
            }
            for job in session.scalars(
                select(EmailJob).where(
                    EmailJob.address == address,
                    EmailJob.status.in_(("sent", "uncertain")),
                    EmailJob.sent_at.is_not(None),
                )
            )
        ]
        items += [
            {
                "direction": item.direction,
                "kind": "reply" if item.direction == "in" else "manual",
                "subject": item.subject,
                "body": item.body,
                "at": item.sent_at,
                "account_id": item.account_id,
                "account": names.get(item.account_id, ""),
                "message_id": item.message_id,
            }
            for item in session.scalars(select(EmailMessage).where(EmailMessage.address == address))
        ]
        return sorted(items, key=lambda item: as_utc(item["at"]))

    def conversation(self, params: dict) -> dict:
        address = addresses.normalize(str(params.get("address") or ""))
        if not address:
            raise UserError("У контакта нет email.")
        with self.sessions() as session:
            history = self._history(session, address)
            sender = next((item for item in reversed(history) if item["direction"] == "out"), None)
            account = session.get(EmailAccount, sender["account_id"]) if sender else None
            suppressed = session.get(EmailSuppression, address)
            if account is None:
                blocker = (
                    "С этим адресом ещё не было переписки: первое письмо уходит кампанией."
                    if not history
                    else "Ящик, с которого шла переписка, удалён."
                )
            elif account.status == "auth_failed":
                blocker = f"Ящик {account.address} без входа: обновите пароль в «Ящиках»."
            elif suppressed is not None and suppressed.reason in ("bounced", "invalid"):
                blocker = "Адрес не принимает почту."
            else:
                blocker = ""
            return {
                "address": address,
                "from": account.address if account else "",
                "can_reply": not blocker,
                "blocker": blocker,
                "unsubscribed": bool(suppressed and suppressed.reason == "unsubscribed"),
                "messages": [
                    {key: value for key, value in item.items() if key != "message_id"}
                    | {"at": _iso(item["at"])}
                    for item in history
                ],
            }

    def reply(self, params: dict) -> dict:
        """Answers in the same thread from the mailbox the conversation went from."""
        address = addresses.normalize(str(params.get("address") or ""))
        body = str(params.get("body") or "").strip()
        if not body:
            raise UserError("Напишите текст ответа.")
        if len(body) > MAX_BODY:
            raise UserError(f"Ответ длиннее {MAX_BODY} символов.")
        state = self.conversation({"address": address})
        if not state["can_reply"]:
            raise UserError(state["blocker"])
        now = self.now()
        with self.lock, self.sessions.begin() as session:
            history = self._history(session, address)
            sender = next(item for item in reversed(history) if item["direction"] == "out")
            account = session.get(EmailAccount, sender["account_id"])
            ids = [item["message_id"] for item in history if item["message_id"]]
            subject = history[0]["subject"] or "Hey"
            outgoing = Outgoing(
                sender=account.address,
                sender_name=account.display_name,
                to=address,
                subject=subject if subject.lower().startswith("re:") else f"Re: {subject}",
                body=body,
                in_reply_to=ids[-1] if ids else None,
                references=ids[:-1],
                unsubscribe=False,
            )
            password = self._password(account)
            account_id = account.id
            # Written by hand now: queued follow-ups would repeat the conversation.
            for job in session.scalars(
                select(EmailJob).where(EmailJob.address == address, EmailJob.status == "pending")
            ):
                job.status, job.note = "cancelled", "Переписка из CRM"
        try:
            message_id = self.transport(outgoing.sender, password).send(outgoing)
        except MailError as error:
            if error.kind == "auth":
                self._set_status(account_id, "auth_failed", error.detail)
            raise UserError(error.detail) from error
        with self.lock, self.sessions.begin() as session:
            session.add(
                EmailMessage(
                    address=address,
                    account_id=account_id,
                    direction="out",
                    subject=outgoing.subject[:300],
                    body=body,
                    message_id=message_id,
                    sent_at=now,
                )
            )
            account = session.get(EmailAccount, account_id)
            if account is not None:
                account.last_sent_at = now
        self._crm([self._touch(address, now)])
        log.info("email_reply_sent")
        return self.conversation({"address": address})

    def _check_bounces(self, session, account: EmailAccount, now: datetime) -> None:
        week = now - timedelta(days=7)
        sent = session.scalar(
            select(func.count())
            .select_from(EmailJob)
            .where(EmailJob.account_id == account.id, EmailJob.sent_at > week)
        )
        bounced = session.scalar(
            select(func.count())
            .select_from(EmailJob)
            .where(EmailJob.account_id == account.id, EmailJob.bounced_at > week)
        )
        if (
            account.status == "active"
            and bounced >= BOUNCE_PAUSE_COUNT
            and sent
            and bounced / sent >= BOUNCE_PAUSE_SHARE
        ):
            account.status = "bounces"
            account.reason = (
                f"{bounced} возвратов из {sent} писем за неделю. Почистите список адресов "
                "и возобновите ящик."
            )
            log.info("email_account_status", extra={"reason": "bounces"})

    # ---------- reading ----------

    def state(self) -> dict:
        now = self.now()
        with self.sessions() as session:
            row = self._workspace(session)
            accounts = []
            for account in session.scalars(select(EmailAccount).order_by(EmailAccount.id)):
                used = sent_last_day(session, account.id, now)
                accounts.append(
                    {
                        "id": account.id,
                        "address": account.address,
                        "display_name": account.display_name,
                        "status": account.status,
                        "status_label": ACCOUNT_LABELS.get(account.status, account.status),
                        "reason": account.reason,
                        "until": _iso(account.until),
                        "daily_limit": account.daily_limit,
                        "warmup": account.warmup,
                        "cap_today": daily_cap(account, now),
                        "sent_24h": used,
                        "next_send_at": _iso(account.next_send_at),
                        "checked_at": _iso(account.checked_at),
                    }
                )
            campaigns = self._campaign_rows(session, live_only=True)
            jobs = []
            live_ids = [item["id"] for item in campaigns]
            if live_ids:
                for job in session.scalars(
                    select(EmailJob)
                    .where(EmailJob.campaign_id.in_(live_ids))
                    .order_by(EmailJob.campaign_id.desc(), EmailJob.position, EmailJob.step)
                    .limit(1500)
                ):
                    jobs.append(_job_row(job))
            day = now - timedelta(hours=24)
            stats = {
                "sent_24h": session.scalar(
                    select(func.count()).select_from(EmailJob).where(EmailJob.sent_at > day)
                ),
                "replies_24h": session.scalar(
                    select(func.count())
                    .select_from(EmailJob)
                    .where(EmailJob.replied_at > day, EmailJob.step == 0)
                ),
                "sent_total": session.scalar(
                    select(func.count())
                    .select_from(EmailJob)
                    .where(EmailJob.status == "sent", EmailJob.step == 0)
                ),
                "replies_total": session.scalar(
                    select(func.count())
                    .select_from(EmailJob)
                    .where(EmailJob.replied_at.is_not(None), EmailJob.step == 0)
                ),
                "bounces_total": session.scalar(
                    select(func.count())
                    .select_from(EmailJob)
                    .where(EmailJob.bounced_at.is_not(None))
                ),
                "suppressed": session.scalar(select(func.count()).select_from(EmailSuppression)),
            }
            return {
                "accounts": accounts,
                "workspace": {
                    "recipients": row.recipients,
                    "subjects": row.subjects,
                    "bodies": row.bodies,
                    "followups": row.followups,
                    "signature": row.signature,
                    "interval_min_seconds": row.interval_min_seconds,
                    "interval_max_seconds": row.interval_max_seconds,
                    "window_zone": row.window_zone,
                    "window_start_hour": row.window_start_hour,
                    "window_end_hour": row.window_end_hour,
                    "skip_previously_emailed": row.skip_previously_emailed,
                    "window_open_now": in_window(
                        row.window_zone, row.window_start_hour, row.window_end_hour, now
                    ),
                },
                "campaigns": campaigns,
                "jobs": jobs,
                "stats": stats,
                "inbox": self.last_inbox,
                "inbox_pending": self.inbox_requested,
                "zones": list(ZONE_OFFSETS),
            }

    def _campaign_rows(self, session, live_only: bool) -> list[dict]:
        query = select(EmailCampaign).order_by(EmailCampaign.id.desc())
        if live_only:
            query = query.where(EmailCampaign.status.in_(("running", "paused")))
        else:
            query = query.limit(100)
        rows = []
        for campaign in session.scalars(query):
            counts = dict(
                session.execute(
                    select(EmailJob.status, func.count())
                    .where(EmailJob.campaign_id == campaign.id, EmailJob.step == 0)
                    .group_by(EmailJob.status)
                ).all()
            )
            replies = session.scalar(
                select(func.count())
                .select_from(EmailJob)
                .where(
                    EmailJob.campaign_id == campaign.id,
                    EmailJob.step == 0,
                    EmailJob.replied_at.is_not(None),
                )
            )
            bounces = session.scalar(
                select(func.count())
                .select_from(EmailJob)
                .where(EmailJob.campaign_id == campaign.id, EmailJob.bounced_at.is_not(None))
            )
            followups = session.scalar(
                select(func.count())
                .select_from(EmailJob)
                .where(
                    EmailJob.campaign_id == campaign.id,
                    EmailJob.step > 0,
                    EmailJob.status == "sent",
                )
            )
            rows.append(
                {
                    "id": campaign.id,
                    "status": campaign.status,
                    "total": campaign.total,
                    "sent": counts.get("sent", 0),
                    "pending": counts.get("pending", 0) + counts.get("sending", 0),
                    "failed": counts.get("failed", 0) + counts.get("uncertain", 0),
                    "skipped": counts.get("skipped", 0) + counts.get("cancelled", 0),
                    "followups_sent": followups,
                    "replies": replies,
                    "bounces": bounces,
                    "window": f"{campaign.window_start_hour}:00–{campaign.window_end_hour}:00 "
                    f"{campaign.window_zone}",
                    "created_at": _iso(campaign.created_at),
                    "finished_at": _iso(campaign.finished_at),
                }
            )
        return rows

    def campaigns(self) -> dict:
        with self.sessions() as session:
            return {"campaigns": self._campaign_rows(session, live_only=False)}


SUPPRESSION_LABELS = {
    "bounced": "Возврат: адрес не принимает почту",
    "invalid": "Адрес не существует",
    "unsubscribed": "Попросил не писать",
    "manual": "В стоп-листе",
}
JOB_LABELS = {
    "pending": "В очереди",
    "sending": "Отправляется",
    "sent": "Отправлено",
    "failed": "Ошибка",
    "skipped": "Пропущено",
    "uncertain": "Проверьте вручную",
    "cancelled": "Отменено",
}


def _job_row(job: EmailJob) -> dict:
    status = job.status
    label = JOB_LABELS.get(status, status)
    if job.bounced_at:
        status, label = "bounced", "Возврат"
    elif job.replied_at and job.status == "sent":
        status, label = "replied", "Ответил"
    elif job.status == "pending" and job.due_at is None:
        label = "Ждёт прошлое письмо"
    return {
        "id": job.id,
        "campaign_id": job.campaign_id,
        "step": job.step,
        "email": job.address,
        "name": job.name,
        "subject": job.subject,
        "status": status,
        "label": label,
        "note": job.note,
        "due_at": _iso(job.due_at),
        "sent_at": _iso(job.sent_at),
        "account_id": job.account_id,
    }


def _naive(value: datetime) -> datetime:
    """The CRM stores UTC without a zone."""
    return as_utc(value).replace(tzinfo=None)
