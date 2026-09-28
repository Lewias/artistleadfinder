"""SQLite lifecycle and explicit initial schema version."""

from pathlib import Path

from platformdirs import user_data_path
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, SchemaMigration


def application_data_dir() -> Path:
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
        if any(version > 5 for version in versions):
            engine.dispose()
            raise RuntimeError("База создана более новой версией приложения.")
    Base.metadata.create_all(engine)
    # Schemas 4 and 5 add columns to existing tables, which create_all does not do.
    added = {
        "scout_runs": {
            "backlog": "JSON NOT NULL DEFAULT '[]'",
            "found": "INTEGER NOT NULL DEFAULT 0",
            "stats": "JSON NOT NULL DEFAULT '{}'",
        },
        "scout_sources": {
            "last_scanned_at": "DATETIME",
            "status": "VARCHAR(40) NOT NULL DEFAULT 'new'",
            "leads_found": "INTEGER NOT NULL DEFAULT 0",
            "added_at": "DATETIME",
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
    return engine, factory
