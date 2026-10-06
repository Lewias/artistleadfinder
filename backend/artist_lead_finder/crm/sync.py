"""Two-way sync of the CRM with the shared server.

The local tables stay the working copy (filters, sorting, import and export run on
them); the server is the source of truth. Every local change marks the row `dirty`
(a flush hook, so no CRM method has to remember it), a contact deleted for good leaves a
tombstone, and a changed status list marks its CRM. A sync round sends those, then takes
what changed on the server since the last round: a user gets their own CRM, an admin
every user's, with the owner's name. The last write wins; a local change waiting to be
sent is not overwritten by an older server copy.
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..account.client import AuthError, OfflineError
from ..models import CrmContact, CrmStatus, CrmTombstone, Setting

log = logging.getLogger(__name__)

SYNC_FLAG = "crm_sync"
INTERVAL_SECONDS = 15
BATCH = 200
PAGE = 500
# Rows committed on the server just before the previous cursor may show up late.
OVERLAP = timedelta(minutes=2)
CRMS = ("instagram", "imessage")
FIELDS = ("name", "notes", "next_action", "earned", "potential")
LISTS = ("statuses", "channels")
DATES = ("last_contact_at", "next_action_at", "deleted_at")


def _dirty_key(crm: str) -> str:
    return f"crm_statuses_dirty_{crm}"


def _synced_key(crm: str) -> str:
    return f"crm_statuses_synced_{crm}"


def _set(session, key: str, value) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


@event.listens_for(Session, "before_flush")
def _mark_changes(session, flush_context, instances) -> None:
    """Local edits wait to be sent; rows written by the sync itself do not."""
    if session.info.get(SYNC_FLAG):
        return
    crms = set()
    for item in list(session.new) + list(session.dirty):
        if isinstance(item, CrmContact) and (item in session.new or session.is_modified(item)):
            item.dirty = True
        elif isinstance(item, CrmStatus):
            crms.add(item.crm)
    for item in list(session.deleted):
        if isinstance(item, CrmContact) and item.remote_id:
            session.merge(CrmTombstone(remote_id=item.remote_id))
        elif isinstance(item, CrmStatus):
            crms.add(item.crm)
    for crm in crms:
        _set(session, _dirty_key(crm), True)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


def parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def contact_payload(contact: CrmContact) -> dict:
    payload = {"id": contact.remote_id, "crm": contact.crm}
    payload.update({field: getattr(contact, field) for field in FIELDS})
    payload.update({field: list(getattr(contact, field) or []) for field in LISTS})
    payload.update({field: iso(getattr(contact, field)) for field in DATES})
    return payload


class CrmSync:
    def __init__(self, sessions, account, interval: float = INTERVAL_SECONDS):
        self.sessions = sessions
        self.account = account
        self.interval = interval
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    # ---------- loop ----------

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def trigger(self) -> None:
        self.wake.set()

    def _loop(self) -> None:
        while not self.stopping.is_set():
            # A burst of edits goes out together a moment after the last one.
            if self.wake.wait(self.interval):
                self.wake.clear()
                self.stopping.wait(1)
            if self.stopping.is_set():
                break
            if self.account.ready:
                self.run_once()

    def run_once(self) -> bool:
        """One round; False when the server could not be reached or refused."""
        with self.lock:
            try:
                client, token = self.account.client, self.account.token()
                self._push_contacts(client, token)
                self._push_tombstones(client, token)
                self._sync_statuses(client, token)
                self._pull_contacts(client, token)
                return True
            except (OfflineError, AuthError):
                log.warning("crm_sync_unavailable")
                return False

    # ---------- contacts ----------

    def _push_contacts(self, client, token) -> None:
        while True:
            with self.sessions.begin() as session:
                session.info[SYNC_FLAG] = True
                rows = list(
                    session.scalars(
                        select(CrmContact).where(CrmContact.dirty.is_(True)).limit(BATCH)
                    )
                )
                for row in rows:
                    if not row.remote_id:
                        row.remote_id = str(uuid.uuid4())
                payload = [contact_payload(row) for row in rows]
            if not payload:
                return
            # The version as stored (the flush above stamped updated_at).
            with self.sessions() as session:
                versions = dict(
                    session.execute(
                        select(CrmContact.remote_id, CrmContact.updated_at).where(
                            CrmContact.remote_id.in_([item["id"] for item in payload])
                        )
                    ).all()
                )
            saved = client.upsert("crm_contacts", payload, token) or []
            with self.sessions.begin() as session:
                session.info[SYNC_FLAG] = True
                for item in saved:
                    row = session.scalar(
                        select(CrmContact).where(CrmContact.remote_id == item.get("id"))
                    )
                    if row is None:
                        continue
                    row.owner_id = item.get("owner_id") or row.owner_id
                    if not row.owner_name and row.owner_id == self.account.user_id:
                        row.owner_name = self._own_name()
                    # Changed again while it was being sent: it goes in the next round.
                    if row.updated_at == versions.get(row.remote_id):
                        row.dirty = False
            if len(payload) < BATCH:
                return

    def _push_tombstones(self, client, token) -> None:
        with self.sessions() as session:
            ids = list(session.scalars(select(CrmTombstone.remote_id).limit(BATCH)))
        if not ids:
            return
        client.update(
            "crm_contacts",
            {"id": f"in.({','.join(ids)})"},
            {"purged": True, "name": "", "notes": "", "statuses": [], "channels": []},
            token,
        )
        with self.sessions.begin() as session:
            for remote_id in ids:
                tombstone = session.get(CrmTombstone, remote_id)
                if tombstone is not None:
                    session.delete(tombstone)

    def _own_name(self) -> str:
        user = self.account.user or {}
        return user.get("display_name") or user.get("email") or ""

    def _owner_names(self, client, token) -> dict[str, str]:
        if not self.account.sees_all_crm:
            return {self.account.user_id: self._own_name()}
        rows = client.select("profiles", {"select": "id,email,display_name"}, token) or []
        return {row["id"]: row.get("display_name") or row.get("email") or "" for row in rows}

    def _pull_contacts(self, client, token) -> None:
        # An account that becomes admin or moderator pulls every CRM from the start.
        key = f"crm_sync_cursor_{'admin' if self.account.sees_all_crm else 'user'}"
        with self.sessions() as session:
            stored = session.get(Setting, key)
            cursor = parse(stored.value) if stored else None
        names = self._owner_names(client, token)
        since = cursor - OVERLAP if cursor else None
        while True:
            query = {"select": "*", "order": "updated_at.asc,id.asc", "limit": str(PAGE)}
            if since is not None:
                query["updated_at"] = f"gt.{iso(since)}"
            rows = client.select("crm_contacts", query, token) or []
            if not rows:
                return
            with self.sessions.begin() as session:
                session.info[SYNC_FLAG] = True
                for item in rows:
                    self._apply(session, item, names)
                newest = max(parse(item.get("updated_at")) or datetime.min for item in rows)
                cursor = max(cursor or newest, newest)
                _set(session, key, iso(cursor))
            if len(rows) < PAGE:
                return
            since = newest

    def _apply(self, session, item: dict, names: dict[str, str]) -> None:
        local = session.scalar(select(CrmContact).where(CrmContact.remote_id == item.get("id")))
        if item.get("purged"):
            if local is not None:
                session.delete(local)
            return
        if local is not None and local.dirty:
            return
        if item.get("crm") not in CRMS:
            return
        if local is None:
            local = CrmContact(crm=item["crm"], remote_id=item["id"])
            session.add(local)
        for field in FIELDS:
            if item.get(field) is not None:
                value = item[field]
                setattr(local, field, float(value) if field in ("earned", "potential") else value)
        for field in LISTS:
            value = item.get(field)
            setattr(local, field, value if isinstance(value, list) else [])
        for field in DATES:
            setattr(local, field, parse(item.get(field)))
        local.owner_id = item.get("owner_id")
        local.owner_name = names.get(local.owner_id, local.owner_name or "")
        local.dirty = False

    # ---------- statuses ----------

    def _sync_statuses(self, client, token) -> None:
        me = self.account.user_id
        remote = {
            row["crm"]: row
            for row in client.select(
                "crm_status_sets", {"select": "*", "owner_id": f"eq.{me}"}, token
            )
            or []
        }
        for crm in CRMS:
            with self.sessions() as session:
                dirty = bool((session.get(Setting, _dirty_key(crm)) or Setting(value=False)).value)
                synced = session.get(Setting, _synced_key(crm))
                synced_at = parse(synced.value) if synced else None
                items = [
                    {"label": row.label, "color": row.color}
                    for row in session.scalars(
                        select(CrmStatus).where(CrmStatus.crm == crm).order_by(CrmStatus.position)
                    )
                ]
            server = remote.get(crm)
            server_at = parse(server.get("updated_at")) if server else None
            newer_there = server_at is not None and (synced_at is None or server_at > synced_at)
            # Never synced here and the server has a list: the server's list wins over the
            # defaults this copy seeded; otherwise the latest edit wins.
            if newer_there and (not dirty or synced_at is None):
                self._take_statuses(crm, server)
            elif dirty:
                saved = client.upsert(
                    "crm_status_sets",
                    [{"owner_id": me, "crm": crm, "items": items}],
                    token,
                    on_conflict="owner_id,crm",
                )
                with self.sessions.begin() as session:
                    session.info[SYNC_FLAG] = True
                    _set(session, _dirty_key(crm), False)
                    stamp = (saved or [{}])[0].get("updated_at")
                    if stamp:
                        _set(session, _synced_key(crm), stamp)

    def _take_statuses(self, crm: str, server: dict) -> None:
        items = [
            item
            for item in server.get("items") or []
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ]
        with self.sessions.begin() as session:
            session.info[SYNC_FLAG] = True
            for row in session.scalars(select(CrmStatus).where(CrmStatus.crm == crm)):
                session.delete(row)
            session.flush()
            for position, item in enumerate(items):
                session.add(
                    CrmStatus(
                        crm=crm,
                        label=str(item["label"])[:40],
                        color=str(item.get("color") or "slate")[:12],
                        position=position,
                    )
                )
            _set(session, f"crm_statuses_seeded_{crm}", True)
            _set(session, _dirty_key(crm), False)
            _set(session, _synced_key(crm), server.get("updated_at"))
