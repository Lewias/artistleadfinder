"""Gmail over SMTP (send) and IMAP (replies and bounces) with an app password.

Plain text only, no tracking pixel and no links added by the app: that is what keeps
cold email out of spam. Errors come back as MailError kinds the queue acts on.
"""

import email
import email.policy
import imaplib
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, getaddresses, make_msgid, parseaddr

import certifi

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
TIMEOUT = 30
# New messages read per check; the rest wait for the next one.
MAX_FETCH = 200
BOUNCE_SENDERS = re.compile(r"^(?:mailer-daemon|postmaster)@", re.IGNORECASE)
# Gmail's own quota and abuse answers: the mailbox rests for a day.
LIMIT_SIGNS = ("5.4.5", "4.7.0", "4.7.28", "daily user sending", "sending limit", "rate limit")
# A VPN or proxy that tampers with mail traffic breaks the TLS stream at the sign-in.
BROKEN_LINK = (
    "Соединение с Gmail рвётся на входе: обычно так делает VPN или прокси, который не "
    "пропускает почту. Выключите VPN или добавьте smtp.gmail.com и imap.gmail.com в его "
    "исключения (прямое подключение) и попробуйте снова."
)


class MailError(Exception):
    """kind: auth, limited, recipient, rejected, temporary or uncertain."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass
class Outgoing:
    sender: str
    sender_name: str
    to: str
    subject: str
    body: str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    # Cold email offers one-click unsubscribe; a warm-up email is a personal note.
    unsubscribe: bool = True


@dataclass
class Incoming:
    uid: int
    sender: str
    subject: str
    in_reply_to: str
    references: list[str]
    failed_recipients: list[str]
    auto_reply: bool
    bounce: bool
    text: str
    message_id: str = ""


def context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def build(message: Outgoing) -> tuple[EmailMessage, str]:
    msg = EmailMessage(policy=email.policy.SMTP)
    msg["From"] = formataddr((message.sender_name, message.sender))
    msg["To"] = message.to
    msg["Subject"] = message.subject
    msg["Date"] = format_datetime(datetime.now().astimezone())
    domain = message.sender.rsplit("@", 1)[-1]
    message_id = make_msgid(domain=domain)
    msg["Message-ID"] = message_id
    if message.in_reply_to:
        msg["In-Reply-To"] = message.in_reply_to
        msg["References"] = " ".join([*message.references, message.in_reply_to])
    # One-click unsubscribe by reply; the inbox check suppresses the address.
    if message.unsubscribe:
        msg["List-Unsubscribe"] = f"<mailto:{message.sender}?subject=unsubscribe>"
    msg.set_content(message.body)
    return msg, message_id


def _smtp_kind(code: int, text: str) -> str:
    lowered = text.lower()
    if any(sign in lowered for sign in LIMIT_SIGNS):
        return "limited"
    return "temporary" if 400 <= code < 500 else "rejected"


def _text(raw) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


class GmailTransport:
    def __init__(self, address: str, password: str):
        self.address = address
        self.password = password

    def verify(self) -> None:
        """Signs in to both SMTP and IMAP; MailError("auth") on a wrong app password."""
        with self._smtp():
            pass
        client = self._imap()
        client.logout()

    def _smtp(self) -> smtplib.SMTP_SSL:
        try:
            client = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT, context=context())
        except (OSError, smtplib.SMTPException) as error:
            raise MailError("temporary", f"Нет связи с Gmail: {type(error).__name__}") from error
        try:
            client.login(self.address, self.password)
        except smtplib.SMTPAuthenticationError as error:
            client.close()
            raise MailError(
                "auth", "Gmail не принял пароль приложения. Создайте новый и вставьте его."
            ) from error
        except (ssl.SSLError, smtplib.SMTPServerDisconnected) as error:
            client.close()
            raise MailError("temporary", BROKEN_LINK) from error
        except (OSError, smtplib.SMTPException) as error:
            client.close()
            raise MailError("temporary", f"Gmail не ответил: {type(error).__name__}") from error
        return client

    def send(self, message: Outgoing) -> str:
        """Message-ID of the sent email. Errors before the data was handed over are safe to
        retry; a connection lost while handing it over is «uncertain»."""
        msg, message_id = build(message)
        client = self._smtp()
        try:
            client.send_message(msg, from_addr=self.address, to_addrs=[message.to])
        except smtplib.SMTPRecipientsRefused as error:
            code, text = next(iter(error.recipients.values()), (550, b""))
            kind = _smtp_kind(code, _text(text))
            raise MailError(
                "recipient" if kind == "rejected" else kind, f"{code} {_text(text)[:200]}"
            ) from error
        except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as error:
            text = _text(error.smtp_error)
            raise MailError(
                _smtp_kind(error.smtp_code, text), f"{error.smtp_code} {text[:200]}"
            ) from error
        except (smtplib.SMTPServerDisconnected, socket.timeout, OSError) as error:
            raise MailError(
                "uncertain", "Связь оборвалась во время отправки: проверьте «Отправленные»"
            ) from error
        except smtplib.SMTPException as error:
            raise MailError("temporary", type(error).__name__) from error
        finally:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                client.close()
        return message_id

    def _imap(self) -> imaplib.IMAP4_SSL:
        try:
            client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=context(), timeout=TIMEOUT)
        except OSError as error:
            raise MailError("temporary", f"Нет связи с Gmail: {type(error).__name__}") from error
        try:
            client.login(self.address, self.password)
        except imaplib.IMAP4.error as error:
            client.shutdown()
            text = str(error).lower()
            if "imap" in text and "enable" in text:
                raise MailError("auth", "В настройках Gmail выключен IMAP.") from error
            raise MailError(
                "auth", "Gmail не принял пароль приложения. Создайте новый и вставьте его."
            ) from error
        return client

    def fetch(self, uidvalidity: int | None, last_uid: int, since: datetime) -> tuple[int, list]:
        """(UIDVALIDITY, new inbox messages after last_uid). A changed UIDVALIDITY or the
        first check reads from `since`."""
        client = self._imap()
        try:
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise MailError("temporary", "Не удалось открыть «Входящие»")
            validity = int(_text(client.response("UIDVALIDITY")[1][0]))
            if uidvalidity == validity and last_uid > 0:
                status, data = client.uid("search", None, f"UID {last_uid + 1}:*")
            else:
                last_uid = 0
                status, data = client.uid("search", None, f"SINCE {since.strftime('%d-%b-%Y')}")
            uids = [int(value) for value in _text(data[0] or b"").split() if int(value) > last_uid]
            found = []
            for uid in sorted(uids)[:MAX_FETCH]:
                status, parts = client.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((part[1] for part in parts if isinstance(part, tuple)), None)
                if status == "OK" and raw:
                    found.append(parse(uid, raw))
            return validity, found
        except (imaplib.IMAP4.error, OSError, ValueError, IndexError) as error:
            raise MailError("temporary", f"Ошибка IMAP: {type(error).__name__}") from error
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    def warm_handle(self, message_id: str | None, in_reply_to: str | None, star: bool) -> dict:
        """The receiving side of a warm-up email: finds it in the inbox or in spam (by its
        Message-ID, or by In-Reply-To when the id is unknown), moves it out of spam, marks
        it read (and starred), archives it. {"placement": inbox|spam|missing, "message":
        Incoming or None}."""
        if message_id:
            criteria = f'HEADER Message-ID "{_bare(message_id)}"'
        elif in_reply_to:
            criteria = f'HEADER In-Reply-To "{_bare(in_reply_to)}"'
        else:
            return {"placement": "missing", "message": None}
        client = self._imap()
        try:
            folders = _special_folders(client)
            placement = None
            for folder, where in (("INBOX", "inbox"), (folders.get("junk"), "spam")):
                if not folder:
                    continue
                status, _ = client.select(_quote(folder))
                if status != "OK":
                    continue
                status, data = client.uid("search", None, criteria)
                uids = _text(data[0] or b"").split() if status == "OK" else []
                if not uids:
                    continue
                uid = uids[-1]
                placement = where
                if where == "spam":
                    client.uid("move", uid, "INBOX")
                    client.select("INBOX")
                    status, data = client.uid("search", None, criteria)
                    uids = _text(data[0] or b"").split() if status == "OK" else []
                    if not uids:
                        break
                    uid = uids[-1]
                status, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
                raw = next((part[1] for part in parts if isinstance(part, tuple)), None)
                flags = "(\\Seen \\Flagged)" if star else "(\\Seen)"
                client.uid("store", uid, "+FLAGS", flags)
                # Archived like a read personal email: the inbox stays for real replies.
                if folders.get("all"):
                    client.uid("move", uid, _quote(folders["all"]))
                return {
                    "placement": placement,
                    "message": parse(int(uid), raw) if raw else None,
                }
            return {"placement": placement or "missing", "message": None}
        except (imaplib.IMAP4.error, OSError, ValueError, IndexError) as error:
            raise MailError("temporary", f"Ошибка IMAP: {type(error).__name__}") from error
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def _bare(message_id: str) -> str:
    return message_id.strip().strip("<>").replace('"', "")


def _quote(folder: str) -> str:
    return '"' + folder.replace('"', '\\"') + '"'


def _special_folders(client) -> dict[str, str]:
    """Gmail names Spam and All Mail in the account's language; the special-use flags
    (RFC 6154) do not change."""
    status, rows = client.list()
    found: dict[str, str] = {}
    if status != "OK":
        return found
    for row in rows or []:
        line = _text(row)
        match = re.match(r'\((?P<flags>[^)]*)\) "[^"]*" (?P<name>.+)$', line)
        if not match:
            continue
        name = match.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"')
        flags = match.group("flags").lower()
        if "\\junk" in flags:
            found["junk"] = name
        elif "\\all" in flags:
            found["all"] = name
    return found


def _plain_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html")) if msg.is_multipart() else msg
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, KeyError, AssertionError):
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    return text if isinstance(text, str) else ""


def parse(uid: int, raw: bytes) -> Incoming:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    subject = str(msg.get("Subject", ""))
    auto = str(msg.get("Auto-Submitted", "no")).lower()
    auto_reply = (auto not in ("", "no")) or bool(
        re.match(
            r"(?:automatic reply|auto(?:matic)?[- ]?reply|out of (?:the )?office)", subject, re.I
        )
    )
    content_type = msg.get_content_type()
    bounce = bool(BOUNCE_SENDERS.match(sender)) or content_type == "multipart/report"
    failed = [
        address.lower()
        for _, address in getaddresses(msg.get_all("X-Failed-Recipients", []))
        if address
    ]
    text = _plain_text(msg)
    if bounce and not failed:
        # Without the header the failed address is somewhere in the report.
        report = raw.decode("utf-8", "replace")
        failed = sorted(
            {value.lower() for value in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", report)}
        )
    references = str(msg.get("References", "")).split()
    return Incoming(
        uid=uid,
        sender=sender,
        subject=subject,
        in_reply_to=str(msg.get("In-Reply-To", "")).strip(),
        references=references,
        failed_recipients=failed,
        auto_reply=auto_reply and not bounce,
        bounce=bounce,
        text=text[:4000],
        message_id=str(msg.get("Message-ID", "")).strip(),
    )
