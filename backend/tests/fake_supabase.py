"""A loopback stand-in for the Supabase project: Auth tokens, the account RPCs of
supabase/migrations and the CRM tables, with the same access rules. No network."""

import itertools
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeSupabase:
    def __init__(self):
        self.users: dict[str, dict] = {}  # id -> {email, password, role, blocked, name}
        self.keys: dict[str, dict] = {}  # key -> {user_id, activated, revoked}
        self.tokens: dict[str, str] = {}  # access token -> user id
        self.refresh: dict[str, str] = {}  # refresh token -> user id
        self.tables: dict[str, list[dict]] = {"crm_contacts": [], "crm_status_sets": []}
        # Cloud parser jobs and their candidates; only the owner reads them.
        self.cloud: dict[str, list[dict]] = {"cloud_jobs": [], "cloud_candidates": []}
        # (owner, profile) -> {name, record}: what the parser logs in with.
        self.cloud_sessions: dict[tuple[str, str], dict] = {}
        # Telegram: user id -> linked chat name; user id -> the code the app shows.
        self.tg_links: dict[str, str] = {}
        self.tg_codes: dict[str, str] = {}
        self.down = False
        self.counter = itertools.count(1)
        self.requests: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def add_user(self, email, password="secret-pass", role="user", name=""):
        user_id = f"00000000-0000-0000-0000-{next(self.counter):012d}"
        self.users[user_id] = {
            "email": email,
            "password": password,
            "role": role,
            "blocked": False,
            "name": name,
        }
        return user_id

    def add_key(self, key, user_id=None, role=None):
        # A key for an account grants the role it already has (an admin keeps admin).
        if role is None:
            current = self.users[user_id]["role"] if user_id else "user"
            role = current if current in ("user", "moderator") else "user"
        self.keys[key] = {"user_id": user_id, "activated": False, "revoked": False, "role": role}

    # ---------- behaviour ----------

    def _tokens(self, user_id):
        n = next(self.counter)
        access, refresh = f"access-{n}", f"refresh-{n}"
        self.tokens[access] = user_id
        self.refresh[refresh] = user_id
        return {"access_token": access, "refresh_token": refresh, "expires_in": 3600}

    def _status(self, user_id):
        user = self.users[user_id]
        licensed = any(
            k["user_id"] == user_id and k["activated"] and not k["revoked"]
            for k in self.keys.values()
        )
        return {
            "user_id": user_id,
            "email": user["email"],
            "display_name": user["name"],
            "role": user["role"],
            "blocked": user["blocked"],
            "licensed": licensed,
        }

    def _activate(self, user_id, key):
        found = self.keys.get(key)
        if found is None or found["revoked"]:
            return 400, {"code": "P0002", "message": "Ключ не найден или отозван."}
        if found["user_id"] not in (None, user_id):
            return 400, {"code": "42501", "message": "Ключ уже используется другим аккаунтом."}
        found.update(user_id=user_id, activated=True)
        if self.users[user_id]["role"] != "admin":
            self.users[user_id]["role"] = found.get("role", "user")
        return 200, self._status(user_id)

    def _admin(self, user_id):
        user = self.users[user_id]
        return user["role"] == "admin" and not user["blocked"]

    def _sees_all(self, user_id):
        user = self.users[user_id]
        moderator = user["role"] == "moderator" and not user["blocked"]
        return self._admin(user_id) or (moderator and self._status(user_id)["licensed"])

    def _profiles(self, user_id):
        return [
            {
                "id": key,
                "email": value["email"],
                "display_name": value["name"],
                "role": value["role"],
                "blocked": value["blocked"],
                "created_at": key,
            }
            for key, value in self.users.items()
            if self._sees_all(user_id) or key == user_id
        ]

    def _key_rows(self, user_id):
        return [
            {
                "id": f"10000000-0000-0000-0000-{index:012d}",
                "key_hint": key[-4:],
                "user_id": value["user_id"],
                "device_id": None,
                "note": value.get("note", ""),
                "role": value.get("role", "user"),
                "created_at": None,
                "activated_at": "now" if value["activated"] else None,
                "revoked_at": "now" if value["revoked"] else None,
            }
            for index, (key, value) in enumerate(self.keys.items(), start=1)
            if self._admin(user_id) or value["user_id"] == user_id
        ]

    def _key_by_id(self, key_id):
        for index, key in enumerate(self.keys, start=1):
            if f"10000000-0000-0000-0000-{index:012d}" == key_id:
                return self.keys[key]
        return None

    def _admin_rpc(self, user_id, name, body):
        if not self._admin(user_id):
            return 400, {"code": "42501", "message": "Доступно только админу."}
        if name == "admin_issue_key":
            key = f"ALF-NEW{next(self.counter):05d}-AAAAA-BBBBB-CCCCC"
            role = body.get("p_role", "user")
            if body.get("p_user") and self.users[body["p_user"]]["role"] != "admin":
                self.users[body["p_user"]]["role"] = role
            self.keys[key] = {
                "role": role,
                "user_id": body.get("p_user"),
                "activated": False,
                "revoked": False,
                "note": body.get("p_note", ""),
            }
            return 200, key
        if name == "admin_set_role" and body["p_role"] not in ("user", "moderator"):
            message = "Можно назначить только пользователя или модератора."
            return 400, {"code": "22023", "message": message}
        if name == "admin_set_role" and self.users[body["p_user"]]["role"] == "admin":
            if body["p_user"] != user_id:
                return 400, {"code": "42501", "message": "Роль админа меняется только на сервере."}
        if name in ("admin_set_role", "admin_set_blocked", "admin_set_name"):
            if body["p_user"] == user_id and name != "admin_set_name":
                return 400, {"code": "42501", "message": "Свою роль менять нельзя."}
            field = {
                "admin_set_role": ("role", "p_role"),
                "admin_set_blocked": ("blocked", "p_blocked"),
                "admin_set_name": ("name", "p_name"),
            }[name]
            self.users[body["p_user"]][field[0]] = body[field[1]]
            return 200, None
        found = self._key_by_id(body.get("p_id"))
        if found is not None and name == "admin_revoke_key":
            found["revoked"] = True
        if found is not None and name == "admin_unbind_key":
            found.update(user_id=None, activated=False)
        return 200, None

    def _cloud_session_put(self, user_id, body):
        self.cloud_sessions[(user_id, body["p_profile"])] = {
            "name": body["p_name"],
            "record": body["p_record"],
        }
        return 204, None

    def _cloud_sessions_list(self, user_id):
        return 200, [
            {
                "profile_id": profile,
                "name": item["name"],
                "has_proxy": bool(item["record"].get("proxy")),
            }
            for (owner, profile), item in self.cloud_sessions.items()
            if owner == user_id
        ]

    def _cloud_start(self, user_id, body):
        jobs = self.cloud["cloud_jobs"]
        for job in jobs:
            if job["owner_id"] == user_id and job["request_id"] == body["p_request_id"]:
                return 200, job["id"]
        session = self.cloud_sessions.get((user_id, body["p_params"]["profile_id"]))
        if session is None:
            return 400, {"message": "Сессия этого аккаунта Instagram не отправлена на сервер."}
        kind = body["p_params"].get("kind") or "scout"
        if kind == "outreach" and not session["record"].get("proxy"):
            return 400, {"message": "Для облачной рассылки у аккаунта должен быть прокси."}
        active = [
            job
            for job in jobs
            if job["owner_id"] == user_id
            and job["kind"] == kind
            and job["stage"] not in ("completed", "failed", "cancelled")
        ]
        if kind == "outreach" and active:
            return 400, {
                "message": "Облачная рассылка уже идёт. Дождитесь конца или остановите её."
            }
        if len(active) >= 2:
            return 400, {
                "message": "Уже идёт 2 облачных задачи. Дождитесь конца или отмените одну."
            }
        params = {key: value for key, value in body["p_params"].items() if key != "kind"}
        job = {
            "id": f"00000000-0000-4000-8000-{next(self.counter):012d}",
            "owner_id": user_id,
            "request_id": body["p_request_id"],
            "profile_id": body["p_params"]["profile_id"],
            "kind": kind,
            "params": params,
            "stage": "queued",
            "cancel_requested": False,
            "progress": {},
            "counters": {},
            "error": None,
            "created_at": stamp(),
            "updated_at": stamp(),
            "finished_at": None,
        }
        jobs.append(job)
        return 200, job["id"]

    def _cloud_unwritten(self, user_id, body):
        """New leads of the user's parser jobs that no outreach job has taken."""
        jobs = {job["id"]: job for job in self.cloud["cloud_jobs"] if job["owner_id"] == user_id}
        taken = {
            name
            for job in jobs.values()
            if job["kind"] == "outreach"
            for name in job["params"].get("usernames") or []
        }
        names = []
        for row in reversed(self.cloud["cloud_candidates"]):
            job = jobs.get(row["job_id"])
            if job and job["kind"] == "scout" and row.get("outcome") == "added":
                if row["username"] not in taken and row["username"] not in names:
                    names.append(row["username"])
        return 200, [{"username": name} for name in names[: int(body.get("p_limit") or 500)]]

    def _cloud_cancel(self, user_id, body):
        for job in self.cloud["cloud_jobs"]:
            if job["id"] == body["p_job"] and job["owner_id"] == user_id:
                job["cancel_requested"] = True
                if job["stage"] == "queued":
                    job["stage"] = "cancelled"
                return 204, None
        return 404, {"message": "Задача не найдена."}

    def _telegram(self, user_id, name):
        if name == "tg_link_start":
            self.tg_codes[user_id] = f"{next(self.counter):010X}"
            return 200, {
                "code": self.tg_codes[user_id],
                "expires_at": stamp(),
                "bot": "alf_test_bot",
            }
        if name == "tg_unlink":
            self.tg_links.pop(user_id, None)
            return 204, None
        linked = user_id in self.tg_links
        return 200, {
            "linked": linked,
            "username": self.tg_links.get(user_id, ""),
            "notify": True,
            "bot": "alf_test_bot",
        }

    def _cloud_rows(self, user_id, table, query):
        def match(row, key):
            wanted = (query.get(key) or [""])[0]
            if not wanted:
                return True
            if wanted.startswith("eq."):
                return str(row.get(key)) == wanted[3:]
            if wanted.startswith("in.("):
                return str(row.get(key)) in wanted[4:-1].split(",")
            return True

        rows = [
            {k: v for k, v in row.items() if k != "owner_id"}
            for row in self.cloud[table]
            if row["owner_id"] == user_id
            and all(match(row, key) for key in ("id", "job_id", "outcome", "state"))
        ]
        if table == "cloud_jobs":
            rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows

    def _visible(self, user_id, row):
        user = self.users[user_id]
        return self._sees_all(user_id) or (row["owner_id"] == user_id and not user["blocked"])

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"null") if length else None

            def _user(self):
                token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                return fake.tokens.get(token)

            def do_POST(self):
                self._route("POST")

            def do_GET(self):
                self._route("GET")

            def do_PATCH(self):
                self._route("PATCH")

            def _route(self, method):
                fake.requests.append(f"{method} {self.path}")
                if fake.down:
                    return self._send(503, {"message": "unavailable"})
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                body = self._body()
                if url.path == "/auth/v1/token":
                    grant = query.get("grant_type", [""])[0]
                    if grant == "password":
                        for user_id, user in fake.users.items():
                            if (
                                user["email"] == body["email"]
                                and user["password"] == body["password"]
                            ):
                                return self._send(200, fake._tokens(user_id))
                        return self._send(
                            400,
                            {
                                "error": "invalid_grant",
                                "error_description": "Invalid login credentials",
                            },
                        )
                    user_id = fake.refresh.pop(body.get("refresh_token"), None)
                    if not user_id:
                        return self._send(
                            400,
                            {
                                "error": "invalid_grant",
                                "error_description": "Invalid Refresh Token",
                            },
                        )
                    return self._send(200, fake._tokens(user_id))
                if url.path == "/auth/v1/signup":
                    if any(u["email"] == body["email"] for u in fake.users.values()):
                        return self._send(
                            422,
                            {"error_code": "user_already_exists", "msg": "User already registered"},
                        )
                    new = fake.add_user(
                        body["email"], body["password"], name=body["data"]["display_name"]
                    )
                    return self._send(200, {**fake._tokens(new), "user": {"id": new}})
                if url.path == "/auth/v1/logout":
                    return self._send(204)
                user_id = self._user()
                if user_id is None:
                    return self._send(401, {"message": "JWT expired"})
                if url.path == "/rest/v1/rpc/session_status":
                    return self._send(200, fake._status(user_id))
                if url.path.startswith("/rest/v1/rpc/admin_"):
                    return self._send(
                        *fake._admin_rpc(user_id, url.path.removeprefix("/rest/v1/rpc/"), body)
                    )
                if url.path == "/functions/v1/admin-users":
                    if not fake._admin(user_id):
                        return self._send(403, {"error": "Доступно только админу."})
                    if body["action"] == "create":
                        if any(u["email"] == body["email"] for u in fake.users.values()):
                            return self._send(400, {"error": "Такой email уже есть."})
                        new = fake.add_user(
                            body["email"], body["password"], name=body["display_name"]
                        )
                        return self._send(200, {"id": new, "email": body["email"]})
                    fake.users[body["user_id"]]["password"] = body["password"]
                    return self._send(200, {"ok": True})
                if url.path == "/rest/v1/license_keys":
                    return self._send(200, fake._key_rows(user_id))
                if url.path == "/rest/v1/rpc/cloud_session_put":
                    return self._send(*fake._cloud_session_put(user_id, body))
                if url.path == "/rest/v1/rpc/cloud_sessions_list":
                    return self._send(*fake._cloud_sessions_list(user_id))
                if url.path == "/rest/v1/rpc/cloud_start":
                    return self._send(*fake._cloud_start(user_id, body))
                if url.path == "/rest/v1/rpc/cloud_unwritten":
                    return self._send(*fake._cloud_unwritten(user_id, body))
                if url.path in (
                    "/rest/v1/rpc/tg_link_start",
                    "/rest/v1/rpc/tg_status",
                    "/rest/v1/rpc/tg_unlink",
                ):
                    return self._send(*fake._telegram(user_id, url.path.rsplit("/", 1)[1]))
                if url.path == "/rest/v1/rpc/cloud_cancel":
                    return self._send(*fake._cloud_cancel(user_id, body))
                if url.path.removeprefix("/rest/v1/") in fake.cloud and method == "GET":
                    table = url.path.removeprefix("/rest/v1/")
                    return self._send(200, fake._cloud_rows(user_id, table, query))
                if url.path == "/rest/v1/rpc/activate_key":
                    if fake.users[user_id]["blocked"]:
                        return self._send(400, {"message": "Аккаунт заблокирован или не найден."})
                    return self._send(*fake._activate(user_id, body["p_key"]))
                table = url.path.removeprefix("/rest/v1/")
                if table == "profiles":
                    return self._send(200, fake._profiles(user_id))
                if table in fake.tables:
                    return self._table(method, table, query, body, user_id)
                return self._send(404, {"message": "not found"})

            def _table(self, method, table, query, body, user_id):
                rows = fake.tables[table]
                if method == "GET":
                    since = (query.get("updated_at") or [""])[0].removeprefix("gt.")
                    owner = (query.get("owner_id") or [""])[0].removeprefix("eq.")
                    found = [
                        row
                        for row in rows
                        if fake._visible(user_id, row)
                        and (not since or row["updated_at"] > since)
                        and (not owner or row["owner_id"] == owner)
                    ]
                    return self._send(200, sorted(found, key=lambda row: row["updated_at"]))
                if method == "POST":
                    keys = ("owner_id", "crm") if table == "crm_status_sets" else ("id",)
                    saved = []
                    for incoming in body:
                        incoming = {**incoming}
                        incoming.setdefault("owner_id", user_id)
                        existing = next(
                            (r for r in rows if all(r.get(k) == incoming.get(k) for k in keys)),
                            None,
                        )
                        target = existing or {"owner_id": incoming["owner_id"]}
                        if not fake._visible(user_id, {**target, **incoming}):
                            return self._send(403, {"message": "row-level security"})
                        owner = target["owner_id"]
                        target.update(incoming)
                        target["owner_id"] = owner
                        target["updated_at"] = stamp()
                        target["updated_by"] = user_id
                        if existing is None:
                            rows.append(target)
                        saved.append(dict(target))
                    return self._send(201, saved)
                if method == "PATCH":
                    ids = (query.get("id") or [""])[0].removeprefix("in.(").rstrip(")").split(",")
                    changed = []
                    for row in rows:
                        if row.get("id") in ids and fake._visible(user_id, row):
                            row.update(body)
                            row["updated_at"] = stamp()
                            row["updated_by"] = user_id
                            changed.append(dict(row))
                    return self._send(200, changed)
                return self._send(405, {"message": "method"})

        return Handler
