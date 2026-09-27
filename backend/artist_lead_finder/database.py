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
        if any(version > 4 for version in versions):
            engine.dispose()
            raise RuntimeError("База создана более новой версией приложения.")
    Base.metadata.create_all(engine)
    # Schema 4 adds columns to an existing table, which create_all does not do.
    columns = {column["name"] for column in inspect(engine).get_columns("scout_runs")}
    with engine.begin() as connection:
        if "backlog" not in columns:
            connection.execute(
                text("ALTER TABLE scout_runs ADD COLUMN backlog JSON NOT NULL DEFAULT '[]'")
            )
        if "found" not in columns:
            connection.execute(
                text("ALTER TABLE scout_runs ADD COLUMN found INTEGER NOT NULL DEFAULT 0")
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
    return engine, factory
