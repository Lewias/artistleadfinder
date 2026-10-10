"""CRM contacts of one channel: list, edit, trash, statuses, imports, files and «Написать».

The Instagram and the iMessage CRM are separate (`crm` column). Contacts move between
them, and from the parsing base, only through an import; a repeated import merges into
the contacts that share a channel (Instagram username, email or phone) or the lead.
Dates of next actions are calendar days in the user's local time.

With accounts on, the tables are the working copy of the shared CRM (crm/sync.py). An
admin's copy holds every user's contacts: the list can show all or one owner, while
imports, «Написать» and statuses always work on the admin's own CRM.
"""

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, or_, select

from ..errors import UserError
from ..imessage.phones import EMAIL, normalize_phone
from ..models import (
    Conversation,
    CrmContact,
    CrmStatus,
    CrmTombstone,
    IMessageCampaign,
    IMessageJob,
    Lead,
    LeadScoutProfile,
    Setting,
)
from . import sheets
from . import sync as _sync  # noqa: F401 - registers the change marker
from .emoji import clean_emoji

CRMS = ("instagram", "imessage")
KINDS = ("instagram", "email", "phone")
COLORS = ("violet", "blue", "green", "amber", "red", "pink", "slate")
DEFAULT_STATUSES = [
    ("Артист", "violet"),
    ("Интересуется", "amber"),
    ("Ответил", "green"),
    ("Сделка", "green"),
    ("Отказ", "red"),
    ("Не связываться", "red"),
]
CATEGORY_LABELS = {"artist": "Артист", "producer": "Продюсер", "media": "Медиа", "other": "Другое"}
USERNAME = re.compile(r"^[a-z0-9._]{1,30}$")
INSTAGRAM_URL = re.compile(r"instagram\.com/([A-Za-z0-9._]{1,30})", re.I)
SPLIT = re.compile(r"[,;\n]+")
MAX_STATUSES = 40
MAX_LABEL = 40


def status_emoji(value) -> str:
    """One emoji before a status label."""
    emoji = clean_emoji(value)
    if emoji is None:
        raise UserError("Неподходящий эмодзи статуса.")
    return emoji


PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
EXPORT_HEADER = [
    "Контакт",
    "Instagram",
    "Email",
    "Телефон",
    "Статусы",
    "Последний контакт",
    "Следующее действие",
    "Дата действия",
    "Заметки",
    "Принёс",
    "Потенциал",
]
# Header names an imported file may use, lower case.
COLUMNS = {
    "name": ("контакт", "имя", "name", "contact", "full name"),
    "instagram": ("instagram", "инстаграм", "username", "ig", "insta"),
    "email": ("email", "e-mail", "почта", "mail"),
    "phone": ("телефон", "phone", "номер", "phone number", "тел"),
    "statuses": ("статусы", "статус", "statuses", "status", "метки", "tags", "теги"),
    "last_contact_at": ("последний контакт", "last contact"),
    "next_action": ("следующее действие", "next action", "действие"),
    "next_action_at": ("дата действия", "next action date", "дата"),
    "notes": ("заметки", "заметка", "notes", "note", "комментарий"),
    "earned": ("принёс", "принес", "earned", "деньги", "доход"),
    "potential": ("потенциал", "potential"),
}
SOURCES = {
    "instagram": ("leads", "imessage"),
    "imessage": ("instagram", "leads"),
}


def _crm(params: dict) -> str:
    crm = str(params.get("crm") or "")
    if crm not in CRMS:
        raise UserError("Неизвестная CRM.")
    return crm


def _ids(params: dict) -> list[int]:
    ids = params.get("ids")
    if (
        not isinstance(ids, list)
        or len(ids) > 100000
        or any(not isinstance(value, int) or value <= 0 for value in ids)
    ):
        raise UserError("Некорректный список контактов.")
    return ids


def normalize_channel(kind: str, value: object) -> str | None:
    text = str(value or "").strip()
    if kind == "instagram":
        match = INSTAGRAM_URL.search(text)
        text = (match.group(1) if match else text).lstrip("@").lower()
        return text if USERNAME.match(text) else None
    if kind == "email":
        text = text.lower()
        return text if len(text) <= 254 and EMAIL.match(text) else None
    if kind == "phone":
        compact = re.sub(r"[\s()\-.]", "", text)
        return normalize_phone(compact)
    return None


def channels_from(value: object) -> list[dict]:
    """Validated, de-duplicated channels; the order is kept, the first is the main one."""
    if not isinstance(value, list) or len(value) > 50:
        raise UserError("Некорректный список каналов.")
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        kind = str(item.get("kind") if isinstance(item, dict) else "")
        if kind not in KINDS:
            raise UserError("Канал: Instagram, email или телефон.")
        raw = item.get("value")
        normalized = normalize_channel(kind, raw)
        if normalized is None:
            label = {"instagram": "Instagram", "email": "Email", "phone": "Телефон"}[kind]
            hint = " (с «+» и кодом страны)" if kind == "phone" else ""
            raise UserError(f"{label}{hint}: «{str(raw)[:60]}» не подходит.")
        if (kind, normalized) not in seen:
            seen.add((kind, normalized))
            result.append({"kind": kind, "value": normalized})
    return result


def labels_from(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_STATUSES:
        raise UserError("Некорректный список статусов.")
    result: list[str] = []
    for item in value:
        label = " ".join(str(item).split())[:MAX_LABEL]
        if label and label not in result:
            result.append(label)
    return result


def _money(value: object) -> float:
    if value in (None, ""):
        return 0.0
    text = re.sub(r"[^\d,.\-]", "", str(value)).replace(",", ".")
    try:
        amount = float(text) if text else 0.0
    except ValueError as error:
        raise UserError(f"Сумма «{str(value)[:30]}» не число.") from error
    if not 0 <= amount <= 1e12:
        raise UserError("Сумма — от 0.")
    return round(amount, 2)


def _day(value: object) -> datetime | None:
    """A calendar day from ISO (2026-10-05), Russian (05.10.2026) or Excel serial form."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], pattern)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None)
    except ValueError:
        pass
    serial = sheets.excel_date(text)
    if serial is not None:
        return serial
    raise UserError(f"Дата «{text[:30]}» не распознана (нужно 2026-10-05 или 05.10.2026).")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _keys(contact: CrmContact) -> set[tuple[str, str]]:
    keys = {(item["kind"], item["value"]) for item in contact.channels or []}
    if contact.lead_id:
        keys.add(("lead", str(contact.lead_id)))
    return keys


def _primary(channels: list[dict], crm: str) -> list[dict]:
    """The iMessage CRM leads with a phone or email, the Instagram one with the username."""
    first = ("phone", "email") if crm == "imessage" else ("instagram",)
    return sorted(channels, key=lambda item: item["kind"] not in first)


def _merge_list(current: list, extra: list) -> list:
    result = list(current)
    for item in extra:
        if item not in result:
            result.append(item)
    return result


class CrmService:
    def __init__(
        self,
        sessions,
        add_usernames: Callable[[list[str]], int],
        add_recipients: Callable[[list[str]], int],
        today: Callable[[], date] = date.today,
    ):
        self.sessions = sessions
        self.add_usernames = add_usernames
        self.add_recipients = add_recipients
        self.today = today
        # (user id, is admin) of the signed-in account; (None, False) without accounts.
        self.viewer: Callable[[], tuple[str | None, bool]] = lambda: (None, False)

    def _own(self, query):
        """Only the viewer's own contacts (rows from before sign-in have no owner yet)."""
        me, admin = self.viewer()
        if not admin:
            return query
        return query.where(or_(CrmContact.owner_id.is_(None), CrmContact.owner_id == me))

    def _owned_by(self, query, owner: str | None):
        """Own contacts by default; `all` or an owner's id in an admin's or moderator's copy."""
        if owner in (None, "", "mine"):
            return self._own(query)
        if owner == "all":
            return query
        return query.where(CrmContact.owner_id == str(owner)[:36])

    # ---------- statuses ----------

    def _statuses(self, session, crm: str) -> list[CrmStatus]:
        key = f"crm_statuses_seeded_{crm}"
        if session.get(Setting, key) is None:
            session.add(Setting(key=key, value=True))
            for position, (label, color) in enumerate(DEFAULT_STATUSES):
                session.add(CrmStatus(crm=crm, label=label, color=color, position=position))
            session.flush()
        return list(
            session.scalars(
                select(CrmStatus).where(CrmStatus.crm == crm).order_by(CrmStatus.position)
            )
        )

    def statuses_save(self, params: dict) -> dict:
        """Replaces the configured statuses; a removed status leaves the contacts too."""
        crm = _crm(params)
        items = params.get("statuses")
        if not isinstance(items, list) or len(items) > MAX_STATUSES:
            raise UserError(f"Не больше {MAX_STATUSES} статусов.")
        wanted: list[tuple[str, str, str]] = []
        for item in items:
            label = " ".join(str(item.get("label") or "").split())[:MAX_LABEL]
            color = str(item.get("color") or "violet")
            if not label:
                raise UserError("У статуса должно быть название.")
            if color not in COLORS:
                raise UserError("Неизвестный цвет статуса.")
            if any(label.casefold() == known.casefold() for known, *_ in wanted):
                raise UserError(f"Статус «{label}» повторяется.")
            wanted.append((label, color, status_emoji(item.get("emoji"))))
        with self.sessions.begin() as session:
            removed = {row.label for row in self._statuses(session, crm)} - {
                label for label, *_ in wanted
            }
            session.execute(delete(CrmStatus).where(CrmStatus.crm == crm))
            session.flush()
            for position, (label, color, emoji) in enumerate(wanted):
                session.add(
                    CrmStatus(crm=crm, label=label, color=color, emoji=emoji, position=position)
                )
            if removed:
                own = self._own(select(CrmContact).where(CrmContact.crm == crm))
                for contact in session.scalars(own):
                    if removed & set(contact.statuses or []):
                        contact.statuses = [s for s in contact.statuses if s not in removed]
        return self.contacts({"crm": crm, "page_size": 1})

    # ---------- list ----------

    def _last_contacts(self, session, crm: str, contacts: list[CrmContact]) -> dict[int, datetime]:
        """When the app last wrote to each contact: outreach for Instagram, the phone
        (execution acknowledged, not tests) for iMessage."""
        found: dict[int, datetime] = {}
        if crm == "instagram":
            names: dict[str, list[int]] = {}
            for contact in contacts:
                for item in contact.channels or []:
                    if item["kind"] == "instagram":
                        names.setdefault(item["value"], []).append(contact.id)
            keys = list(names)
            for start in range(0, len(keys), 500):
                for username, when in session.execute(
                    select(Lead.username, Lead.last_contacted_at).where(
                        Lead.platform == "instagram",
                        Lead.username.in_(keys[start : start + 500]),
                        Lead.last_contacted_at.is_not(None),
                    )
                ):
                    for contact_id in names[username]:
                        found[contact_id] = max(found.get(contact_id, when), when)
        else:
            values: dict[str, list[int]] = {}
            for contact in contacts:
                for item in contact.channels or []:
                    if item["kind"] in ("phone", "email"):
                        values.setdefault(item["value"], []).append(contact.id)
            keys = list(values)
            for start in range(0, len(keys), 500):
                for phone, when in session.execute(
                    select(IMessageJob.phone, func.max(IMessageJob.acked_at))
                    .join(IMessageCampaign, IMessageCampaign.id == IMessageJob.campaign_id)
                    .where(
                        IMessageJob.phone.in_(keys[start : start + 500]),
                        IMessageJob.acked_at.is_not(None),
                        IMessageCampaign.is_test.is_(False),
                    )
                    .group_by(IMessageJob.phone)
                ):
                    for contact_id in values[phone]:
                        found[contact_id] = max(found.get(contact_id, when), when)
        return found

    def _dict(self, contact: CrmContact, derived: datetime | None) -> dict:
        last = max((value for value in (contact.last_contact_at, derived) if value), default=None)
        me, _ = self.viewer()
        return {
            "owner_id": contact.owner_id,
            "owner_name": contact.owner_name,
            "mine": contact.owner_id in (None, me),
            "id": contact.id,
            "name": contact.name,
            "source": contact.source or "",
            "cloud": contact.cloud,
            "statuses": contact.statuses or [],
            "channels": contact.channels or [],
            "notes": contact.notes,
            "last_contact_at": _iso(last),
            "next_action": contact.next_action,
            "next_action_at": _iso(contact.next_action_at),
            "earned": contact.earned,
            "potential": contact.potential,
            "lead_id": contact.lead_id,
            "deleted_at": _iso(contact.deleted_at),
            "attention": self._attention(contact),
        }

    def _attention(self, contact: CrmContact) -> bool:
        return bool(
            contact.next_action_at is not None
            and contact.deleted_at is None
            and contact.next_action_at.date() <= self.today()
        )

    def contacts(self, params: dict) -> dict:
        crm = _crm(params)
        tab = str(params.get("tab") or "all")
        if tab not in ("all", "attention", "trash"):
            raise UserError("Неизвестная вкладка.")
        search = str(params.get("search") or "").strip().casefold()[:200]
        sort = str(params.get("sort") or "created")
        descending = bool(params.get("descending", sort == "created"))
        filters = params.get("filters") or {}
        page = max(1, int(params.get("page") or 1))
        size = min(MAX_PAGE_SIZE, max(1, int(params.get("page_size") or PAGE_SIZE)))
        owner = params.get("owner")
        with self.sessions.begin() as session:
            statuses = self._statuses(session, crm)
            query = self._owned_by(select(CrmContact).where(CrmContact.crm == crm), owner)
            contacts = list(session.scalars(query.order_by(CrmContact.id)))
            derived = self._last_contacts(session, crm, contacts)
            owners = self._owners(session, crm)
        live = [contact for contact in contacts if contact.deleted_at is None]
        counts = {
            "all": len(live),
            "attention": sum(self._attention(contact) for contact in live),
            "trash": len(contacts) - len(live),
        }
        totals = {
            "earned": round(sum(contact.earned for contact in live), 2),
            "potential": round(sum(contact.potential for contact in live), 2),
        }
        in_use = _merge_list([row.label for row in statuses], [])
        for contact in live:
            in_use = _merge_list(in_use, contact.statuses or [])
        if tab == "trash":
            rows = [contact for contact in contacts if contact.deleted_at is not None]
        elif tab == "attention":
            rows = [contact for contact in live if self._attention(contact)]
        else:
            rows = live
        rows = [
            contact
            for contact in rows
            if self._matches(contact, search, filters, derived.get(contact.id))
        ]
        rows = self._sorted(rows, sort, descending, derived)
        start = (page - 1) * size
        return {
            "items": [self._dict(c, derived.get(c.id)) for c in rows[start : start + size]],
            "total": len(rows),
            "counts": counts,
            "totals": totals,
            "statuses": [
                {"label": row.label, "color": row.color, "emoji": row.emoji or ""}
                for row in statuses
            ],
            "labels": in_use,
            "owners": owners,
        }

    def _owners(self, session, crm: str) -> list[dict]:
        """Other owners in an admin's copy, for the owner filter; empty for a user."""
        me, admin = self.viewer()
        if not admin:
            return []
        rows = session.execute(
            select(CrmContact.owner_id, CrmContact.owner_name, func.count())
            .where(CrmContact.crm == crm, CrmContact.deleted_at.is_(None))
            .group_by(CrmContact.owner_id, CrmContact.owner_name)
        )
        found: dict[str, dict] = {}
        for owner_id, name, count in rows:
            key = me if owner_id in (None, me) else owner_id
            item = found.setdefault(
                key, {"id": key, "name": name or "", "count": 0, "mine": key == me}
            )
            item["count"] += count
            item["name"] = item["name"] or name or ""
        return sorted(found.values(), key=lambda item: (not item["mine"], item["name"].casefold()))

    def _matches(self, contact: CrmContact, search: str, filters: dict, derived) -> bool:
        if search:
            haystack = " ".join(
                [
                    contact.name,
                    contact.notes,
                    contact.next_action,
                    *(item["value"] for item in contact.channels or []),
                    *(contact.statuses or []),
                ]
            ).casefold()
            if search not in haystack:
                return False
        wanted = filters.get("statuses") or []
        if wanted and not set(wanted) & set(contact.statuses or []):
            return False
        kinds = filters.get("channels") or []
        if kinds and not {item["kind"] for item in contact.channels or []} & set(kinds):
            return False
        last = max((v for v in (contact.last_contact_at, derived) if v), default=None)
        today = self.today()
        when = filters.get("last")
        if when == "never" and last is not None:
            return False
        if when in ("week", "month"):
            days = 7 if when == "week" else 30
            if last is None or last.date() < today - timedelta(days=days):
                return False
        if when == "older" and (last is None or last.date() >= today - timedelta(days=30)):
            return False
        action = filters.get("next")
        due = contact.next_action_at.date() if contact.next_action_at else None
        if action == "none" and (contact.next_action or due):
            return False
        if action == "due" and (due is None or due > today):
            return False
        if action == "planned" and (due is None or due <= today):
            return False
        notes = filters.get("notes")
        if notes == "with" and not contact.notes.strip():
            return False
        if notes == "without" and contact.notes.strip():
            return False
        money = filters.get("money")
        if money == "earned" and not contact.earned:
            return False
        if money == "potential" and not contact.potential:
            return False
        if money == "none" and (contact.earned or contact.potential):
            return False
        return True

    @staticmethod
    def _sorted(rows, sort: str, descending: bool, derived) -> list[CrmContact]:
        if sort == "name":
            return sorted(rows, key=lambda c: c.name.casefold(), reverse=descending)
        if sort in ("last", "next"):

            def value(contact):
                if sort == "next":
                    return contact.next_action_at
                return max(
                    (v for v in (contact.last_contact_at, derived.get(contact.id)) if v),
                    default=None,
                )

            known = [contact for contact in rows if value(contact) is not None]
            unknown = [contact for contact in rows if value(contact) is None]
            # Contacts without a date stay at the end in both directions.
            return sorted(known, key=value, reverse=descending) + unknown
        return sorted(rows, key=lambda c: c.id, reverse=descending)

    # ---------- edit ----------

    def save(self, params: dict) -> dict:
        crm = _crm(params)
        name = " ".join(str(params.get("name") or "").split())[:160]
        channels = channels_from(params.get("channels") or [])
        if not name:
            name = channels[0]["value"] if channels else ""
        if not name:
            raise UserError("Укажите имя или хотя бы один канал.")
        values = {
            "name": name,
            "channels": channels,
            "statuses": labels_from(params.get("statuses") or []),
            "notes": str(params.get("notes") or "")[:5000],
            "last_contact_at": _day(params.get("last_contact_at")),
            "next_action": " ".join(str(params.get("next_action") or "").split())[:300],
            "next_action_at": _day(params.get("next_action_at")),
            "earned": _money(params.get("earned")),
            "potential": _money(params.get("potential")),
        }
        with self.sessions.begin() as session:
            if params.get("id"):
                contact = session.get(CrmContact, int(params["id"]))
                if contact is None or contact.crm != crm:
                    raise UserError("Контакт не найден.")
            else:
                keys = {(item["kind"], item["value"]) for item in channels}
                own = self._own(select(CrmContact).where(CrmContact.crm == crm))
                for other in session.scalars(own):
                    if keys & _keys(other):
                        raise UserError(f"Такой контакт уже есть: {other.name}.")
                contact = CrmContact(crm=crm, owner_id=self.viewer()[0])
                session.add(contact)
            for key, value in values.items():
                setattr(contact, key, value)
            session.flush()
            derived = self._last_contacts(session, crm, [contact])
            return self._dict(contact, derived.get(contact.id))

    def _update(self, params: dict, apply: Callable[[CrmContact], None]) -> dict:
        crm = _crm(params)
        ids = _ids(params)
        with self.sessions.begin() as session:
            changed = 0
            for start in range(0, len(ids), 500):
                for contact in session.scalars(
                    select(CrmContact).where(
                        CrmContact.crm == crm, CrmContact.id.in_(ids[start : start + 500])
                    )
                ):
                    apply(contact)
                    changed += 1
        return {"changed": changed}

    def trash(self, params: dict) -> dict:
        now = datetime.now()

        def apply(contact: CrmContact) -> None:
            contact.deleted_at = contact.deleted_at or now

        return self._update(params, apply)

    def restore(self, params: dict) -> dict:
        def apply(contact: CrmContact) -> None:
            contact.deleted_at = None

        return self._update(params, apply)

    def purge(self, params: dict) -> dict:
        """Deletes for good: the given contacts of the trash, or the whole trash."""
        crm = _crm(params)
        where = [CrmContact.crm == crm, CrmContact.deleted_at.is_not(None)]
        if params.get("ids") is not None:
            where.append(CrmContact.id.in_(_ids(params)))
        with self.sessions.begin() as session:
            # Emptying the whole trash empties the admin's own trash only.
            chosen = select(CrmContact.id, CrmContact.remote_id).where(*where)
            if params.get("ids") is None:
                chosen = self._own(chosen)
            rows = session.execute(chosen).all()
            for _, remote_id in rows:
                if remote_id:
                    session.merge(CrmTombstone(remote_id=remote_id))
            ids = [row_id for row_id, _ in rows]
            removed = 0
            for start in range(0, len(ids), 500):
                removed += session.execute(
                    delete(CrmContact).where(CrmContact.id.in_(ids[start : start + 500]))
                ).rowcount
        return {"removed": removed}

    def label(self, params: dict) -> dict:
        """Adds or removes one status on the chosen contacts."""
        label = " ".join(str(params.get("label") or "").split())[:MAX_LABEL]
        if not label:
            raise UserError("Выберите статус.")
        add = bool(params.get("add", True))

        def apply(contact: CrmContact) -> None:
            current = list(contact.statuses or [])
            if add and label not in current:
                contact.statuses = [*current, label]
            elif not add and label in current:
                contact.statuses = [s for s in current if s != label]

        return self._update(params, apply)

    # ---------- «Написать» ----------

    def write(self, params: dict) -> dict:
        """Puts the chosen contacts into this channel's mailing list; nothing is sent."""
        crm = _crm(params)
        ids = _ids(params)
        with self.sessions() as session:
            contacts = [
                contact
                for start in range(0, len(ids), 500)
                for contact in session.scalars(
                    self._own(
                        select(CrmContact).where(
                            CrmContact.crm == crm,
                            CrmContact.id.in_(ids[start : start + 500]),
                            CrmContact.deleted_at.is_(None),
                        )
                    )
                )
            ]
        kinds = ("instagram",) if crm == "instagram" else ("phone", "email")
        values = []
        for contact in contacts:
            if "Не связываться" in (contact.statuses or []):
                continue
            value = next(
                (item["value"] for item in contact.channels or [] if item["kind"] in kinds), None
            )
            if value:
                values.append(value)
        if not values:
            raise UserError(
                "У выбранных нет Instagram."
                if crm == "instagram"
                else "У выбранных нет телефона или email."
            )
        added = self.add_usernames(values) if crm == "instagram" else self.add_recipients(values)
        return {"added": added, "found": len(values)}

    # ---------- imports ----------

    def sources(self, params: dict) -> list[dict]:
        crm = _crm(params)
        result = []
        with self.sessions() as session:
            for source in SOURCES[crm]:
                if source == "leads":
                    count = session.scalar(select(func.count()).select_from(Lead))
                else:
                    count = len(self._crm_rows(session, source))
                result.append({"id": source, "count": count})
        return result

    def _crm_rows(self, session, source: str) -> list[CrmContact]:
        return list(
            session.scalars(
                self._own(
                    select(CrmContact).where(
                        CrmContact.crm == source, CrmContact.deleted_at.is_(None)
                    )
                ).order_by(CrmContact.id)
            )
        )

    def _lead_contacts(self, session) -> list[dict]:
        """The contact base is shared: every lead goes to either CRM; without a phone or
        email an iMessage contact keeps its Instagram and waits for one."""
        query = select(Lead, LeadScoutProfile).outerjoin(
            LeadScoutProfile, LeadScoutProfile.lead_id == Lead.id
        )
        replied = set(
            session.scalars(select(Conversation.lead_id).where(Conversation.status == "replied"))
        )
        result = []
        for lead, profile in session.execute(query.order_by(Lead.id)):
            channels = []
            if lead.platform == "instagram":
                channels.append({"kind": "instagram", "value": lead.username})
            for kind, values in (
                ("email", profile.emails if profile else []),
                ("phone", profile.phones if profile else []),
            ):
                channels.extend({"kind": kind, "value": value} for value in values or [])
            statuses = []
            if profile and profile.profile_type in CATEGORY_LABELS:
                statuses.append(CATEGORY_LABELS[profile.profile_type])
            if profile and profile.source_username:
                statuses.append(profile.source_username)
            if lead.id in replied:
                statuses.append("Ответил")
            if lead.do_not_contact:
                statuses.append("Не связываться")
            result.append(
                {
                    "name": lead.username,
                    "channels": channels,
                    "statuses": statuses,
                    "notes": lead.bio,
                    "last_contact_at": lead.last_contacted_at,
                    "lead_id": lead.id,
                }
            )
        return result

    @staticmethod
    def _copy(contact: CrmContact) -> dict:
        return {
            "name": contact.name,
            "channels": contact.channels or [],
            "statuses": contact.statuses or [],
            "notes": contact.notes,
            "last_contact_at": contact.last_contact_at,
            "next_action": contact.next_action,
            "next_action_at": contact.next_action_at,
            "lead_id": contact.lead_id,
        }

    def import_verse(self, params: dict) -> dict:
        crm = _crm(params)
        source = str(params.get("source") or "")
        if source not in SOURCES[crm]:
            raise UserError("Неизвестный источник импорта.")
        with self.sessions.begin() as session:
            if source == "leads":
                incoming = self._lead_contacts(session)
            else:
                incoming = [self._copy(row) for row in self._crm_rows(session, source)]
            return self._merge_all(session, crm, incoming)

    def merge(self, crm: str, incoming: list[dict]) -> dict:
        """Contacts from another part of the app (the replies read from Direct), merged
        like an import."""
        if crm not in ("instagram", "imessage"):
            raise UserError("Неизвестная CRM.")
        with self.sessions.begin() as session:
            return self._merge_all(session, crm, incoming)

    def _merge_all(self, session, crm: str, incoming: list[dict]) -> dict:
        """Adds new contacts and merges the rest into the contact sharing a channel/lead."""
        index: dict[tuple[str, str], CrmContact] = {}
        own = self._own(select(CrmContact).where(CrmContact.crm == crm))
        for contact in session.scalars(own):
            for key in _keys(contact):
                index.setdefault(key, contact)
        added = merged = skipped = 0
        for item in incoming:
            channels = []
            for channel in item.get("channels") or []:
                value = normalize_channel(channel["kind"], channel["value"])
                if value and {"kind": channel["kind"], "value": value} not in channels:
                    channels.append({"kind": channel["kind"], "value": value})
            channels = _primary(channels, crm)
            # Nothing to write to and nothing to merge by: a file row of only a name.
            if not channels:
                skipped += 1
                continue
            name = (item.get("name") or (channels[0]["value"] if channels else "")).strip()[:160]
            if not name:
                skipped += 1
                continue
            keys = {(c["kind"], c["value"]) for c in channels}
            if item.get("lead_id"):
                keys.add(("lead", str(item["lead_id"])))
            contact = next((index[key] for key in keys if key in index), None)
            statuses = labels_from(list(item.get("statuses") or [])[:MAX_STATUSES])
            if contact is None:
                contact = CrmContact(
                    crm=crm,
                    owner_id=self.viewer()[0],
                    name=name,
                    channels=channels,
                    statuses=statuses,
                    notes=(item.get("notes") or "")[:5000],
                    last_contact_at=item.get("last_contact_at"),
                    next_action=(item.get("next_action") or "")[:300],
                    next_action_at=item.get("next_action_at"),
                    earned=item.get("earned") or 0,
                    potential=item.get("potential") or 0,
                    lead_id=item.get("lead_id"),
                )
                session.add(contact)
                added += 1
            else:
                contact.channels = _merge_list(contact.channels or [], channels)
                contact.statuses = _merge_list(contact.statuses or [], statuses)[:MAX_STATUSES]
                if not contact.notes.strip() and item.get("notes"):
                    contact.notes = item["notes"][:5000]
                if not contact.next_action and item.get("next_action"):
                    contact.next_action = item["next_action"][:300]
                    contact.next_action_at = item.get("next_action_at")
                last = item.get("last_contact_at")
                if last and (contact.last_contact_at is None or last > contact.last_contact_at):
                    contact.last_contact_at = last
                if not contact.earned and item.get("earned"):
                    contact.earned = item["earned"]
                if not contact.potential and item.get("potential"):
                    contact.potential = item["potential"]
                if contact.lead_id is None and item.get("lead_id"):
                    contact.lead_id = item["lead_id"]
                merged += 1
            for key in _keys(contact) | keys:
                index.setdefault(key, contact)
        return {"added": added, "merged": merged, "skipped": skipped}

    # ---------- files ----------

    def import_file(self, params: dict) -> dict:
        crm = _crm(params)
        rows = sheets.read_table(Path(str(params.get("path") or "")))
        if not rows:
            raise UserError("Файл пустой.")
        header = [cell.casefold().strip() for cell in rows[0]]
        columns = {
            field: header.index(name)
            for field, names in COLUMNS.items()
            for name in names
            if name in header
        }
        if not {"instagram", "email", "phone"} & set(columns):
            raise UserError(
                "Нужна хотя бы одна колонка: Instagram, Email или Телефон "
                "(первая строка — названия колонок)."
            )
        incoming = []
        problems = 0
        for row in rows[1:]:

            def cell(field: str, row=row) -> str:
                index = columns.get(field)
                return row[index] if index is not None and index < len(row) else ""

            channels = [
                {"kind": kind, "value": value.strip()}
                for kind in ("instagram", "email", "phone")
                for value in SPLIT.split(cell(kind))
                if value.strip()
            ]
            try:
                incoming.append(
                    {
                        "name": cell("name"),
                        "channels": channels,
                        "statuses": [s.strip() for s in SPLIT.split(cell("statuses"))],
                        "notes": cell("notes"),
                        "last_contact_at": _day(cell("last_contact_at")),
                        "next_action": cell("next_action"),
                        "next_action_at": _day(cell("next_action_at")),
                        "earned": _money(cell("earned")),
                        "potential": _money(cell("potential")),
                    }
                )
            except ValueError:
                problems += 1
        with self.sessions.begin() as session:
            result = self._merge_all(session, crm, incoming)
        return {**result, "skipped": result["skipped"] + problems}

    def export(self, params: dict) -> dict:
        crm = _crm(params)
        path = Path(str(params.get("path") or ""))
        ids = set(_ids(params)) if params.get("ids") is not None else None
        with self.sessions() as session:
            query = self._owned_by(
                select(CrmContact).where(CrmContact.crm == crm, CrmContact.deleted_at.is_(None)),
                params.get("owner"),
            )
            contacts = [
                contact
                for contact in session.scalars(query.order_by(CrmContact.id))
                if ids is None or contact.id in ids
            ]
            derived = self._last_contacts(session, crm, contacts)

        def joined(contact: CrmContact, kind: str) -> str:
            return ", ".join(i["value"] for i in contact.channels or [] if i["kind"] == kind)

        def day(value: datetime | None) -> str:
            return value.date().isoformat() if value else ""

        def amount(value: float) -> str:
            return str(int(value)) if float(value).is_integer() else str(value)

        rows = []
        for contact in contacts:
            last = max(
                (v for v in (contact.last_contact_at, derived.get(contact.id)) if v),
                default=None,
            )
            rows.append(
                [
                    contact.name,
                    joined(contact, "instagram"),
                    joined(contact, "email"),
                    joined(contact, "phone"),
                    ", ".join(contact.statuses or []),
                    day(last),
                    contact.next_action,
                    day(contact.next_action_at),
                    contact.notes,
                    amount(contact.earned),
                    amount(contact.potential),
                ]
            )
        sheets.write_table(path, EXPORT_HEADER, rows)
        return {"path": str(path), "count": len(rows)}
