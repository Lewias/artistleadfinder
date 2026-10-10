"""A real Postgres with the server migrations and stand-ins for the parser's core.

Postgres comes from `pgserver` (a local build, no Docker); each test gets a fresh
database. Supabase's `auth` schema is reduced to what the migrations use: `auth.users`
and `auth.uid()` from the request claims, as PostgREST sets them. Extensions the stand-in
lacks (pgcrypto for access keys) are not needed by the cloud parser.
"""

import itertools
import json
import os
import re
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[3]
# The parser runs the app's own core.
sys.path.insert(0, str(ROOT / "backend"))

from cloud_worker.runner import Runner  # noqa: E402

MIGRATIONS = ROOT / "supabase" / "migrations"
PGDATA = os.environ.get("CLOUD_TEST_PGDATA", "C:/Users/Public/alf-cloud-test/pgdata")
PROFILE = "a" * 32
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
    name = f"cloud_{os.getpid()}_{next(_counter)}"
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
    """Calls the database the way PostgREST does for a signed-in user."""

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

    def put_session(self, profile=PROFILE, cookies=None, proxy=None):
        record = {"cookies": cookies or [{"name": "sessionid", "value": "s1"}], "proxy": proxy}
        self.run(
            "select public.cloud_session_put(%s, %s, %s::jsonb)",
            (profile, "Рабочий", json.dumps(record)),
        )

    def start(self, params: dict, request_id: str | None = None) -> str:
        rows = self.run(
            "select public.cloud_start(%s, %s::jsonb) as id",
            (request_id or uuid.uuid4().hex, json.dumps(params)),
        )
        return str(rows[0]["id"])

    def cancel(self, job_id: str) -> None:
        self.run("select public.cloud_cancel(%s)", (job_id,))


def params(**overrides) -> dict:
    base = {
        "profile_id": PROFILE,
        "sources": ["rapgoat.tv"],
        "categories": ["ARTIST", "PRODUCER"],
        "target": 3,
        "settings": {"scout_methods": ["posts"]},
    }
    base.update(overrides)
    return base


def lead(name, category="ARTIST", emails=()):
    return {
        "username": name,
        "instagram_id": f"id-{name}",
        "via": ["posts"],
        "origins": [
            {"via": "posts", "source": "rapgoat.tv", "post": "https://www.instagram.com/p/C1/"}
        ],
        "category": category,
        "confidence": 80,
        "reason": "ссылки на релизы",
        "ai_ok": True,
        "profile": {
            "instagram_id": f"id-{name}",
            "username": name,
            "full_name": name.title(),
            "biography": "Rapper",
            "links": [],
            "emails": list(emails),
            "phones": [],
            "followers": 1500,
        },
    }


class FakeEngine:
    """The core of one user: what a scout run would find, without a browser."""

    def __init__(self, owner):
        self.owner = owner
        self.runs: list[dict] = []
        self.sessions: dict = {}
        self.settings: dict = {}
        self.calls: list[tuple] = []
        self.pending: list[dict] = []
        self.closed = False
        self.cancelled_open = 0

    def put_session(self, profile, name, record):
        self.sessions[profile] = record

    def session(self, profile):
        return {**self.sessions[profile], "cookies": [{"name": "sessionid", "value": "renewed"}]}

    def apply_settings(self, settings):
        self.settings = settings

    def cancel_open_runs(self):
        self.cancelled_open += 1

    def start(self, profile, sources, target):
        self.runs.append({"profile": profile, "sources": sources, "target": target})
        return len(self.runs)

    def new_leads(self, run_id):
        found, self.pending = self.pending, []
        return found

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "browser.runtime.is_open":
            return {"open": True}
        return {"ok": True}

    def close(self):
        self.closed = True


class ScriptedDriver:
    """Plays queue states to the runner's tick, adding leads as the 'parser' finds them."""

    def __init__(self, engine, script):
        self.engine = engine
        self.script = script
        self.decisions = []

    def run(self, job_id, tick):
        state = {"status": "running"}
        final = "completed"
        for step in self.script:
            self.engine.pending += step.get("leads", [])
            state = {"status": "running", "profile_id": PROFILE, **step.get("state", {})}
            decision = tick(state)
            self.decisions.append(decision)
            if callable(step.get("then")):
                step["then"]()
            final = step.get("final", final)
            if decision == "stop":
                return state
        return {**state, "status": final}


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        # Every tick is far enough apart for the runner's checks.
        self.value += 10
        return self.value


def make_runner(dsn, scripts, engines=None, worker_id="cloud-test"):
    engines = engines if engines is not None else {}

    def engine_for(owner):
        engines[owner] = FakeEngine(owner)
        return engines[owner]

    def driver_for(engine):
        return ScriptedDriver(engine, scripts.pop(0) if scripts else [])

    return Runner(
        lambda: psycopg.connect(dsn, autocommit=True, row_factory=dict_row),
        engine_for,
        driver_for,
        worker_id=worker_id,
        clock=Clock(),
    )
