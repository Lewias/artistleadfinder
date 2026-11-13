"""The assistant's tools over the rest of the app.

Reading (the artist base, statistics, search history, iMessage campaigns, accounts)
runs at once. Actions (adding to a mailing list, a new CRM contact) are only proposed:
each waits in the interface until the user presses «Выполнить», and runs with the app's
own checks then. Nothing here touches keys or proxies. Running Instagram (Direct,
«Рассылка», the parser, settings) is in `operator_tools`, proposed the same way.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from ..errors import UserError
from ..models import AssistantAction, utcnow

MAX_RESULT = 30_000
LEAD_STATUSES = ["", "new", "reviewed", "qualified", "rejected", "contacted"]


class ToolError(Exception):
    """A tool could not do what was asked; the text goes back to the model."""


def _schema(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def compact(value: Any, depth: int = 0) -> Any:
    """Data for the model: no empty fields, short strings and lists, shallow nesting."""
    if isinstance(value, dict):
        if depth > 3:
            return "…"
        return {
            key: compact(item, depth + 1)
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        items = [compact(item, depth + 1) for item in value[:25]]
        return items + [f"… и ещё {len(value) - 25}"] if len(value) > 25 else items
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…"
    return value


APP_TOOLS = [
    {
        "name": "leads_search",
        "description": "База артистов (все найденные профили Instagram): поиск по username, "
        "имени, био; status: '', new, reviewed, qualified, rejected, contacted; "
        "сортировка: score, followers, activity, created.",
        "strict": True,
        "input_schema": _schema(
            {
                "query": {"type": "string"},
                "status": {"type": "string", "enum": LEAD_STATUSES},
                "min_followers": {"type": "integer"},
                "sort": {"type": "string", "enum": ["score", "followers", "activity", "created"]},
                "limit": {"type": "integer"},
            }
        ),
    },
    {
        "name": "app_stats",
        "description": "Статистика приложения: сколько артистов, новых за сегодня, "
        "квалифицированных, активных поисков, жанры.",
        "strict": True,
        "input_schema": _schema({}),
    },
    {
        "name": "search_history",
        "description": "История запусков парсера на компьютере: когда, сколько источников "
        "пройдено, сколько лидов найдено, почему пропускались.",
        "strict": True,
        "input_schema": _schema({"limit": {"type": "integer"}}),
    },
    {
        "name": "imessage_campaigns",
        "description": "Рассылки iMessage через iPhone: статус и сколько отправлено.",
        "strict": True,
        "input_schema": _schema({}),
    },
    {
        "name": "accounts",
        "description": "Аккаунты Instagram в приложении: имя, есть ли вход и прокси.",
        "strict": True,
        "input_schema": _schema({}),
    },
    {
        "name": "propose_instagram_list_add",
        "description": "Предложить добавить usernames Instagram в список «Рассылки» приложения "
        "(ничего не отправляет). Выполнится после «Выполнить».",
        "strict": True,
        "input_schema": _schema(
            {
                "usernames": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            }
        ),
    },
    {
        "name": "propose_imessage_list_add",
        "description": "Предложить добавить телефоны (с + и кодом страны) или email в список "
        "получателей кампании iMessage (ничего не отправляет). Выполнится после «Выполнить».",
        "strict": True,
        "input_schema": _schema(
            {
                "handles": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            }
        ),
    },
    {
        "name": "propose_crm_contact",
        "description": "Предложить новый контакт в CRM (instagram или imessage) с каналами: "
        "kind instagram (username), phone (с + и кодом страны), email. Создастся после "
        "«Выполнить».",
        "strict": True,
        "input_schema": _schema(
            {
                "crm": {"type": "string", "enum": ["instagram", "imessage"]},
                "name": {"type": "string"},
                "channels": {
                    "type": "array",
                    "items": _schema(
                        {
                            "kind": {"type": "string", "enum": ["instagram", "phone", "email"]},
                            "value": {"type": "string"},
                        }
                    ),
                },
                "notes": {"type": "string"},
            }
        ),
    },
]
APP_LABELS = {
    "leads_search": "Смотрю базу артистов",
    "app_stats": "Смотрю статистику",
    "search_history": "Смотрю историю поисков",
    "imessage_campaigns": "Смотрю рассылки iMessage",
    "accounts": "Смотрю аккаунты",
    "propose_instagram_list_add": "Предлагаю пополнить список рассылки",
    "propose_imessage_list_add": "Предлагаю пополнить список iMessage",
    "propose_crm_contact": "Предлагаю новый контакт",
}


class AppTools:
    """Mixed into AssistantService: `core` reaches the app's own methods."""

    core: Any
    add_usernames: Any

    # ---------- reading ----------

    def tool_leads_search(self, params: dict) -> dict:
        query = {
            "search": str(params.get("query") or "")[:240],
            "status": params.get("status") if params.get("status") in LEAD_STATUSES else "",
            "min_followers": max(0, int(params.get("min_followers") or 0)),
            "sort": params.get("sort") or "score",
            "page_size": max(1, min(int(params.get("limit") or 20), 50)),
        }
        found = self.core("leads.list", query)
        return compact({"total": found.get("total"), "leads": found.get("items") or []})

    def tool_app_stats(self, params: dict) -> dict:
        stats = self.core("dashboard.get", {})
        return compact({key: value for key, value in stats.items() if key not in ("leads",)})

    def tool_search_history(self, params: dict) -> Any:
        return compact(
            self.core("scout.runs", {"limit": max(1, min(int(params.get("limit") or 10), 30))})
        )

    def tool_imessage_campaigns(self, params: dict) -> Any:
        return compact(self.core("imessage.campaigns", {})[:10])

    def _profiles(self) -> list[dict]:
        return [
            {
                "id": profile["id"],
                "name": profile["name"],
                "logged_in": bool(profile.get("cookie_count")),
                "proxy": bool(profile.get("proxy")),
            }
            for profile in self.core("browser.list", {})
        ]

    def tool_accounts(self, params: dict) -> Any:
        return [
            {key: value for key, value in item.items() if key != "id"} for item in self._profiles()
        ]

    # ---------- proposals ----------

    def _propose(self, kind: str, params: dict, summary: str) -> dict:
        with self.sessions.begin() as session:
            action = AssistantAction(kind=kind, params=params, summary=summary[:500])
            session.add(action)
            session.flush()
            action_id = action.id
        return {
            "action_id": action_id,
            "status": "ждёт, пока пользователь нажмёт «Выполнить»",
            "summary": summary,
        }

    def tool_propose_instagram_list_add(self, params: dict) -> dict:
        from ..outreach.workspace import normalize_username

        names = [name for name in map(normalize_username, params.get("usernames") or []) if name]
        names = list(dict.fromkeys(names))[:500]
        if not names:
            raise ToolError("Нет ни одного корректного username.")
        reason = str(params.get("reason") or "").strip()
        return self._propose(
            "instagram_list_add",
            {"usernames": names},
            f"В список «Рассылки»: {len(names)} — "
            + ", ".join("@" + name for name in names[:5])
            + (" …" if len(names) > 5 else "")
            + (f" ({reason})" if reason else ""),
        )

    def tool_propose_imessage_list_add(self, params: dict) -> dict:
        from ..imessage.phones import normalize_recipient

        handles = [
            value for value in map(normalize_recipient, params.get("handles") or []) if value
        ]
        handles = list(dict.fromkeys(handles))[:500]
        if not handles:
            raise ToolError("Нет ни одного телефона с + и кодом страны или email.")
        reason = str(params.get("reason") or "").strip()
        return self._propose(
            "imessage_list_add",
            {"handles": handles},
            f"В получатели iMessage: {len(handles)} — "
            + ", ".join(handles[:5])
            + (" …" if len(handles) > 5 else "")
            + (f" ({reason})" if reason else ""),
        )

    def tool_propose_crm_contact(self, params: dict) -> dict:
        from ..crm.service import channels_from

        crm = params.get("crm") if params.get("crm") in ("instagram", "imessage") else "imessage"
        name = " ".join(str(params.get("name") or "").split())[:160]
        try:
            channels = channels_from(params.get("channels") or [])
        except UserError as error:
            raise ToolError(str(error)) from None
        if not name and not channels:
            raise ToolError("Нужно имя или хотя бы один канал.")
        return self._propose(
            "crm_contact",
            {
                "crm": crm,
                "name": name,
                "channels": channels,
                "notes": str(params.get("notes") or "")[:1000],
            },
            f"Новый контакт в CRM {crm}: {name or channels[0]['value']}"
            + (" — " + ", ".join(item["value"] for item in channels[:3]) if channels else ""),
        )

    # ---------- running a proposal ----------

    def _execute(self, action: AssistantAction) -> str:
        params = action.params
        done = self._execute_operator(action.kind, params)
        if done is not None:
            return done
        if action.kind == "instagram_list_add":
            added = self.add_usernames(params["usernames"])
            return f"Добавлено в список «Рассылки»: {added} (остальные уже были)."
        if action.kind == "imessage_list_add":
            added = self.imessage.add_recipients(params["handles"])
            return f"Добавлено в получатели iMessage: {added} (остальные уже были)."
        if action.kind == "crm_contact":
            contact = self.crm.save(params)
            return f"Контакт «{contact['name']}» создан в CRM."
        raise UserError("Неизвестное действие.")

    def action_run(self, params: dict) -> dict:
        with self.sessions() as session:
            action = session.get(AssistantAction, int(params.get("id") or 0))
        if action is None or action.status != "pending":
            raise UserError("Это действие уже выполнено или отклонено.")
        try:
            result, status = self._execute(action), "done"
        except (UserError, ToolError) as error:
            result, status = str(error), "failed"
        with self.sessions.begin() as session:
            row = session.get(AssistantAction, action.id)
            row.status, row.result, row.done_at = status, result[:500], utcnow()
        return self.state({})

    def action_reject(self, params: dict) -> dict:
        with self.sessions.begin() as session:
            action = session.get(AssistantAction, int(params.get("id") or 0))
            if action is None or action.status != "pending":
                raise UserError("Это действие уже выполнено или отклонено.")
            action.status, action.done_at = "rejected", utcnow()
        return self.state({})

    def actions_view(self, session) -> list[dict]:
        """Waiting proposals and the last ones that ran, newest last."""
        rows = list(
            session.scalars(select(AssistantAction).order_by(AssistantAction.id.desc()).limit(20))
        )
        return [
            {
                "id": row.id,
                "kind": row.kind,
                "summary": row.summary,
                "status": row.status,
                "result": row.result,
                "done_at": row.done_at.isoformat() if isinstance(row.done_at, datetime) else None,
            }
            for row in reversed(rows)
            if row.status == "pending" or row.status in ("done", "failed")
        ][-10:]
