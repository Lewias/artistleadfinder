"""«Облачный парсер»: the app's own Lead Scout running on the account server.

The app sends the chosen browser profile's Instagram session (cookies and proxy) and the
current Scout settings, creates the job and reads its progress and results with the
user's own session; the server checks the owner on every call. The parser goes on with
the app closed, and the leads it saves arrive in the Instagram CRM with the usual sync.
"""

import re
import uuid

from .browser_capture import profile_url
from .errors import UserError

JOB_COLUMNS = (
    "id,request_id,kind,profile_id,params,stage,cancel_requested,progress,counters,error,"
    "created_at,updated_at,started_at,finished_at"
)
CANDIDATE_COLUMNS = (
    "id,username,instagram_id,via,origins,profile,category,confidence,reason,outcome,note,"
    "contact_id,updated_at"
)
JOB_ID = re.compile(r"^[0-9a-f-]{36}$")
PROFILE_ID = re.compile(r"^[0-9a-f]{32}$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
CATEGORIES = ("ARTIST", "PRODUCER", "MEDIA", "OTHER", "UNKNOWN")
VIEWS = {
    "all": {},
    "saved": {"outcome": "in.(added,updated)"},
    "filtered": {"outcome": "eq.filtered"},
    # Outreach jobs.
    "sent": {"outcome": "eq.sent"},
    "problems": {"outcome": "in.(failed,skipped)"},
}
PAGE = 100
MAX_RECIPIENTS = 500
NO_PROXY = (
    "У этого аккаунта нет прокси. Облачная рассылка идёт только через прокси аккаунта — "
    "добавьте его в «Аккаунтах»."
)
NO_MESSAGES = (
    "Нет сообщений для рассылки. Добавьте их в разделе «Парсер и рассылка» — облачная "
    "рассылка берёт сообщения оттуда."
)


def _job_id(params: dict) -> str:
    value = str(params.get("id") or "")
    if not JOB_ID.match(value):
        raise UserError("Задача не найдена.")
    return value


def source_url(value: str) -> str:
    """A source as the scout takes it: a profile link; a bare username or @username too."""
    value = value.strip()
    if not value.startswith(("@", "http://", "https://", "instagram.com", "www.instagram.com")):
        value = "@" + value
    elif value.startswith(("instagram.com", "www.instagram.com")):
        value = "https://" + value
    return profile_url(value.replace("http://", "https://", 1))


def job_params(params: dict) -> dict:
    """The job as the server takes it; the server checks every value again."""
    profile = str(params.get("profile_id") or "")
    if not PROFILE_ID.match(profile):
        raise UserError("Выберите аккаунт Instagram.")
    sources, wrong = [], []
    for line in re.split(r"[\n,]+", str(params.get("sources") or "")):
        if not line.strip():
            continue
        try:
            url = source_url(line)
        except ValueError:
            wrong.append(line.strip())
            continue
        if url not in sources:
            sources.append(url)
    if wrong:
        raise UserError(
            "Это не профили Instagram: " + ", ".join(wrong[:5]) + ("…" if len(wrong) > 5 else "")
        )
    if not sources:
        raise UserError("Добавьте хотя бы один источник.")
    if len(sources) > 500:
        raise UserError("Не больше 500 источников за раз.")
    categories = [item for item in params.get("categories") or [] if item in CATEGORIES]
    if not categories:
        raise UserError("Выберите категории для CRM.")
    try:
        target = int(params.get("target"))
    except (TypeError, ValueError):
        raise UserError("Сколько лидов найти: от 1 до 500.") from None
    if not 1 <= target <= 500:
        raise UserError("Сколько лидов найти: от 1 до 500.")
    return {"profile_id": profile, "sources": sources, "categories": categories, "target": target}


class CloudService:
    def __init__(self, account, local=lambda: None):
        self.account = account
        # The app's own core: the browser profiles and the Scout settings.
        self.local = local

    def call(self, method: str, params: dict):
        actions = {
            "cloud.jobs": self.jobs,
            "cloud.job": self.job,
            "cloud.start": self.start,
            "cloud.outreach_start": self.outreach_start,
            "cloud.outreach_sources": self.outreach_sources,
            "cloud.cancel": self.cancel,
            "cloud.sessions": self.sessions,
            "cloud.telegram": self.telegram,
            "cloud.telegram_link": self.telegram_link,
            "cloud.telegram_unlink": self.telegram_unlink,
        }
        if method not in actions:
            raise UserError("Неизвестное действие.")
        if not self.account.configured:
            raise UserError("Облачный парсер работает через сервер аккаунтов.")
        return actions[method](params)

    @property
    def client(self):
        return self.account.client

    def jobs(self, params: dict | None = None) -> list[dict]:
        return (
            self.client.select(
                "cloud_jobs",
                {"select": JOB_COLUMNS, "order": "created_at.desc", "limit": "30"},
                self.account.token(),
            )
            or []
        )

    def sessions(self, params: dict | None = None) -> list[dict]:
        return self.client.rpc("cloud_sessions_list", {}, self.account.token()) or []

    # ---------- Telegram bot ----------

    def telegram(self, params: dict | None = None) -> dict:
        return self.client.rpc("tg_status", {}, self.account.token()) or {"linked": False}

    def telegram_link(self, params: dict | None = None) -> dict:
        """A one-time code for `/start <code>`; with the bot's name, a link that sends it."""
        started = self.client.rpc("tg_link_start", {}, self.account.token()) or {}
        bot = started.get("bot") or ""
        if not bot:
            raise UserError("Telegram-бот на сервере ещё не запущен.")
        return {**started, "url": f"https://t.me/{bot}?start={started['code']}"}

    def telegram_unlink(self, params: dict | None = None) -> dict:
        self.client.rpc("tg_unlink", {}, self.account.token())
        return self.telegram()

    def job(self, params: dict) -> dict:
        job_id = _job_id(params)
        token = self.account.token()
        rows = self.client.select(
            "cloud_jobs", {"select": JOB_COLUMNS, "id": f"eq.{job_id}"}, token
        )
        if not rows:
            raise UserError("Задача не найдена.")
        view = params.get("view") or "all"
        if view not in VIEWS:
            raise UserError("Неизвестный фильтр.")
        page = max(0, int(params.get("page") or 0))
        query = {
            "select": CANDIDATE_COLUMNS,
            "job_id": f"eq.{job_id}",
            "order": "id.desc",
            "limit": str(PAGE),
            "offset": str(page * PAGE),
            **VIEWS[view],
        }
        items = self.client.select("cloud_candidates", query, token) or []
        return {"job": rows[0], "items": items, "page": page, "page_size": PAGE}

    def _send_session(self, profile: str, token: str, need_proxy: bool = False) -> None:
        """The profile's current Instagram session and proxy go to the server."""
        local = self.local()
        if local is None:
            raise UserError("Приложение ещё не готово. Повторите через секунду.")
        try:
            record = local.call("browser.load_internal", {"id": profile})
        except Exception:
            raise UserError("Аккаунт Instagram не найден.") from None
        cookies = record.get("cookies") or []
        if not any(cookie.get("name") == "sessionid" for cookie in cookies):
            raise UserError(
                "В этом аккаунте нет входа в Instagram. Откройте его в «Аккаунтах», войдите "
                "и закройте окно — сессия сохранится."
            )
        if need_proxy and not record.get("proxy"):
            raise UserError(NO_PROXY)
        self.client.rpc(
            "cloud_session_put",
            {
                "p_profile": profile,
                "p_name": record.get("name") or "",
                "p_record": {"cookies": cookies, "proxy": record.get("proxy")},
            },
            token,
        )

    def _settings(self, prefix: str = "scout_") -> dict:
        local = self.local()
        settings = local.call("settings.get", {}) if local is not None else {}
        return {key: value for key, value in settings.items() if key.startswith(prefix)}

    # ---------- outreach ----------

    def _workspace(self) -> dict:
        """The app's «Рассылка»: its list (with each name's state) and its messages."""
        local = self.local()
        if local is None:
            raise UserError("Приложение ещё не готово. Повторите через секунду.")
        return local.call("outreach.workspace", {}) or {}

    def outreach_sources(self, params: dict | None = None) -> dict:
        """Whom a cloud outreach can write to: the app's list (names not written to yet)
        and the cloud parser's new leads no cloud outreach has taken; and the messages."""
        workspace = self._workspace()
        fresh = [
            item["username"]
            for item in workspace.get("usernames") or []
            if item.get("status") == "new"
        ]
        found = (
            self.client.rpc("cloud_unwritten", {"p_limit": MAX_RECIPIENTS}, self.account.token())
            or []
        )
        return {
            "list": len(fresh),
            "found": len(found),
            "messages": len(workspace.get("messages") or []),
        }

    def outreach_start(self, params: dict) -> dict:
        request_id = str(params.get("request_id") or uuid.uuid4().hex)
        if not REQUEST_ID.match(request_id):
            raise UserError("Некорректный запрос.")
        profile = str(params.get("profile_id") or "")
        if not PROFILE_ID.match(profile):
            raise UserError("Выберите аккаунт Instagram.")
        try:
            limit = int(params.get("limit") or MAX_RECIPIENTS)
        except (TypeError, ValueError):
            limit = 0
        if not 1 <= limit <= MAX_RECIPIENTS:
            raise UserError(f"Скольким написать: от 1 до {MAX_RECIPIENTS}.")
        workspace = self._workspace()
        messages = list(workspace.get("messages") or [])
        if not messages:
            raise UserError(NO_MESSAGES)
        token = self.account.token()
        if params.get("source") == "found":
            rows = self.client.rpc("cloud_unwritten", {"p_limit": limit}, token) or []
            usernames = [row["username"] for row in rows]
            if not usernames:
                raise UserError("Новых лидов облачного парсера, которым ещё не писали, нет.")
        elif params.get("source") == "list":
            usernames = [
                item["username"]
                for item in workspace.get("usernames") or []
                if item.get("status") == "new"
            ][:limit]
            if not usernames:
                raise UserError("В «Рассылке по списку» нет аккаунтов, которым ещё не писали.")
        else:
            raise UserError("Выберите, кому писать.")
        self._send_session(profile, token, need_proxy=True)
        job_id = self.client.rpc(
            "cloud_start",
            {
                "p_request_id": request_id,
                "p_params": {
                    "kind": "outreach",
                    "profile_id": profile,
                    "usernames": usernames,
                    "messages": messages,
                    "settings": self._settings("outreach_"),
                },
            },
            token,
        )
        return self.job({"id": str(job_id)})

    def start(self, params: dict) -> dict:
        request_id = str(params.get("request_id") or uuid.uuid4().hex)
        if not REQUEST_ID.match(request_id):
            raise UserError("Некорректный запрос.")
        job = job_params(params)
        token = self.account.token()
        self._send_session(job["profile_id"], token)
        job_id = self.client.rpc(
            "cloud_start",
            {"p_request_id": request_id, "p_params": {**job, "settings": self._settings()}},
            token,
        )
        return self.job({"id": str(job_id)})

    def cancel(self, params: dict) -> dict:
        job_id = _job_id(params)
        self.client.rpc("cloud_cancel", {"p_job": job_id}, self.account.token())
        return self.job({"id": job_id})
