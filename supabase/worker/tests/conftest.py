"""A real Postgres with the server migrations.

Postgres comes from `pgserver` (a local build, no Docker); each test gets a fresh
database. Supabase's `auth` schema is reduced to what the migrations use: `auth.users`
and `auth.uid()` from the request claims, as PostgREST sets them. Extensions the stand-in
lacks (pgcrypto for access keys) are not needed by the bot.
"""

import itertools
import json
import os
import re
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
PGDATA = os.environ.get("CLOUD_TEST_PGDATA", "C:/Users/Public/alf-cloud-test/pgdata")
_counter = itertools.count(1)

AUTH = """
create schema if not exists auth;
create schema if not exists extensions;
create table auth.users (
  id uuid primary key,
  email text,
  raw_user_meta_data jsonb not null default '{}'
);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true)::json ->> 'sub', '')::uuid
$$;
"""


@pytest.fixture(scope="session")
def server():
    import pgserver

    srv = pgserver.get_server(PGDATA, cleanup_mode="stop")
    with psycopg.connect(srv.get_uri(), autocommit=True) as conn:
        for role in ("anon", "authenticated", "service_role"):
            if not conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone():
                conn.execute(f"create role {role} nologin")
    yield srv


def _migration(text: str) -> str:
    # pgcrypto is not part of the local build; its functions are not used here.
    return re.sub(r"create extension if not exists pgcrypto;", "", text)


@pytest.fixture
def dsn(server):
    name = f"bot_{os.getpid()}_{next(_counter)}"
    base = server.get_uri()
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"drop database if exists {name}")
        conn.execute(f"create database {name}")
    url = base.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("set check_function_bodies = off")
        conn.execute(AUTH)
        for path in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(_migration(path.read_text(encoding="utf-8")))
        conn.execute("grant usage on schema public, auth to authenticated, anon")
    yield url
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s", (name,)
        )
        conn.execute(f"drop database if exists {name}")


@pytest.fixture
def db(dsn):
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        yield conn


def add_user(db, email: str) -> str:
    user_id = str(uuid.uuid4())
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))
    # An account works with an activated access key (migration 0002).
    db.execute(
        "insert into public.license_keys (key_hash, key_hint, user_id, activated_at) "
        "values (%s, 'TEST', %s, now())",
        (uuid.uuid4().hex, user_id),
    )
    return user_id


class AsUser:
    """Calls the database the way PostgREST does for a signed-in user (the app)."""

    def __init__(self, dsn: str, user_id: str):
        self.dsn = dsn
        self.user_id = user_id

    def run(self, sql: str, params=()):
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                conn.execute("set local role authenticated")
                conn.execute(
                    "select set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"sub": self.user_id, "role": "authenticated"}),),
                )
                cur = conn.execute(sql, params)
                return cur.fetchall() if cur.description else None

    def take(self) -> list[dict]:
        """What the app does every few seconds: the questions waiting for this account."""
        rows = self.run("select request_id, question from public.tg_requests_take()")
        return sorted(rows, key=lambda row: row["request_id"])

    def answer(self, request_id: int, text: str) -> None:
        self.run("select public.tg_request_answer(%s, %s)", (request_id, text))
