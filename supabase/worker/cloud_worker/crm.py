"""Leads of a job into the owner's Instagram CRM (the `crm_contacts` the app syncs).

A contact is the same person when it has the same Instagram ID, or, without one, the
same Instagram username in its channels. Found again, it gets the new profile data,
the check date and any new public email or phone; its name (unless empty), statuses,
notes, next action, money and conversation history stay as the user left them.
"""

from datetime import datetime, timezone

from psycopg.types.json import Jsonb

SOURCE = "Cloud Parser"
STATUS_LABELS = {"ARTIST": "Артист", "PRODUCER": "Продюсер", "MEDIA": "Медиа"}
# An Instagram channel may hold a username, @username or a profile link.
CHANNEL_USERNAME = (
    "lower(regexp_replace(regexp_replace(ch->>'value', "
    "'^(https?://)?(www\\.)?instagram\\.com/', '', 'i'), '^@|/+$', '', 'g'))"
)


def known(cur, owner_id: str, usernames: list[str], ids: list[str]) -> tuple[set, set]:
    """Which of these usernames and Instagram IDs the owner's Instagram CRM already has."""
    names, found_ids = set(), set()
    if usernames:
        cur.execute(
            f"""
            select distinct {CHANNEL_USERNAME} as username
              from public.crm_contacts c, jsonb_array_elements(c.channels) ch
             where c.owner_id = %(owner)s and c.crm = 'instagram' and not c.purged
               and ch->>'kind' = 'instagram' and {CHANNEL_USERNAME} = any(%(names)s)
            """,
            {"owner": owner_id, "names": usernames},
        )
        names = {row["username"] for row in cur.fetchall()}
    if ids:
        cur.execute(
            """
            select instagram_id from public.crm_contacts
             where owner_id = %s and crm = 'instagram' and not purged
               and instagram_id = any(%s)
            """,
            (owner_id, ids),
        )
        found_ids = {row["instagram_id"] for row in cur.fetchall()}
    return names, found_ids


def _channels(existing: list, username: str, emails: list[str], phones: list[str]) -> list:
    channels = [item for item in (existing or []) if isinstance(item, dict)]
    have = {(item.get("kind"), str(item.get("value") or "").lower()) for item in channels}
    if not any(item.get("kind") == "instagram" for item in channels):
        channels.insert(0, {"kind": "instagram", "value": username})
    for kind, values in (("email", emails), ("phone", phones)):
        for value in values:
            if (kind, value.lower()) not in have:
                channels.append({"kind": kind, "value": value})
                have.add((kind, value.lower()))
    return channels


def cloud_info(candidate: dict, job_id: str, now: datetime) -> dict:
    profile = candidate.get("profile") or {}
    return {
        "job_id": job_id,
        "checked_at": now.astimezone(timezone.utc).isoformat(),
        "category": candidate.get("category"),
        "confidence": candidate.get("confidence"),
        "reason": candidate.get("reason"),
        "ai": candidate.get("ai_ok"),
        "via": candidate.get("via") or [],
        "origins": (candidate.get("origins") or [])[:3],
        "profile": {
            "full_name": profile.get("full_name"),
            "biography": (profile.get("biography") or "")[:500] or None,
            "links": (profile.get("links") or [])[:5],
            "followers": profile.get("followers"),
            "following": profile.get("following"),
            "posts": profile.get("posts"),
            "verified": profile.get("verified"),
            "private": profile.get("private"),
            "business": profile.get("business"),
            "category": profile.get("category"),
        },
    }


def mark_written(cur, owner_id: str, username: str, when: datetime) -> str | None:
    """The cloud outreach wrote to this person: the CRM contact's last contact moves to
    `when` (never back). Returns the contact's id, None when the CRM has no such contact."""
    cur.execute(
        f"""
        update public.crm_contacts c
           set last_contact_at = greatest(coalesce(c.last_contact_at, %(when)s), %(when)s)
         where c.id = (
           select c2.id from public.crm_contacts c2
            where c2.owner_id = %(owner)s and c2.crm = 'instagram' and not c2.purged
              and exists (
                select 1 from jsonb_array_elements(c2.channels) ch
                 where ch->>'kind' = 'instagram' and {CHANNEL_USERNAME} = %(name)s)
            order by c2.deleted_at nulls first, c2.created_at
            limit 1)
        returning c.id
        """,
        {"owner": owner_id, "name": username, "when": when},
    )
    row = cur.fetchone()
    return str(row["id"]) if row else None


def save(cur, owner_id: str, job_id: str, candidate: dict, now: datetime) -> tuple[str, str]:
    """(outcome, contact id): 'added' for a new contact, 'updated' for a known one."""
    profile = candidate.get("profile") or {}
    username = candidate["username"]
    instagram_id = candidate.get("instagram_id") or profile.get("instagram_id")
    # One save per owner at a time: two jobs finding the same person make one contact.
    cur.execute("select pg_advisory_xact_lock(hashtext('cloud_crm:' || %s))", (owner_id,))
    row = None
    if instagram_id:
        cur.execute(
            """
            select id, channels, name from public.crm_contacts
             where owner_id = %s and crm = 'instagram' and instagram_id = %s and not purged
            """,
            (owner_id, instagram_id),
        )
        row = cur.fetchone()
    if row is None:
        cur.execute(
            f"""
            select c.id, c.channels, c.name, c.instagram_id from public.crm_contacts c
             where c.owner_id = %(owner)s and c.crm = 'instagram' and not c.purged
               and exists (
                 select 1 from jsonb_array_elements(c.channels) ch
                  where ch->>'kind' = 'instagram' and {CHANNEL_USERNAME} = %(name)s)
             order by c.deleted_at nulls first, c.created_at
             limit 1
            """,
            {"owner": owner_id, "name": username},
        )
        row = cur.fetchone()
        # A known contact with another Instagram ID is someone else who took the name.
        if row is not None and row.get("instagram_id") and instagram_id:
            if row["instagram_id"] != instagram_id:
                row = None
    info = Jsonb(cloud_info(candidate, job_id, now))
    emails, phones = profile.get("emails") or [], profile.get("phones") or []
    if row is not None:
        cur.execute(
            """
            update public.crm_contacts
               set instagram_id = coalesce(instagram_id, %s),
                   channels = %s,
                   cloud = %s,
                   name = case when name = '' then %s else name end
             where id = %s
            """,
            (
                instagram_id,
                Jsonb(_channels(row["channels"], username, emails, phones)),
                info,
                profile.get("full_name") or username,
                row["id"],
            ),
        )
        return "updated", str(row["id"])
    status = STATUS_LABELS.get(candidate.get("category") or "")
    cur.execute(
        """
        insert into public.crm_contacts
          (owner_id, crm, name, statuses, channels, source, cloud, instagram_id)
        values (%s, 'instagram', %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            owner_id,
            (profile.get("full_name") or username)[:160],
            Jsonb([status] if status else []),
            Jsonb(_channels([], username, emails, phones)),
            SOURCE,
            info,
            instagram_id,
        ),
    )
    return "added", str(cur.fetchone()["id"])
