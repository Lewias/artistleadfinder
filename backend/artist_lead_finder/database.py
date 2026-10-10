"""SQLite lifecycle and explicit initial schema version."""

import os
from pathlib import Path

from platformdirs import user_data_path
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, SchemaMigration, Setting


class NewerDatabaseError(RuntimeError):
    """The database was migrated by a newer version; opening it here could damage it."""


# A separate data folder for build checks, so they never touch the real account and leads.
DATA_DIR_ENV = "ARTIST_LEAD_FINDER_DATA"
# (setting, old default, new default) of schema 16.
PACE_DEFAULTS_16 = (
    ("page_delay_min", 8, 4),
    ("page_delay_max", 20, 10),
    ("profiles_per_hour", 60, 200),
)
# Schema 17: the hourly profile cap on its old default goes from 200 to 400.
HOURLY_CAP_17 = ("profiles_per_hour", 200, 400)


def application_data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return user_data_path("ArtistLeadFinder", appauthor=False, roaming=False)


def open_database(path: Path | None = None) -> tuple[Engine, sessionmaker[Session]]:
    path = path or application_data_dir() / "artist-leads.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def configure_connection(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    # Reject newer schemas rather than silently opening an incompatible database.
    SchemaMigration.__table__.create(engine, checkfirst=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        versions = list(session.scalars(select(SchemaMigration.version)))
        if any(version > 21 for version in versions):
            engine.dispose()
            raise NewerDatabaseError(
                "База данных создана более новой версией Artist Lead Finder. Установите "
                "последнюю версию приложения — лиды, CRM и аккаунты сохранятся."
            )
    Base.metadata.create_all(engine)
    # Schemas 4 and 5 add columns to existing tables, which create_all does not do.
    added = {
        "search_jobs": {"hidden": "BOOLEAN NOT NULL DEFAULT 0"},
        "leads": {
            "do_not_contact": "BOOLEAN NOT NULL DEFAULT 0",
            "last_contacted_at": "DATETIME",
        },
        "outreach_templates": {"hidden": "BOOLEAN NOT NULL DEFAULT 0"},
        "outreach_campaigns": {"message_variants": "JSON NOT NULL DEFAULT '[]'"},
        "scout_runs": {
            "backlog": "JSON NOT NULL DEFAULT '[]'",
            "found": "INTEGER NOT NULL DEFAULT 0",
            "stats": "JSON NOT NULL DEFAULT '{}'",
            "started_at": "DATETIME",
        },
        "scout_processed_profiles": {
            "instagram_user_id": "VARCHAR(40)",
            "category": "VARCHAR(20)",
            "confidence": "INTEGER",
        },
        "scout_processed_posts": {
            "status": "VARCHAR(20) NOT NULL DEFAULT 'processed'",
            "attempts": "INTEGER NOT NULL DEFAULT 1",
            "last_error": "VARCHAR(200)",
        },
        "lead_scout_profiles": {
            "profile_decided_by": "VARCHAR(10) NOT NULL DEFAULT 'local'",
            "category_name": "VARCHAR(120)",
            "is_business": "BOOLEAN",
            "bio_links": "JSON NOT NULL DEFAULT '[]'",
            "local_category": "VARCHAR(20)",
            "local_confidence": "INTEGER",
            "ai_category": "VARCHAR(20)",
            "origin_id": "VARCHAR(120)",
        },
        "imessage_workspace": {
            "messages": "JSON NOT NULL DEFAULT '[]'",
            "sequence": "JSON NOT NULL DEFAULT '[]'",
        },
        "imessage_campaigns": {"steps": "JSON NOT NULL DEFAULT '[]'"},
        "imessage_jobs": {"step": "INTEGER NOT NULL DEFAULT 0"},
        "crm_contacts": {
            "remote_id": "VARCHAR(36)",
            "owner_id": "VARCHAR(36)",
            "owner_name": "VARCHAR(160) NOT NULL DEFAULT ''",
            "dirty": "BOOLEAN NOT NULL DEFAULT 1",
            "source": "VARCHAR(40) NOT NULL DEFAULT ''",
            "cloud": "JSON",
        },
        "crm_statuses": {"emoji": "VARCHAR(16) NOT NULL DEFAULT ''"},
        "scout_sources": {
            "last_scanned_at": "DATETIME",
            "status": "VARCHAR(40) NOT NULL DEFAULT 'new'",
            "leads_found": "INTEGER NOT NULL DEFAULT 0",
            "added_at": "DATETIME",
            "candidates_found": "INTEGER NOT NULL DEFAULT 0",
            "profiles_resolved": "INTEGER NOT NULL DEFAULT 0",
            "profiles_skipped": "INTEGER NOT NULL DEFAULT 0",
            "errors_count": "INTEGER NOT NULL DEFAULT 0",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, definitions in added.items():
            columns = {column["name"] for column in inspector.get_columns(table)}
            for column, definition in definitions.items():
                if column not in columns:
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    )
        # Schema 8 replaces the username-keyed AI cache (no classifier version) with
        # scout_ai_classifications; the old rows are only a cache.
        connection.execute(text("DROP TABLE IF EXISTS scout_ai_cache"))
        # Stories were removed from the scout in 0.10 with their memory of processed frames.
        connection.execute(text("DROP TABLE IF EXISTS scout_processed_stories"))
        # Schema 9: run history order for runs created before the column existed.
        connection.execute(
            text(
                "UPDATE scout_runs SET started_at = (SELECT COALESCE(started_at, created_at)"
                " FROM search_jobs WHERE search_jobs.id = scout_runs.job_id)"
                " WHERE started_at IS NULL"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_scout_runs_started_at ON scout_runs (started_at)")
        )
        # Schema 15: indexes of the columns added to crm_contacts above.
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_crm_contacts_remote"
                " ON crm_contacts (remote_id)"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_crm_contacts_dirty ON crm_contacts (dirty)")
        )
    with factory.begin() as session:
        if session.get(SchemaMigration, 1) is None:
            session.add(SchemaMigration(version=1))
        if session.get(SchemaMigration, 2) is None:
            session.add(SchemaMigration(version=2))
        if session.get(SchemaMigration, 3) is None:
            session.add(SchemaMigration(version=3))
        if session.get(SchemaMigration, 4) is None:
            session.add(SchemaMigration(version=4))
        if session.get(SchemaMigration, 5) is None:
            session.add(SchemaMigration(version=5))
        if session.get(SchemaMigration, 6) is None:
            session.add(SchemaMigration(version=6))
        # Schema 7: scout_profile_cache (new table, created by create_all).
        if session.get(SchemaMigration, 7) is None:
            session.add(SchemaMigration(version=7))
        # Schema 8: scout_ai_classifications, scout_pending_ai_jobs,
        # lead_scout_profiles.profile_decided_by; scout_ai_cache removed.
        if session.get(SchemaMigration, 8) is None:
            session.add(SchemaMigration(version=8))
        # Schema 9: scout_lead_sources, scout_decisions, scout_runs.started_at, lead and
        # processed-profile details, per-source totals.
        if session.get(SchemaMigration, 9) is None:
            session.add(SchemaMigration(version=9))
        # Schema 10: outreach (templates, campaigns, recipients, outbound jobs,
        # conversations, messages, follow-up sequences and jobs, sender health, events)
        # and leads.do_not_contact / last_contacted_at.
        if session.get(SchemaMigration, 10) is None:
            session.add(SchemaMigration(version=10))
        # Schema 11: primary-outreach list (outreach_workspace), campaign message variants,
        # hidden templates.
        if session.get(SchemaMigration, 11) is None:
            session.add(SchemaMigration(version=11))
        # Schema 12: iMessage through the iPhone (workspace, attachments, campaigns, jobs,
        # events); new tables only.
        if session.get(SchemaMigration, 12) is None:
            session.add(SchemaMigration(version=12))
        # Schema 13: the Instagram and iMessage CRMs (crm_contacts, crm_statuses); new
        # tables only.
        if session.get(SchemaMigration, 13) is None:
            session.add(SchemaMigration(version=13))
        # Schema 14: iMessage templates (imessage_templates) and chains of messages
        # (workspace.sequence, campaigns.steps, jobs.step).
        if session.get(SchemaMigration, 14) is None:
            session.add(SchemaMigration(version=14))
        # Schema 15: the shared CRM (crm_contacts remote id, owner, dirty; crm_tombstones).
        if session.get(SchemaMigration, 15) is None:
            session.add(SchemaMigration(version=15))
        # Schema 16: a faster scout pace (pauses and the hourly cap still on the old defaults
        # take the new ones; values someone chose themselves stay).
        if session.get(SchemaMigration, 16) is None:
            for key, before, after in PACE_DEFAULTS_16:
                setting = session.get(Setting, key)
                if setting is not None and setting.value == before:
                    setting.value = after
            session.add(SchemaMigration(version=16))
        if session.get(SchemaMigration, 17) is None:
            key, before, after = HOURLY_CAP_17
            setting = session.get(Setting, key)
            if setting is not None and setting.value == before:
                setting.value = after
            session.add(SchemaMigration(version=17))
        # Schema 18: crm_statuses.emoji.
        if session.get(SchemaMigration, 18) is None:
            session.add(SchemaMigration(version=18))
        # Schema 19: reading the outreach threads (inbox_scans, inbox_scan_items,
        # inbox_findings); new tables only.
        if session.get(SchemaMigration, 19) is None:
            session.add(SchemaMigration(version=19))
        # Schema 20: crm_contacts.source and .cloud, what the cloud parser found.
        if session.get(SchemaMigration, 20) is None:
            session.add(SchemaMigration(version=20))
        # Schema 21: the assistant (assistant_turns, assistant_drafts, assistant_usage,
        # assistant_actions); new tables only.
        if session.get(SchemaMigration, 21) is None:
            session.add(SchemaMigration(version=21))
    return engine, factory
