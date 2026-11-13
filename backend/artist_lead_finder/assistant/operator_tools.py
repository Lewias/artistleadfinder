"""The assistant as the operator of the app: «Рассылка», the parser, settings.

Reading runs at once: phones and emails found in Direct replies, the state of «Рассылка»
and of the parser, the sources, the logs, the settings, a look at the page open in an
account's window. Everything that changes or sends something
(starting or stopping «Рассылка» and the parser, senders, limits,
sources, deleting an account, «Автоведение») is only proposed and runs with the app's own
checks after the user presses «Выполнить».

The parser is driven by the shell, not by the core: a start is put in a small queue the
shell takes (`assistant.shell_next_internal`), opens the window and starts the usual run.
"""

import json
import logging
from collections import deque
from typing import Any

from ..errors import UserError
from .app_tools import ToolError, _schema, compact

log = logging.getLogger(__name__)

RUN_OPEN = ("running", "paused")
STRINGS = {"type": "array", "items": {"type": "string"}}

# The page as a person would describe it: where it is, what can be pressed, is it a login.
LOOK = """() => ({url:location.href,title:document.title,
links:[...document.querySelectorAll('a[href]')]
  .map(a=>({text:(a.innerText||a.getAttribute('aria-label')||'').trim().slice(0,80),
    href:a.getAttribute('href')})).filter(a=>a.text).slice(0,50),
buttons:[...document.querySelectorAll('button,[role=button]')]
  .map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim()).filter(Boolean).slice(0,60),
composer:document.querySelectorAll('[role=textbox],textarea').length,
login:!!document.querySelector('input[name=username]'),
dialogs:[...document.querySelectorAll('[role=dialog]')].map(d=>(d.innerText||'').slice(0,500))})"""

OPERATOR_TOOLS = [
    {
        "name": "direct_findings",
        "description": "Телефоны и почты, найденные в ответах Direct, которые ещё не разобраны.",
        "strict": True,
        "input_schema": _schema({}),
    },
    {
        "name": "outreach_status",
        "description": "«Рассылка» Instagram: идёт ли, сколько в списке и в каких статусах, "
        "аккаунты-отправители (состояние, отправлено за 24 ч, лимит), последние кампании.",
        "strict": True,
        "input_schema": _schema({}),
    },
    {
        "name": "parser_status",
        "description": "Парсер по аккаунтам: цель, найдено, текущий запуск и его стадия; "
        "«Найти и написать», «Автоведение» и ответы оболочки на запуски от ассистента.",
        "strict": True,
        "input_schema": _schema({}),
    },
    {
        "name": "parser_sources",
        "description": "Источники парсера: сколько всего и включено, по каждому — статус и "
        "сколько лидов он дал.",
        "strict": True,
        "input_schema": _schema({}),
    },
    {
        "name": "parser_log",
        "description": "Журнал парсера: decisions — почему профили взяты или пропущены, "
        "events — события запусков.",
        "strict": True,
        "input_schema": _schema(
            {
                "kind": {"type": "string", "enum": ["decisions", "events"]},
                "limit": {"type": "integer"},
            }
        ),
    },
    {
        "name": "app_settings",
        "description": "Настройки приложения: лимиты и паузы рассылки, парсера и чтения Direct.",
        "strict": True,
        "input_schema": _schema({}),
    },
    {
        "name": "window_look",
        "description": "Что открыто в окне аккаунта: адрес, кнопки, ссылки, диалоги, есть ли "
        "форма входа. username не пустой — сначала открыть этот профиль Instagram в окне. "
        "Окно должно быть открыто.",
        "strict": True,
        "input_schema": _schema({"account": {"type": "string"}, "username": {"type": "string"}}),
    },
    {
        "name": "propose_outreach",
        "description": "Предложить запустить «Рассылку» по списку (start) или остановить её "
        "(stop: неотправленные снимаются, список остаётся).",
        "strict": True,
        "input_schema": _schema({"action": {"type": "string", "enum": ["start", "stop"]}}),
    },
    {
        "name": "propose_outreach_senders",
        "description": "Предложить набор аккаунтов-отправителей «Рассылки» (имена аккаунтов).",
        "strict": True,
        "input_schema": _schema({"accounts": STRINGS}),
    },
    {
        "name": "propose_campaign_control",
        "description": "Предложить поставить кампанию рассылки на паузу, продолжить или "
        "отменить (id из outreach_status).",
        "strict": True,
        "input_schema": _schema(
            {
                "campaign_id": {"type": "integer"},
                "action": {"type": "string", "enum": ["pause", "resume", "cancel"]},
            }
        ),
    },
    {
        "name": "propose_sender_status",
        "description": "Предложить состояние аккаунта для рассылки: active (вернуть в работу), "
        "paused, disabled.",
        "strict": True,
        "input_schema": _schema(
            {
                "account": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "paused", "disabled"]},
            }
        ),
    },
    {
        "name": "propose_settings",
        "description": "Предложить изменить настройки: changes_json — JSON-объект "
        "{ключ: значение} с ключами из app_settings.",
        "strict": True,
        "input_schema": _schema({"changes_json": {"type": "string"}}),
    },
    {
        "name": "propose_parser_sources",
        "description": "Предложить добавить источники парсера (add: ссылки или @ники "
        "Instagram), включить (enable) или выключить (disable) существующие.",
        "strict": True,
        "input_schema": _schema(
            {"action": {"type": "string", "enum": ["add", "enable", "disable"]}, "urls": STRINGS}
        ),
    },
    {
        "name": "propose_parser_start",
        "description": "Предложить запустить парсер на аккаунте: найти target новых лидов. "
        "Окно аккаунта откроется само.",
        "strict": True,
        "input_schema": _schema({"account": {"type": "string"}, "target": {"type": "integer"}}),
    },
    {
        "name": "propose_parser_control",
        "description": "Предложить поставить запуск парсера на аккаунте на паузу, продолжить "
        "или отменить.",
        "strict": True,
        "input_schema": _schema(
            {
                "account": {"type": "string"},
                "action": {"type": "string", "enum": ["pause", "resume", "cancel"]},
            }
        ),
    },
    {
        "name": "propose_account_delete",
        "description": "Предложить удалить аккаунт Instagram из приложения вместе с его входом "
        "(окно закроется). Необратимо.",
        "strict": True,
        "input_schema": _schema({"account": {"type": "string"}}),
    },
    {
        "name": "propose_duty",
        "description": "Предложить включить или выключить «Автоведение»: раз в 5 минут "
        "приложение само держит лимиты рассылки, запускает «Рассылку», когда в списке есть "
        "новые, и парсер на аккаунте parser, пока новых в списке меньше queue_target. "
        "senders — аккаунты-отправители.",
        "strict": True,
        "input_schema": _schema(
            {
                "enabled": {"type": "boolean"},
                "parser": {"type": "string"},
                "senders": STRINGS,
                "daily_limit_per_sender": {"type": "integer"},
                "send_interval_seconds": {"type": "integer"},
                "queue_target": {"type": "integer"},
            }
        ),
    },
]
OPERATOR_LABELS = {
    "direct_findings": "Смотрю находки из Direct",
    "outreach_status": "Смотрю рассылку",
    "parser_status": "Смотрю парсер",
    "parser_sources": "Смотрю источники",
    "parser_log": "Смотрю журнал парсера",
    "app_settings": "Смотрю настройки",
    "window_look": "Смотрю окно аккаунта",
    "propose_outreach": "Предлагаю действие с рассылкой",
    "propose_outreach_senders": "Предлагаю отправителей",
    "propose_campaign_control": "Предлагаю действие с кампанией",
    "propose_sender_status": "Предлагаю состояние аккаунта",
    "propose_settings": "Предлагаю настройки",
    "propose_parser_sources": "Предлагаю источники",
    "propose_parser_start": "Предлагаю запустить парсер",
    "propose_parser_control": "Предлагаю действие с парсером",
    "propose_account_delete": "Предлагаю удалить аккаунт",
    "propose_duty": "Предлагаю «Автоведение»",
}
VERBS = {"pause": "пауза", "resume": "продолжить", "cancel": "отменить"}
SENDER_STATES = {"active": "в работе", "paused": "на паузе", "disabled": "выключен"}


def source_url(value: object) -> str:
    """A profile link, an @name or a bare name as the parser's source address."""
    from ..browser_capture import profile_url

    raw = str(value or "").strip()
    if raw.startswith(("instagram.com/", "www.instagram.com/")):
        raw = "https://" + raw
    elif "/" not in raw:
        raw = "@" + raw.lstrip("@")
    try:
        return profile_url(raw)
    except ValueError:
        raise ToolError(f"Не похоже на профиль Instagram: {str(value)[:80]}") from None


def run_of(account: dict) -> dict:
    """The account's parser run that is still open; an interrupted one is not."""
    run = account.get("run") or {}
    return run if run.get("status") in RUN_OPEN and run.get("stage") != "interrupted" else {}


class OperatorTools:
    """Mixed into AssistantService next to AppTools (`core`, `_profiles`, `_propose`)."""

    core: Any
    duty: Any

    def _init_operator(self) -> None:
        # Parser starts waiting for the shell, and what the shell answered to the last ones.
        self.shell_requests: deque[dict] = deque()
        self.shell_notes: deque[dict] = deque(maxlen=10)

    def _account(self, value: object) -> dict:
        wanted = " ".join(str(value or "").split()).lower()
        profiles = self._profiles()
        for profile in profiles:
            if wanted and wanted in (profile["id"], profile["name"].lower()):
                return profile
        names = ", ".join(profile["name"] for profile in profiles) or "аккаунтов нет"
        raise ToolError(f"Аккаунт не найден. Есть: {names}.")

    # ---------- the shell's queue ----------

    def request_scout(self, profile_id: str, target: int | None = None) -> None:
        """A parser run on the account, started by the shell at its next round."""
        accounts = {item["profile"]["id"]: item for item in self.core("scout.accounts", {})}
        if profile_id not in accounts:
            raise UserError("Аккаунт не найден.")
        if run_of(accounts[profile_id]):
            raise UserError("На этом аккаунте уже идёт парсинг.")
        if any(item["profile_id"] == profile_id for item in self.shell_requests):
            raise UserError("Запуск парсера на этом аккаунте уже ждёт очереди.")
        if target:
            self.core(
                "scout.account_target", {"profile_id": profile_id, "target": target, "reset": True}
            )
        self.shell_requests.append({"kind": "scout", "profile_id": profile_id})

    def shell_next(self, params: dict) -> dict | None:
        try:
            self.duty.tick()
        except Exception as error:  # noqa: BLE001 - the queue is served anyway
            log.warning("duty_failed", extra={"error_type": type(error).__name__})
        return self.shell_requests.popleft() if self.shell_requests else None

    def shell_result(self, params: dict) -> dict:
        error = str(params.get("error") or "")[:300]
        self.shell_notes.append(
            {
                "account": self._names().get(str(params.get("profile_id")), ""),
                "result": error or "парсер запущен",
                "job_id": params.get("job_id"),
            }
        )
        self.duty.started(params.get("job_id"), error)
        return {"ok": True}

    def _names(self) -> dict[str, str]:
        return {profile["id"]: profile["name"] for profile in self._profiles()}

    # ---------- reading ----------

    def tool_direct_findings(self, params: dict) -> Any:
        state = self.core("inbox.state", {})
        return compact({"findings": state.get("findings") or [], "scan": state.get("scan")})

    def tool_outreach_status(self, params: dict) -> dict:
        work = self.core("outreach.workspace", {})
        names = self._names()
        statuses: dict[str, int] = {}
        for item in work.get("usernames") or []:
            statuses[item["status"]] = statuses.get(item["status"], 0) + 1
        settings = self.core("settings.get", {})
        return compact(
            {
                "running": work.get("running"),
                "list": {"total": len(work.get("usernames") or []), "by_status": statuses},
                "messages": len(work.get("messages") or []),
                "selected_senders": [names.get(key, key) for key in work.get("sender_ids") or []],
                "campaign": work.get("campaign"),
                "senders": [
                    {key: value for key, value in sender.items() if key != "id"}
                    for sender in self.core("outreach.senders", {})
                ],
                "limits": {
                    key: settings.get(key)
                    for key in ("outreach_daily_limit_per_sender", "outreach_send_interval_seconds")
                },
                "recent_campaigns": self.core("outreach.campaigns", {})[:5],
            }
        )

    def tool_parser_status(self, params: dict) -> dict:
        accounts = []
        for item in self.core("scout.accounts", {}):
            run = item.get("run") or {}
            accounts.append(
                {
                    "account": item["profile"]["name"],
                    "target": item.get("target"),
                    "found": item.get("found"),
                    "run": {
                        key: run.get(key)
                        for key in ("id", "status", "stage", "kind", "url", "wait_seconds")
                    }
                    if run
                    else None,
                }
            )
        return compact(
            {
                "accounts": accounts,
                "find_and_write": self.core("autopilot.state", {}),
                "duty": self.duty.view(self._names()),
                "waiting_for_shell": len(self.shell_requests),
                "shell_answers": list(self.shell_notes),
            }
        )

    def tool_parser_sources(self, params: dict) -> dict:
        rows = self.core("scout.source_list", {})
        best = sorted(rows, key=lambda row: row.get("leads_found") or 0, reverse=True)[:40]
        return {
            "total": len(rows),
            "enabled": sum(1 for row in rows if row.get("enabled")),
            "sources": [compact(row) for row in best],
        }

    def tool_parser_log(self, params: dict) -> Any:
        limit = max(1, min(int(params.get("limit") or 25), 25))
        method = "scout.events" if params.get("kind") == "events" else "scout.decisions"
        return compact(self.core(method, {"limit": limit}))

    def tool_app_settings(self, params: dict) -> Any:
        return self.core("settings.get", {})

    def tool_window_look(self, params: dict) -> Any:
        from ..direct_message import USERNAME

        profile = self._account(params.get("account"))
        if not self.core("browser.runtime.is_open", {"id": profile["id"]})["open"]:
            raise ToolError("Окно этого аккаунта закрыто.")
        username = str(params.get("username") or "").strip().lstrip("@")
        if username:
            if not USERNAME.fullmatch(username):
                raise ToolError("Некорректный Instagram username.")
            self.core(
                "browser.runtime.navigate",
                {"id": profile["id"], "url": f"https://www.instagram.com/{username}/"},
            )
        return compact(self.core("browser.runtime.eval", {"id": profile["id"], "script": LOOK}))

    # ---------- proposals ----------

    def tool_propose_outreach(self, params: dict) -> dict:
        start = params.get("action") == "start"
        if start:
            work = self.core("outreach.workspace", {})
            fresh = sum(1 for item in work.get("usernames") or [] if item["status"] == "new")
            summary = f"Запустить «Рассылку»: новых в списке {fresh}"
        else:
            summary = "Остановить «Рассылку» (неотправленные снимаются)"
        return self._propose("outreach", {"action": "start" if start else "stop"}, summary)

    def tool_propose_outreach_senders(self, params: dict) -> dict:
        profiles = [self._account(name) for name in params.get("accounts") or []]
        if not profiles:
            raise ToolError("Нужен хотя бы один аккаунт.")
        return self._propose(
            "outreach_senders",
            {"sender_ids": list(dict.fromkeys(profile["id"] for profile in profiles))},
            "Отправители «Рассылки»: " + ", ".join(profile["name"] for profile in profiles),
        )

    def tool_propose_campaign_control(self, params: dict) -> dict:
        action = params.get("action")
        if action not in VERBS:
            raise ToolError("action: pause, resume или cancel.")
        campaign = int(params.get("campaign_id") or 0)
        return self._propose(
            "campaign_control",
            {"id": campaign, "action": action},
            f"Кампания рассылки №{campaign}: {VERBS[action]}",
        )

    def tool_propose_sender_status(self, params: dict) -> dict:
        status = params.get("status")
        if status not in SENDER_STATES:
            raise ToolError("status: active, paused или disabled.")
        profile = self._account(params.get("account"))
        return self._propose(
            "sender_status",
            {"id": profile["id"], "status": status},
            f"Аккаунт «{profile['name']}» для рассылки: {SENDER_STATES[status]}",
        )

    def tool_propose_settings(self, params: dict) -> dict:
        try:
            changes = json.loads(str(params.get("changes_json") or ""))
        except ValueError:
            raise ToolError("changes_json: нужен JSON-объект.") from None
        current = self.core("settings.get", {})
        if not isinstance(changes, dict) or not changes or set(changes) - set(current):
            raise ToolError("Ключи должны быть из app_settings.")
        return self._propose(
            "settings",
            {"changes": changes},
            "Настройки: "
            + ", ".join(f"{key}: {current[key]} → {value}" for key, value in changes.items()),
        )

    def tool_propose_parser_sources(self, params: dict) -> dict:
        action = params.get("action")
        if action not in ("add", "enable", "disable"):
            raise ToolError("action: add, enable или disable.")
        urls = list(dict.fromkeys(map(source_url, params.get("urls") or [])))[:500]
        if not urls:
            raise ToolError("Нужен хотя бы один источник.")
        verb = {"add": "Добавить", "enable": "Включить", "disable": "Выключить"}[action]
        return self._propose(
            "parser_sources",
            {"action": action, "urls": urls},
            f"{verb} источники парсера: {len(urls)} — "
            + ", ".join(urls[:5])
            + (" …" if len(urls) > 5 else ""),
        )

    def tool_propose_parser_start(self, params: dict) -> dict:
        profile = self._account(params.get("account"))
        target = int(params.get("target") or 0)
        if not 1 <= target <= 10000:
            raise ToolError("target: от 1 до 10000 лидов.")
        return self._propose(
            "parser_start",
            {"profile_id": profile["id"], "target": target},
            f"Запустить парсер на «{profile['name']}»: найти {target} новых лидов",
        )

    def tool_propose_parser_control(self, params: dict) -> dict:
        action = params.get("action")
        if action not in VERBS:
            raise ToolError("action: pause, resume или cancel.")
        profile = self._account(params.get("account"))
        return self._propose(
            "parser_control",
            {"profile_id": profile["id"], "action": action},
            f"Парсер на «{profile['name']}»: {VERBS[action]}",
        )

    def tool_propose_account_delete(self, params: dict) -> dict:
        profile = self._account(params.get("account"))
        return self._propose(
            "account_delete",
            {"profile_id": profile["id"]},
            f"Удалить аккаунт «{profile['name']}» из приложения вместе с его входом",
        )

    def tool_propose_duty(self, params: dict) -> dict:
        if not params.get("enabled"):
            return self._propose("duty", {"enabled": False}, "Выключить «Автоведение»")
        parser = self._account(params.get("parser"))
        senders = [self._account(name) for name in params.get("senders") or []]
        try:
            config = self.duty.config(
                {
                    "enabled": True,
                    "parser_id": parser["id"],
                    "sender_ids": list(dict.fromkeys(sender["id"] for sender in senders)),
                    "daily_limit_per_sender": params.get("daily_limit_per_sender"),
                    "send_interval_seconds": params.get("send_interval_seconds"),
                    "queue_target": params.get("queue_target"),
                }
            )
        except UserError as error:
            raise ToolError(str(error)) from None
        return self._propose(
            "duty",
            config,
            f"Включить «Автоведение»: парсер на «{parser['name']}», пока новых в списке меньше "
            f"{config['queue_target']}; пишут "
            + ", ".join(sender["name"] for sender in senders)
            + f" — до {config['daily_limit_per_sender']} в сутки с аккаунта, пауза "
            f"{config['send_interval_seconds']} с",
        )

    # ---------- running a proposal ----------

    def _execute_operator(self, kind: str, params: dict) -> str | None:
        """What the action did, or None when the kind is not the operator's."""
        if kind == "outreach":
            if params["action"] == "start":
                campaign = self.core("outreach.workspace_start", {}).get("campaign") or {}
                return f"«Рассылка» запущена (кампания №{campaign.get('id')})."
            self.core("outreach.workspace_stop", {})
            return "«Рассылка» остановлена."
        if kind == "outreach_senders":
            self.core("outreach.workspace_update", {"sender_ids": params["sender_ids"]})
            return "Отправители «Рассылки» изменены."
        if kind == "campaign_control":
            self.core("outreach.campaign_control", params)
            return f"Кампания №{params['id']}: {VERBS[params['action']]} — сделано."
        if kind == "sender_status":
            self.core("outreach.sender_status", params)
            return f"Аккаунт для рассылки: {SENDER_STATES[params['status']]}."
        if kind == "settings":
            self.core("settings.save", {**self.core("settings.get", {}), **params["changes"]})
            return "Настройки сохранены."
        if kind == "parser_sources":
            if params["action"] == "add":
                self.core("scout.source_add", {"values": params["urls"]})
                return f"Источники добавлены: {len(params['urls'])}."
            enabled = params["action"] == "enable"
            for url in params["urls"]:
                self.core("scout.source_update", {"url": url, "enabled": enabled})
            return f"Источники {'включены' if enabled else 'выключены'}: {len(params['urls'])}."
        if kind == "parser_start":
            self.request_scout(params["profile_id"], params["target"])
            return "Запуск парсера передан приложению: окно откроется, парсер начнёт за минуту."
        if kind == "parser_control":
            accounts = {item["profile"]["id"]: item for item in self.core("scout.accounts", {})}
            run = (accounts.get(params["profile_id"]) or {}).get("run") or {}
            if run.get("status") not in RUN_OPEN:
                raise UserError("На этом аккаунте нет незавершённого запуска парсера.")
            if params["action"] == "resume" and run.get("stage") == "interrupted":
                raise UserError("Запуск прерван перезапуском приложения: запустите парсер заново.")
            self.core("jobs.control", {"id": run["id"], "action": params["action"]})
            return f"Парсер: {VERBS[params['action']]} — сделано."
        if kind == "account_delete":
            profile = params["profile_id"]
            if self.core("browser.runtime.is_open", {"id": profile})["open"]:
                self.core("browser.runtime.close", {"id": profile})
            self.core("browser.delete", {"id": profile})
            return "Аккаунт удалён."
        if kind == "duty":
            self.duty.save(params)
            return "«Автоведение» включено." if params["enabled"] else "«Автоведение» выключено."
        return None
