"""iMessage queue served to an Apple Shortcut on the user's iPhone.

Two protocols, kept apart:
- legacy (`GET /task`): the whole campaign at once, in the format the original
  "Verse iMessage" Shortcut reads. Its ACK comes right after the text step, so it only
  means that step was passed; attachments and pause are out of the server's reach.
- v2 (`GET /v2/next`): one job per request with the campaign state; the Shortcut
  acknowledges after the text and again after every attachment was handed to Messages.

A job is never handed out again by itself: pending -> issued -> execution_acknowledged.
An issued job whose ACK did not come back may still have been sent, so it becomes
`uncertain` and waits for the user. Nothing here reports delivery: Shortcuts does not.
"""

import hashlib
import hmac
import logging
import re
import secrets
import shutil
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import delete, func, select

from .. import secret_box
from ..models import (
    IMessageAttachment,
    IMessageCampaign,
    IMessageEvent,
    IMessageJob,
    IMessageWorkspace,
    Lead,
    LeadScoutProfile,
)
from .network import deep_link, is_lan_address, lan_addresses, lan_rank
from .phones import normalize_recipient

log = logging.getLogger(__name__)

TOKEN_TTL = timedelta(hours=12)
MAX_RECIPIENTS = 500
MAX_MESSAGE_LENGTH = 2000
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
# Types Messages sends as files; the stored copy is served with this type.
ATTACHMENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".pdf": "application/pdf",
}
# v2: the text, the attachments and a confirmation sheet fit well within this.
V2_ACK_WINDOW = timedelta(minutes=10)
# Legacy: the original waits 20-50 s after each contact; contact k is due by then.
LEGACY_STEP = timedelta(seconds=80)
LEGACY_MARGIN = timedelta(minutes=5)
PAUSED_RETRY_SECONDS = 15
MAX_DELAY_SECONDS = 600
DEFAULT_PORT = 47615
ACTIVE = ("running", "paused")
STATUSES = ("pending", "issued", "execution_acknowledged", "uncertain", "failed")
FILE_ID = re.compile(r"^[0-9a-f]{32}$")
JOB_KEY = re.compile(r"^[0-9]{1,9}-[0-9]{1,4}-[0-9a-f]{8}$")
SHORTCUT_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
MAX_EVENTS = 5000
MAX_VARIANTS = 50
# Substituted with the recipient's number or email.
PLACEHOLDERS = ("{Phone}", "{phone}")


def utc_now() -> datetime:
    """Naive UTC, as SQLite returns it."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def recipients_from(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("Некорректный список получателей.")
    result: dict[str, dict] = {}
    invalid = []
    for item in value:
        phone, message = (
            (item.get("phone"), item.get("message", "")) if isinstance(item, dict) else (item, "")
        )
        normalized = normalize_recipient(phone)
        if normalized is None:
            invalid.append(str(phone)[:24])
            continue
        text = str(message or "").strip()
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Текст для {normalized} длиннее {MAX_MESSAGE_LENGTH} символов.")
        result.setdefault(normalized, {"phone": normalized, "message": text})
    if invalid:
        raise ValueError(
            "Телефон в международном формате (с «+» и кодом страны) или email: "
            + ", ".join(invalid[:3])
        )
    if len(result) > MAX_RECIPIENTS:
        raise ValueError(f"Не больше {MAX_RECIPIENTS} получателей.")
    return list(result.values())


def variants_from(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Некорректный список сообщений.")
    result = [str(item).strip() for item in value if str(item).strip()]
    if len(result) > MAX_VARIANTS:
        raise ValueError(f"Не больше {MAX_VARIANTS} сообщений.")
    for index, text in enumerate(result, start=1):
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Сообщение №{index} длиннее {MAX_MESSAGE_LENGTH} символов.")
    return list(dict.fromkeys(result))


def render(text: str, recipient: str) -> str:
    for placeholder in PLACEHOLDERS:
        text = text.replace(placeholder, recipient)
    return text


def variants(row: IMessageWorkspace) -> list[str]:
    return list(row.messages or ([row.message] if row.message else []))


def texts_for(row: IMessageWorkspace, recipients: list[dict]) -> list[str]:
    """Own text, else the variants in turn; {Phone} becomes the recipient."""
    pool = variants(row)
    return [
        render(item["message"] or (pool[index % len(pool)] if pool else ""), item["phone"])
        for index, item in enumerate(recipients)
    ]


class IMessageService:
    def __init__(self, sessions, data_dir: Path, clock: Callable[[], datetime] = utc_now):
        self.sessions = sessions
        self.files = data_dir / "imessage-attachments"
        self.clock = clock
        # HTTP threads and the RPC loop change the same rows; one lock keeps every
        # transition atomic (issue once, ACK once).
        self.lock = threading.RLock()
        self.server = None
        self.token: str | None = None
        self.token_expires_at: datetime | None = None
        self.last_seen: dict | None = None
        self._rejected_logged: dict[str, datetime] = {}
        self._load_token()

    # ---------- storage helpers ----------

    @staticmethod
    def _row(session) -> IMessageWorkspace:
        row = session.get(IMessageWorkspace, 1)
        if row is None:
            row = IMessageWorkspace(id=1, recipients=[], message="", attachment_ids=[])
            session.add(row)
            session.flush()
        return row

    def _event(self, session, kind: str, detail: str = "", campaign_id=None, job_id=None):
        session.add(
            IMessageEvent(
                campaign_id=campaign_id,
                job_id=job_id,
                type=kind,
                detail=detail[:300],
                created_at=self.clock(),
            )
        )

    @staticmethod
    def _active(session) -> IMessageCampaign | None:
        return session.scalar(
            select(IMessageCampaign)
            .where(IMessageCampaign.status.in_(ACTIVE))
            .order_by(IMessageCampaign.id.desc())
            .limit(1)
        )

    @staticmethod
    def _counts(session, campaign_id: int) -> dict[str, int]:
        rows = session.execute(
            select(IMessageJob.status, func.count())
            .where(IMessageJob.campaign_id == campaign_id)
            .group_by(IMessageJob.status)
        )
        counts = dict.fromkeys(STATUSES, 0)
        counts.update({status: count for status, count in rows})
        return counts

    # ---------- token ----------

    def _load_token(self) -> None:
        with self.sessions.begin() as session:
            row = self._row(session)
            expires = naive(row.token_expires_at)
            if not row.token_box or not expires or expires <= self.clock():
                return
            try:
                self.token = secret_box.protect(row.token_box, decrypt=True).decode()
                self.token_expires_at = expires
            except (ValueError, OSError, UnicodeDecodeError):
                log.warning("imessage_token_unreadable")

    def rotate_token(self, params: dict | None = None) -> dict:
        with self.lock, self.sessions.begin() as session:
            self.token = secrets.token_urlsafe(24)
            self.token_expires_at = self.clock() + TOKEN_TTL
            row = self._row(session)
            try:
                row.token_box = secret_box.protect(self.token.encode())
            except (ValueError, OSError):
                # Without an OS secret store the token lives only until the core restarts.
                row.token_box = None
            row.token_expires_at = self.token_expires_at
            self._event(
                session,
                "token_rotated",
                "Выпущен новый токен моста; старые ссылки больше не работают.",
            )
        return self.state()

    def token_valid(self, value: str | None) -> bool:
        token, expires = self.token, self.token_expires_at
        if not token or not value or not expires or expires <= self.clock():
            return False
        return hmac.compare_digest(value.encode(), token.encode())

    # ---------- bridge server ----------

    def base_url(self) -> str | None:
        if self.server is None:
            return None
        host, port = self.server.address
        return f"http://{host}:{port}"

    def _url(self, path: str, **query) -> str:
        return f"{self.base_url()}{path}?{urlencode({'token': self.token, **query})}"

    def bridge_start(self, params: dict) -> dict:
        from .server import BridgeServer

        with self.lock:
            with self.sessions.begin() as session:
                row = self._row(session)
                host = str(params.get("ip") or self._preferred_ip(row.bind_ip))
                port = int(row.port if params.get("port") is None else params["port"])
            # Loopback with any free port (0) only for the protocol tests.
            loopback = bool(params.get("loopback")) and host == "127.0.0.1"
            if not (is_lan_address(host) or loopback):
                raise ValueError("Выберите адрес компьютера в локальной сети (Wi-Fi или Ethernet).")
            if not (1024 <= port <= 65535 or (loopback and port == 0)):
                raise ValueError("Порт от 1024 до 65535.")
            if not self.token_valid(self.token):
                self.rotate_token()
            self._stop_server()
            try:
                self.server = BridgeServer(self, host, port).start()
            except OSError as error:
                raise ValueError(
                    f"Не удалось открыть порт {port} на {host}: он занят или адрес недоступен."
                ) from error
            with self.sessions.begin() as session:
                row = self._row(session)
                if not loopback:
                    row.bind_ip, row.port, row.bridge_enabled = host, port, True
                self._event(
                    session, "bridge_started", f"Мост включён: {host}:{self.server.address[1]}"
                )
            log.info("imessage_bridge_started")
        return self.state()

    @staticmethod
    def _preferred_ip(saved: str | None) -> str:
        """The saved address unless it is gone or a better LAN address exists (a VPN
        tunnel saved by an earlier version)."""
        addresses = lan_addresses()
        if not addresses:
            return saved or ""
        if saved in addresses and lan_rank(saved) <= lan_rank(addresses[0]):
            return saved
        return addresses[0]

    def add_leads(self, params: dict) -> dict:
        """Leads of the base with the chosen CRM statuses: their first phone, else email."""
        statuses = [str(item) for item in params.get("statuses") or []]
        if not statuses:
            raise ValueError("Выберите статусы CRM.")
        with self.sessions() as session:
            rows = session.execute(
                select(LeadScoutProfile.phones, LeadScoutProfile.emails)
                .join(Lead, Lead.id == LeadScoutProfile.lead_id)
                .where(Lead.status.in_(statuses), Lead.do_not_contact.is_(False))
                .order_by(Lead.id)
            )
            found = []
            for phones, emails in rows:
                contact = next(
                    (
                        value
                        for value in [*(phones or []), *(emails or [])]
                        if normalize_recipient(value)
                    ),
                    None,
                )
                if contact:
                    found.append(normalize_recipient(contact))
        added = self.add_recipients(found)
        return {**self.state(), "added": added}

    def add_recipients(self, values: list[str]) -> int:
        """Appends phones or emails to the list; known and invalid ones are skipped."""
        found = [value for value in map(normalize_recipient, values) if value]
        with self.lock, self.sessions.begin() as session:
            row = self._row(session)
            known = {item["phone"] for item in row.recipients}
            new = [value for value in dict.fromkeys(found) if value not in known]
            if len(known) + len(new) > MAX_RECIPIENTS:
                raise ValueError(f"Не больше {MAX_RECIPIENTS} получателей.")
            row.recipients = [*row.recipients, *({"phone": value, "message": ""} for value in new)]
        return len(new)

    def bridge_stop(self, params: dict | None = None) -> dict:
        with self.lock:
            stopped = self._stop_server()
            with self.sessions.begin() as session:
                self._row(session).bridge_enabled = False
                if stopped:
                    self._event(session, "bridge_stopped", "Мост выключен.")
        return self.state()

    def _stop_server(self) -> bool:
        if self.server is None:
            return False
        self.server.stop()
        self.server = None
        return True

    def restore(self) -> None:
        """After a restart of the core the bridge comes back if it was on.

        The app ships only the signed original Shortcut, so the workspace (and a campaign
        the phone has not touched) moves to its protocol."""
        with self.lock, self.sessions.begin() as session:
            row = self._row(session)
            try:
                self._set_protocol(session, row, "legacy")
            except ValueError:
                pass
            enabled = row.bridge_enabled
        if not enabled:
            return
        try:
            self.bridge_start({})
        except ValueError:
            log.warning("imessage_bridge_restore_failed")

    def shutdown(self) -> None:
        with self.lock:
            self._stop_server()

    def seen(self, ip: str, user_agent: str) -> None:
        now = self.clock()
        previous = self.last_seen
        self.last_seen = {"ip": ip, "at": now, "user_agent": user_agent[:120]}
        if previous is None or previous["ip"] != ip or now - previous["at"] > timedelta(minutes=10):
            with self.lock, self.sessions.begin() as session:
                self._event(session, "phone_connected", f"Запрос с устройства {ip}")

    def rejected(self, ip: str) -> None:
        """A request without a valid token; logged at most once a minute per address."""
        now = self.clock()
        last = self._rejected_logged.get(ip)
        if last and now - last < timedelta(minutes=1):
            return
        self._rejected_logged[ip] = now
        with self.lock, self.sessions.begin() as session:
            self._event(
                session, "request_rejected", f"Запрос с {ip} без действующего токена отклонён."
            )

    # ---------- workspace ----------

    def workspace_update(self, params: dict) -> dict:
        with self.lock, self.sessions.begin() as session:
            row = self._row(session)
            if "recipients" in params:
                row.recipients = recipients_from(params["recipients"])
            if "messages" in params:
                row.messages = variants_from(params["messages"])
                row.message = ""
            if "protocol" in params:
                if params["protocol"] not in ("legacy", "v2"):
                    raise ValueError("Неизвестный протокол.")
                self._set_protocol(session, row, params["protocol"])
            for key in ("shortcut_name", "legacy_shortcut_name"):
                if key in params:
                    name = str(params[key] or "").strip()
                    if not SHORTCUT_NAME.match(name):
                        raise ValueError("Имя Shortcut — от 1 до 80 символов.")
                    setattr(row, key, name)
            if "delay_seconds" in params:
                delay = int(params["delay_seconds"])
                if not 0 <= delay <= MAX_DELAY_SECONDS:
                    raise ValueError(f"Пауза между получателями — от 0 до {MAX_DELAY_SECONDS} с.")
                row.delay_seconds = delay
        return self.state()

    def shortcut_save(self, params: dict) -> dict:
        """Saves the signed original Shortcut; it speaks only the legacy protocol."""
        from . import shortcut

        result = shortcut.save_signed(str(params.get("path") or ""))
        with self.lock, self.sessions.begin() as session:
            row = self._row(session)
            try:
                switched = self._set_protocol(session, row, "legacy")
            except ValueError:
                switched = False
        return {**result, "switched": switched, "state": self.state()}

    def _set_protocol(self, session, row: IMessageWorkspace, protocol: str) -> bool:
        """The Shortcut the phone runs. A running campaign follows it while the phone has
        taken nothing yet: the jobs are the same, only the way they are handed out differs."""
        active = self._active(session)
        changed = row.protocol != protocol
        if active is not None and active.protocol != protocol:
            taken = session.scalar(
                select(func.count())
                .select_from(IMessageJob)
                .where(IMessageJob.campaign_id == active.id)
                .where((IMessageJob.issued_at.is_not(None)) | (IMessageJob.status != "pending"))
            )
            if taken:
                raise ValueError(
                    "iPhone уже взял задания этой рассылки. Остановите её, чтобы сменить команду."
                )
            active.protocol = protocol
            changed = True
        row.protocol = protocol
        if changed:
            label = "исходный Shortcut" if protocol == "legacy" else "новый Shortcut"
            self._event(session, "settings", f"Протокол: {label}", active.id if active else None)
        return changed

    def attachment_add(self, params: dict) -> dict:
        source = Path(str(params.get("path") or ""))
        mime = ATTACHMENT_TYPES.get(source.suffix.lower())
        if mime is None:
            raise ValueError(
                "Поддерживаются фото, видео, аудио и PDF: " + ", ".join(ATTACHMENT_TYPES)
            )
        if not source.is_file():
            raise ValueError("Файл не найден.")
        size = source.stat().st_size
        if size == 0 or size > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"Размер файла — до {MAX_ATTACHMENT_BYTES // (1024 * 1024)} МБ.")
        with self.lock:
            with self.sessions() as session:
                if len(self._row(session).attachment_ids) >= MAX_ATTACHMENTS:
                    raise ValueError(f"Не больше {MAX_ATTACHMENTS} вложений.")
            file_id = secrets.token_hex(16)
            self.files.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            target = self.files / file_id
            with source.open("rb") as reader, target.open("wb") as writer:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    writer.write(chunk)
            with self.sessions.begin() as session:
                session.add(
                    IMessageAttachment(
                        id=file_id,
                        filename=source.name[:200],
                        mime=mime,
                        size=size,
                        sha256=digest.hexdigest(),
                        created_at=self.clock(),
                    )
                )
                row = self._row(session)
                row.attachment_ids = [*row.attachment_ids, file_id]
        return self.state()

    def attachment_remove(self, params: dict) -> dict:
        file_id = str(params.get("id") or "")
        if not FILE_ID.match(file_id):
            raise ValueError("Некорректное вложение.")
        with self.lock, self.sessions.begin() as session:
            active = self._active(session)
            if active and file_id in active.attachment_ids:
                raise ValueError("Вложение используется в текущей рассылке. Сначала остановите её.")
            row = self._row(session)
            row.attachment_ids = [item for item in row.attachment_ids if item != file_id]
            attachment = session.get(IMessageAttachment, file_id)
            if attachment is not None:
                session.delete(attachment)
            (self.files / file_id).unlink(missing_ok=True)
        return self.state()

    def attachment_file(self, file_id: str) -> tuple[Path, IMessageAttachment] | None:
        """Only files of the list or of the running campaign, by id, from the app folder."""
        if not FILE_ID.match(file_id):
            return None
        with self.sessions() as session:
            row = self._row(session)
            active = self._active(session)
            allowed = set(row.attachment_ids) | set(active.attachment_ids if active else [])
            attachment = session.get(IMessageAttachment, file_id)
            if file_id not in allowed or attachment is None:
                return None
            session.expunge(attachment)
        path = self.files / file_id
        return (path, attachment) if path.is_file() else None

    # ---------- campaigns ----------

    def _done_phones(self, session, phones: list[str]) -> set[str]:
        """Numbers a real (non-test) campaign already reached or may have reached."""
        found: set[str] = set()
        for start in range(0, len(phones), 500):
            found.update(
                session.scalars(
                    select(IMessageJob.phone)
                    .join(IMessageCampaign, IMessageCampaign.id == IMessageJob.campaign_id)
                    .where(
                        IMessageCampaign.is_test.is_(False),
                        IMessageJob.phone.in_(phones[start : start + 500]),
                        IMessageJob.status.in_(("issued", "execution_acknowledged", "uncertain")),
                    )
                )
            )
        return found

    def start(self, params: dict) -> dict:
        test_phone = params.get("test_phone")
        with self.lock, self.sessions.begin() as session:
            self._sweep(session)
            if self._active(session) is not None:
                raise ValueError("Рассылка уже идёт. Остановите её или дождитесь конца.")
            row = self._row(session)
            recipients = list(row.recipients)
            skipped = 0
            if test_phone:
                phone = normalize_recipient(test_phone)
                if phone is None:
                    raise ValueError("Для теста — телефон с «+» и кодом страны или email.")
                chosen = next((item for item in recipients if item["phone"] == phone), None)
                recipients = [chosen or {"phone": phone, "message": ""}]
            else:
                done = self._done_phones(session, [item["phone"] for item in recipients])
                skipped = sum(item["phone"] in done for item in recipients)
                recipients = [item for item in recipients if item["phone"] not in done]
            if not recipients:
                raise ValueError(
                    "Некому отправлять: все номера уже получили сообщение или ждут проверки."
                    if skipped
                    else "Добавьте получателей."
                )
            texts = texts_for(row, recipients)
            missing = sum(not text for text in texts)
            if missing:
                raise ValueError(f"Нет текста для {missing} получателей: добавьте сообщение.")
            for file_id in row.attachment_ids:
                if not (self.files / file_id).is_file():
                    raise ValueError(
                        "Файл вложения пропал из папки приложения. Удалите его и добавьте снова."
                    )
            campaign = IMessageCampaign(
                protocol=row.protocol,
                status="running",
                is_test=bool(test_phone),
                message=(variants(row) or [""])[0],
                attachment_ids=list(row.attachment_ids),
                delay_seconds=row.delay_seconds,
                total=len(recipients),
                created_at=self.clock(),
            )
            session.add(campaign)
            session.flush()
            for position, (item, text) in enumerate(zip(recipients, texts), start=1):
                session.add(
                    IMessageJob(
                        campaign_id=campaign.id,
                        key=f"{campaign.id}-{position}-{secrets.token_hex(4)}",
                        position=position,
                        phone=item["phone"],
                        message=text,
                        status="pending",
                    )
                )
            kind = "тест" if test_phone else "рассылка"
            protocol = "исходный Shortcut" if row.protocol == "legacy" else "новый Shortcut"
            self._event(
                session,
                "campaign_started",
                f"Запущена {kind} №{campaign.id}: {len(recipients)} получ., {protocol}"
                + (f"; пропущено уже отправленных: {skipped}" if skipped else ""),
                campaign.id,
            )
            log.info("imessage_campaign_started", extra={"campaign_id": campaign.id})
        return {**self.state(), "skipped": skipped}

    def control(self, params: dict) -> dict:
        action = params.get("action")
        moves = {"pause": ("running", "paused"), "resume": ("paused", "running")}
        with self.lock, self.sessions.begin() as session:
            campaign = self._active(session)
            if campaign is None:
                raise ValueError("Нет активной рассылки.")
            if action == "stop":
                campaign.status = "stopped"
                campaign.finished_at = self.clock()
                self._event(
                    session,
                    "campaign_stopped",
                    "Остановлено. Задание, уже выданное телефону, может выполниться до конца.",
                    campaign.id,
                )
            elif action in moves and campaign.status == moves[action][0]:
                campaign.status = moves[action][1]
                self._event(
                    session,
                    f"campaign_{campaign.status}",
                    "Пауза: телефон не получит новых заданий; текущее выполнится до конца."
                    if action == "pause"
                    else "Продолжено.",
                    campaign.id,
                )
            elif action not in (*moves, "stop"):
                raise ValueError("Неизвестное действие.")
        return self.state()

    def resolve(self, params: dict) -> dict:
        """The user's answer for an uncertain (or failed) job; never automatic."""
        resolution = params.get("resolution")
        with self.lock, self.sessions.begin() as session:
            job = session.get(IMessageJob, int(params.get("job_id") or 0))
            if job is None:
                raise ValueError("Задание не найдено.")
            if job.status not in ("uncertain", "failed"):
                raise ValueError("Решение нужно только для неопределённых и неудачных заданий.")
            campaign = session.get(IMessageCampaign, job.campaign_id)
            now = self.clock()
            if resolution == "sent":
                job.status, job.ack_scope, job.acked_at = "execution_acknowledged", "manual", now
                job.resolution = "manual_sent"
                detail = f"{job.phone}: отмечено вручную как отправленное"
            elif resolution == "not_sent":
                job.status, job.resolution = "failed", "manual_not_sent"
                detail = f"{job.phone}: отмечено вручную как не отправленное"
            elif resolution == "resend":
                if campaign.status == "stopped":
                    raise ValueError("Рассылка остановлена. Запустите новую для этого номера.")
                job.status, job.resolution = "pending", "requeued"
                job.deadline_at = job.text_acked_at = None
                if campaign.status == "finished":
                    # Back in the queue, but nothing goes out until the user resumes.
                    campaign.status, campaign.finished_at = "paused", None
                detail = f"{job.phone}: возвращено в очередь по решению пользователя"
            else:
                raise ValueError("Неизвестное решение.")
            self._event(session, "job_resolved", detail, campaign.id, job.id)
            self._finish_if_done(session, campaign)
        return self.state()

    def _finish_if_done(self, session, campaign: IMessageCampaign) -> None:
        if campaign.status not in ACTIVE:
            return
        counts = self._counts(session, campaign.id)
        if counts["pending"] or counts["issued"]:
            return
        campaign.status = "finished"
        campaign.finished_at = self.clock()
        self._event(
            session,
            "campaign_finished",
            f"Готово: подтверждено {counts['execution_acknowledged']}, неизвестно "
            f"{counts['uncertain']}, не отправлено {counts['failed']}.",
            campaign.id,
        )

    def _sweep(self, session) -> None:
        """Issued jobs past their deadline without an ACK become uncertain."""
        now = self.clock()
        overdue = session.scalars(
            select(IMessageJob).where(IMessageJob.status == "issued", IMessageJob.deadline_at < now)
        )
        for job in overdue:
            self._uncertain(session, job, "Подтверждение не пришло вовремя: сообщение могло уйти.")
        for campaign in session.scalars(
            select(IMessageCampaign).where(IMessageCampaign.status.in_(ACTIVE))
        ):
            self._finish_if_done(session, campaign)

    def _uncertain(self, session, job: IMessageJob, reason: str) -> None:
        job.status = "uncertain"
        stage = " Текст был подтверждён, вложения — нет." if job.text_acked_at else ""
        job.note = (reason + stage)[:300]
        self._event(
            session,
            "job_uncertain",
            f"{job.phone}: {reason}{stage} Проверьте переписку на iPhone.",
            job.campaign_id,
            job.id,
        )

    # ---------- phone protocols ----------

    def _attachments(self, session, campaign: IMessageCampaign) -> list[dict]:
        result = []
        for file_id in campaign.attachment_ids:
            attachment = session.get(IMessageAttachment, file_id)
            if attachment is not None:
                result.append(
                    {
                        "downloadUrl": self._url(f"/attachment/{file_id}"),
                        "name": attachment.filename,
                        "mime": attachment.mime,
                    }
                )
        return result

    def legacy_task(self) -> tuple[int, dict]:
        """GET /task for the original Shortcut: every pending job of the campaign at once."""
        empty = {"message": "", "contacts": [], "attachments": []}
        with self.lock, self.sessions.begin() as session:
            self._sweep(session)
            campaign = self._active(session)
            if campaign is None or campaign.protocol != "legacy" or campaign.status != "running":
                return 200, empty
            jobs = list(
                session.scalars(
                    select(IMessageJob)
                    .where(IMessageJob.campaign_id == campaign.id, IMessageJob.status == "pending")
                    .order_by(IMessageJob.position)
                )
            )
            if not jobs:
                return 200, empty
            now = self.clock()
            contacts = []
            for index, job in enumerate(jobs, start=1):
                job.status, job.attempts = "issued", job.attempts + 1
                job.issued_at = now
                job.deadline_at = now + LEGACY_STEP * index + LEGACY_MARGIN
                contacts.append(
                    {
                        "phone": job.phone,
                        "message": job.message,
                        "ackUrl": self._url("/ack", jobId=job.key),
                    }
                )
            self._event(
                session,
                "jobs_issued",
                f"Исходный Shortcut получил всю очередь: {len(jobs)} получ. Пауза и остановка "
                "на эту выдачу уже не влияют.",
                campaign.id,
            )
            return 200, {
                "message": campaign.message,
                "contacts": contacts,
                "attachments": [
                    {"downloadUrl": item["downloadUrl"]}
                    for item in self._attachments(session, campaign)
                ],
            }

    def _state_of(self, session, campaign: IMessageCampaign | None) -> str:
        if campaign is None:
            latest = session.scalar(
                select(IMessageCampaign.status).order_by(IMessageCampaign.id.desc()).limit(1)
            )
            return "stopped" if latest == "stopped" else "finished"
        if campaign.protocol != "v2":
            return "stopped"
        if campaign.status == "paused":
            return "paused"
        return "ready" if self._counts(session, campaign.id)["pending"] else "finished"

    def v2_status(self) -> tuple[int, dict]:
        with self.lock, self.sessions.begin() as session:
            self._sweep(session)
            campaign = self._active(session)
            state = self._state_of(session, campaign)
            body: dict[str, Any] = {"state": state}
            if state == "paused":
                body["retrySeconds"] = PAUSED_RETRY_SECONDS
            if campaign is not None:
                counts = self._counts(session, campaign.id)
                body.update(campaignId=campaign.id, pending=counts["pending"])
            return 200, body

    def v2_next(self) -> tuple[int, dict]:
        """GET /v2/next: one job, or the state that tells the Shortcut to wait or stop."""
        with self.lock, self.sessions.begin() as session:
            self._sweep(session)
            campaign = self._active(session)
            status_url = self._url("/v2/status")
            if campaign is not None and campaign.protocol != "v2":
                return 409, {
                    "state": "stopped",
                    "error": "Текущая рассылка создана для исходного Shortcut.",
                }
            if campaign is not None:
                # The Shortcut asks again only after acknowledging its job; a job still
                # issued here lost its ACK and may have been sent.
                for job in session.scalars(
                    select(IMessageJob).where(
                        IMessageJob.campaign_id == campaign.id, IMessageJob.status == "issued"
                    )
                ):
                    self._uncertain(
                        session, job, "Телефон запросил следующее задание без подтверждения этого."
                    )
                session.flush()
                self._finish_if_done(session, campaign)
            state = self._state_of(
                session, campaign if campaign and campaign.status in ACTIVE else None
            )
            if state == "paused":
                return 200, {
                    "state": "paused",
                    "retrySeconds": PAUSED_RETRY_SECONDS,
                    "statusUrl": status_url,
                }
            if state != "ready":
                return 200, {"state": state, "statusUrl": status_url}
            job = session.scalar(
                select(IMessageJob)
                .where(IMessageJob.campaign_id == campaign.id, IMessageJob.status == "pending")
                .order_by(IMessageJob.position)
                .limit(1)
            )
            now = self.clock()
            job.status, job.attempts = "issued", job.attempts + 1
            job.issued_at, job.deadline_at = now, now + V2_ACK_WINDOW
            remaining = self._counts(session, campaign.id)["pending"]
            self._event(
                session,
                "job_issued",
                f"{job.phone}: задание выдано телефону (попытка {job.attempts})",
                campaign.id,
                job.id,
            )
            return 200, {
                "state": "ready",
                "jobId": job.key,
                "recipient": job.phone,
                "message": job.message,
                "attachments": self._attachments(session, campaign),
                "ackUrl": self._url("/v2/ack", jobId=job.key, stage="done"),
                "textAckUrl": self._url("/v2/ack", jobId=job.key, stage="text"),
                "statusUrl": status_url,
                # No wait after the last job: the next request only reads "finished".
                "delaySeconds": campaign.delay_seconds if remaining else 0,
            }

    def ack(self, key: str, protocol: str, stage: str = "done") -> tuple[int, dict]:
        """Idempotent: a repeated ACK changes nothing; a late one settles an uncertain job."""
        if not JOB_KEY.match(key or "") or stage not in ("text", "done"):
            return 400, {"ok": False, "error": "bad request"}
        with self.lock, self.sessions.begin() as session:
            job = session.scalar(select(IMessageJob).where(IMessageJob.key == key))
            if job is None:
                return 404, {"ok": False, "error": "unknown job"}
            campaign = session.get(IMessageCampaign, job.campaign_id)
            if campaign.protocol != protocol:
                return 409, {"ok": False, "error": "protocol"}
            now = self.clock()
            if protocol == "v2" and stage == "text":
                duplicate = job.text_acked_at is not None
                if job.status == "issued" and not duplicate:
                    job.text_acked_at = now
                    self._event(
                        session,
                        "text_ack",
                        f"{job.phone}: шаг отправки текста пройден",
                        campaign.id,
                        job.id,
                    )
                return 200, {"ok": True, "duplicate": duplicate}
            if job.status == "execution_acknowledged":
                job.ack_count += 1
                if job.ack_count <= 3:
                    self._event(
                        session,
                        "ack_repeated",
                        f"{job.phone}: повторное подтверждение, без изменений",
                        campaign.id,
                        job.id,
                    )
                return 200, {"ok": True, "duplicate": True}
            if job.status == "pending" and job.attempts == 0:
                return 409, {"ok": False, "error": "not issued"}
            previous = job.status
            job.status, job.acked_at, job.ack_count = "execution_acknowledged", now, 1
            job.ack_scope = "text" if protocol == "legacy" else "complete"
            if protocol == "legacy":
                job.text_acked_at = now
            if previous == "issued":
                detail = (
                    "Исходный Shortcut прошёл шаг отправки текста "
                    "(вложения и доставка не подтверждаются)"
                    if protocol == "legacy"
                    else "Shortcut подтвердил выполнение: текст и вложения переданы в Messages"
                )
                self._event(
                    session, "job_acknowledged", f"{job.phone}: {detail}", campaign.id, job.id
                )
            else:
                # Late ACK after a timeout, a manual decision or a return to the queue:
                # the phone did run the job, so it is not sent again.
                job.note = f"Подтверждение пришло позже (было: {previous})."
                self._event(
                    session,
                    "ack_late",
                    f"{job.phone}: подтверждение пришло позже, статус был «{previous}»",
                    campaign.id,
                    job.id,
                )
            self._finish_if_done(session, campaign)
            return 200, {"ok": True, "duplicate": False}

    # ---------- reads for the UI ----------

    def _job_dict(self, job: IMessageJob) -> dict:
        return {
            "id": job.id,
            "key": job.key,
            "position": job.position,
            "phone": job.phone,
            "message": job.message,
            "status": job.status,
            "attempts": job.attempts,
            "issued_at": iso(job.issued_at),
            "text_acked_at": iso(job.text_acked_at),
            "acked_at": iso(job.acked_at),
            "ack_scope": job.ack_scope,
            "resolution": job.resolution,
            "note": job.note,
        }

    def _campaign_dict(self, session, campaign: IMessageCampaign) -> dict:
        return {
            "id": campaign.id,
            "protocol": campaign.protocol,
            "status": campaign.status,
            "is_test": campaign.is_test,
            "delay_seconds": campaign.delay_seconds,
            "total": campaign.total,
            "created_at": iso(campaign.created_at),
            "finished_at": iso(campaign.finished_at),
            "counts": self._counts(session, campaign.id),
        }

    def _recipient_statuses(self, session, phones: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for start in range(0, len(phones), 500):
            for phone, status in session.execute(
                select(IMessageJob.phone, IMessageJob.status)
                .join(IMessageCampaign, IMessageCampaign.id == IMessageJob.campaign_id)
                .where(
                    IMessageCampaign.is_test.is_(False),
                    IMessageJob.phone.in_(phones[start : start + 500]),
                )
                .order_by(IMessageJob.id)
            ):
                result[phone] = status
        return result

    def bridge_info(self, row: IMessageWorkspace) -> dict:
        running = self.server is not None
        valid = self.token_valid(self.token)
        info: dict[str, Any] = {
            "running": running,
            "ip": self.server.address[0] if running else self._preferred_ip(row.bind_ip),
            "port": self.server.address[1] if running else row.port,
            "addresses": lan_addresses(),
            "token_valid": valid,
            "token_expires_at": iso(self.token_expires_at) if valid else None,
            "last_seen": (
                {**self.last_seen, "at": iso(self.last_seen["at"])} if self.last_seen else None
            ),
            "connect_url": None,
            "urls": None,
            "deep_links": None,
        }
        if running and valid:
            task, next_url = self._url("/task"), self._url("/v2/next")
            info["connect_url"] = self._url("/connect")
            info["urls"] = {"legacy": task, "v2": next_url}
            info["deep_links"] = {
                "legacy": deep_link(row.legacy_shortcut_name, task),
                "v2": deep_link(row.shortcut_name, next_url),
            }
        return info

    def state(self, params: dict | None = None) -> dict:
        with self.lock, self.sessions.begin() as session:
            self._sweep(session)
            row = self._row(session)
            statuses = self._recipient_statuses(session, [item["phone"] for item in row.recipients])
            attachments = [
                session.get(IMessageAttachment, file_id) for file_id in row.attachment_ids
            ]
            campaign = self._active(session) or session.scalar(
                select(IMessageCampaign).order_by(IMessageCampaign.id.desc()).limit(1)
            )
            jobs = (
                session.scalars(
                    select(IMessageJob)
                    .where(IMessageJob.campaign_id == campaign.id)
                    .order_by(IMessageJob.position)
                    .limit(MAX_RECIPIENTS)
                )
                if campaign
                else []
            )
            return {
                "bridge": self.bridge_info(row),
                "workspace": {
                    "recipients": [
                        {**item, "status": statuses.get(item["phone"])} for item in row.recipients
                    ],
                    "messages": variants(row),
                    "attachments": [
                        {
                            "id": item.id,
                            "filename": item.filename,
                            "mime": item.mime,
                            "size": item.size,
                        }
                        for item in attachments
                        if item is not None
                    ],
                    "protocol": row.protocol,
                    "shortcut_name": row.shortcut_name,
                    "legacy_shortcut_name": row.legacy_shortcut_name,
                    "delay_seconds": row.delay_seconds,
                },
                "campaign": self._campaign_dict(session, campaign) if campaign else None,
                "active": bool(campaign and campaign.status in ACTIVE),
                "jobs": [self._job_dict(job) for job in jobs],
            }

    def preview(self, params: dict | None = None) -> dict:
        """What each number gets and the JSON the phone reads, without issuing anything."""
        with self.sessions() as session:
            row = self._row(session)
            attachments = [
                {"downloadUrl": f"…/attachment/{file_id}?token=•••", "name": item.filename}
                for file_id in row.attachment_ids
                if (item := session.get(IMessageAttachment, file_id)) is not None
            ]
            items = [
                {"phone": item["phone"], "text": text, "individual": bool(item["message"])}
                for item, text in zip(row.recipients, texts_for(row, row.recipients))
            ]
            first = items[0] if items else {"phone": "+15555550123", "text": ""}
            if row.protocol == "legacy":
                payload: dict = {
                    "message": (variants(row) or [""])[0],
                    "contacts": [
                        {
                            "phone": item["phone"],
                            "message": item["text"],
                            "ackUrl": "…/ack?token=•••&jobId=…",
                        }
                        for item in items[:3]
                    ],
                    "attachments": [{"downloadUrl": item["downloadUrl"]} for item in attachments],
                }
            else:
                payload = {
                    "state": "ready",
                    "jobId": "…",
                    "recipient": first["phone"],
                    "message": first["text"],
                    "attachments": attachments,
                    "ackUrl": "…/v2/ack?token=•••&jobId=…&stage=done",
                    "textAckUrl": "…/v2/ack?token=•••&jobId=…&stage=text",
                    "statusUrl": "…/v2/status?token=•••",
                    "delaySeconds": row.delay_seconds,
                }
            return {"protocol": row.protocol, "items": items, "payload": payload}

    def events(self, params: dict) -> list[dict]:
        limit = min(int(params.get("limit", 200)), 1000)
        with self.sessions() as session:
            query = select(IMessageEvent).order_by(IMessageEvent.id.desc()).limit(limit)
            if params.get("campaign_id"):
                query = query.where(IMessageEvent.campaign_id == int(params["campaign_id"]))
            return [
                {
                    "id": event.id,
                    "campaign_id": event.campaign_id,
                    "job_id": event.job_id,
                    "type": event.type,
                    "detail": event.detail,
                    "created_at": iso(event.created_at),
                }
                for event in session.scalars(query)
            ]

    def campaigns(self, params: dict | None = None) -> list[dict]:
        with self.sessions() as session:
            return [
                self._campaign_dict(session, campaign)
                for campaign in session.scalars(
                    select(IMessageCampaign).order_by(IMessageCampaign.id.desc()).limit(50)
                )
            ]

    def prune(self) -> None:
        with self.sessions.begin() as session:
            cutoff = session.scalar(
                select(IMessageEvent.id)
                .order_by(IMessageEvent.id.desc())
                .offset(MAX_EVENTS)
                .limit(1)
            )
            if cutoff is not None:
                session.execute(delete(IMessageEvent).where(IMessageEvent.id <= cutoff))

    def remove_orphan_files(self) -> None:
        """Copies left by a crash between the copy and the database row."""
        if not self.files.is_dir():
            return
        with self.sessions() as session:
            known = set(session.scalars(select(IMessageAttachment.id)))
        for path in self.files.iterdir():
            if path.is_file() and path.name not in known:
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
