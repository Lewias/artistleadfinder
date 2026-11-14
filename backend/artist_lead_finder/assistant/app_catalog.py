"""The whole app for the assistant: a catalog of the app's own methods, by area.

The dedicated tools cover the everyday work; this catalog reaches everything else the
interface can do — Email campaigns, mailboxes and warm-up, Spotify, iMessage campaigns and
templates, the artist base, every CRM, the parser, «Ответы» in Direct, settings, health — and
the application's log.

Reading methods run at once. Every method that changes, starts or deletes something is
only proposed: it waits for «Выполнить» and then runs through the very same handler the
interface uses, with its checks. Left out on purpose: secrets (API keys, mailbox passwords,
tokens), files on disk (import/export paths), user and key administration, the iPhone bridge
network and the shell's internal methods.
"""

import json
from collections import deque
from pathlib import Path
from typing import Any

from ..errors import UserError
from .app_tools import MAX_RESULT, ToolError, _schema, compact

# method -> (area, "read" | "action", what it does and its params)
CATALOG: dict[str, tuple[str, str, str]] = {
    # ---------- artist base ----------
    "leads.list": (
        "base",
        "read",
        "Артисты базы. search, status (new/reviewed/qualified/rejected/contacted), genre, "
        "source, min_followers, max_followers, minimum_score, activity_days, sort "
        "(score/followers/activity/created), descending, page, page_size ≤ 100.",
    ),
    "leads.detail": ("base", "read", "Карточка артиста с анализом и историей. id."),
    "leads.status": (
        "base",
        "action",
        "Статус артиста. id, status: new/reviewed/qualified/rejected/contacted.",
    ),
    "leads.do_not_contact": ("base", "action", "Не писать артисту. id, value: true/false."),
    "leads.delete": ("base", "action", "Удалить артистов из базы навсегда. ids: [int]."),
    "dashboard.get": ("base", "read", "Сводка: артисты, новые, квалифицированные, жанры."),
    # ---------- CRM ----------
    "crm.list": (
        "crm",
        "read",
        "Контакты CRM со счётчиками и суммами «Принёс/Потенциал». crm: instagram/imessage/"
        "email, search, tab: all/attention/trash, filters: {statuses: [..]}, sort, "
        "descending, page, page_size ≤ 200.",
    ),
    "crm.sources": ("crm", "read", "Откуда пришли контакты CRM. crm."),
    "crm.save": (
        "crm",
        "action",
        "Создать или изменить контакт. crm, id (для изменения), name, channels: "
        "[{kind: instagram/phone/email, value}], statuses: [..], notes, next_action, "
        "next_action_at (ISO), earned, potential.",
    ),
    "crm.statuses_save": (
        "crm",
        "action",
        "Список статусов CRM целиком (удалённый статус снимется с контактов). crm, "
        "statuses: [{label, color: violet/blue/green/amber/red/pink/slate, emoji}].",
    ),
    "crm.label": ("crm", "action", "Метка контактам. crm, ids: [int], label, add: true/false."),
    "crm.write": ("crm", "action", "Добавить контакты CRM в список рассылки канала. crm, ids."),
    "crm.trash": ("crm", "action", "Переместить контакты в корзину. crm, ids."),
    "crm.restore": ("crm", "action", "Вернуть контакты из корзины. crm, ids."),
    "crm.purge": ("crm", "action", "Удалить из корзины навсегда. crm, ids (без ids — всю)."),
    # ---------- Instagram «Рассылка» ----------
    "outreach.workspace": ("instagram", "read", "Список «Рассылки», сообщения, отправители."),
    "outreach.workspace_update": (
        "instagram",
        "action",
        "Изменить «Рассылку»: usernames: [..] (весь список), messages: [тексты-варианты], "
        "sender_ids: [id аккаунтов].",
    ),
    "outreach.workspace_add_leads": (
        "instagram",
        "action",
        "Добавить артистов базы в «Рассылку». lead_ids: [int] или statuses: [..].",
    ),
    "outreach.campaigns": ("instagram", "read", "Кампании рассылки Instagram."),
    "outreach.campaign": ("instagram", "read", "Одна кампания. id."),
    "outreach.recipients": (
        "instagram",
        "read",
        "Получатели кампании. id (из outreach.campaigns), status, page, page_size ≤ 200.",
    ),
    "outreach.events": ("instagram", "read", "Журнал отправок Direct. job_id, limit."),
    "outreach.senders": ("instagram", "read", "Аккаунты-отправители и их лимиты."),
    "outreach.templates": ("instagram", "read", "Шаблоны сообщений Direct."),
    "outreach.sequences": ("instagram", "read", "Цепочки фоллоу-апов Direct."),
    "outreach.template_save": (
        "instagram",
        "action",
        "Шаблон Direct. id (для изменения), name, body, enabled.",
    ),
    "outreach.sequence_save": (
        "instagram",
        "action",
        "Цепочка фоллоу-апов. id, name, steps: [..], enabled.",
    ),
    "outreach.mark_replied": (
        "instagram",
        "action",
        "Отметить, что артист ответил. lead_id, body — его ответ.",
    ),
    "outreach.stop_conversation": ("instagram", "action", "Больше не писать артисту. lead_id."),
    "outreach.resolve_review": (
        "instagram",
        "action",
        "Разобрать отправку «на проверке». recipient_id, sent: true/false.",
    ),
    "outreach.reset_memory": (
        "instagram",
        "action",
        "Забыть, кому уже писали в Direct (необратимо).",
    ),
    # ---------- «Ответы» Direct ----------
    "inbox.state": ("instagram", "read", "Чтение ответов Direct: идёт ли и что найдено."),
    "inbox.start": (
        "instagram",
        "action",
        "Прочитать ответы Direct. sender: id аккаунта, days: за сколько дней.",
    ),
    "inbox.stop": ("instagram", "action", "Остановить чтение ответов Direct."),
    "inbox.add_to_crm": ("instagram", "action", "Найденные телефоны/почты в CRM. ids: [int]."),
    "inbox.hide": ("instagram", "action", "Скрыть находки из ответов. ids: [int]."),
    # ---------- parser ----------
    "scout.accounts": ("parser", "read", "Парсер по аккаунтам: цели, найдено, запуски."),
    "scout.source_list": ("parser", "read", "Источники парсера с результатами."),
    "scout.runs": ("parser", "read", "История запусков. limit."),
    "scout.decisions": ("parser", "read", "Почему профили взяты/пропущены. job_id, limit."),
    "scout.events": ("parser", "read", "События парсера. job_id, limit."),
    "scout.results": ("parser", "read", "Последние результаты парсера."),
    "scout.account_target": (
        "parser",
        "action",
        "Цель парсера на аккаунте. profile_id, target, reset: true — сбросить счётчик.",
    ),
    "scout.source_add": ("parser", "action", "Добавить источники. values: [ссылки или @ники]."),
    "scout.source_update": ("parser", "action", "Включить/выключить источник. url, enabled."),
    "scout.source_remove": ("parser", "action", "Удалить источник. url."),
    "scout.ignore": ("parser", "action", "Никогда не брать профиль. username."),
    "scout.reset_memory": (
        "parser",
        "action",
        "Забыть просмотренные профили и публикации (необратимо).",
    ),
    "autopilot.state": ("parser", "read", "«Найти и написать»: состояние."),
    "autopilot.cancel": ("parser", "action", "Остановить «Найти и написать»."),
    "jobs.list": ("parser", "read", "Запуски поиска."),
    "jobs.detail": ("parser", "read", "Один запуск. id."),
    "jobs.control": ("parser", "action", "Запуск поиска: id, action: pause/resume/cancel."),
    "jobs.clear_history": ("parser", "action", "Очистить историю поисков."),
    # ---------- Spotify ----------
    "spotify.state": ("spotify", "read", "Поиск в Spotify: настройки, идёт ли, счётчики."),
    "spotify.results": (
        "spotify",
        "read",
        "Найденные в Spotify артисты. view: new (ещё не в рассылке)/found (все "
        "подходящие)/skipped (отсеянные), limit ≤ 500, offset.",
    ),
    "spotify.settings": (
        "spotify",
        "action",
        "Настройки поиска Spotify: min_listeners, max_listeners, release_days, target, "
        "queries: [фразы], seeds: [ссылки] и другие поля из spotify.state.",
    ),
    "spotify.start": ("spotify", "action", "Начать поиск в Spotify. target."),
    "spotify.stop": ("spotify", "action", "Остановить поиск в Spotify."),
    "spotify.to_outreach": (
        "spotify",
        "action",
        "Instagram найденных — в «Рассылку». ids: [int] или all_new: true.",
    ),
    "spotify.to_crm": ("spotify", "action", "Найденных — в CRM Instagram. ids или all_new."),
    # ---------- iMessage ----------
    "imessage.state": ("imessage", "read", "Кампания iMessage: получатели, тексты, телефон."),
    "imessage.campaigns": ("imessage", "read", "Кампании iMessage."),
    "imessage.events": ("imessage", "read", "Журнал iMessage. campaign_id, limit."),
    "imessage.templates": ("imessage", "read", "Шаблоны iMessage по папкам."),
    "imessage.preview": ("imessage", "read", "Как уйдут сообщения кампании iMessage."),
    "imessage.workspace_update": (
        "imessage",
        "action",
        "Изменить кампанию iMessage: recipients: [{phone, message}], messages: [варианты], "
        "delay_seconds.",
    ),
    "imessage.add_leads": (
        "imessage",
        "action",
        "Артистов базы с телефоном — в получатели. lead_ids: [int] или all: true.",
    ),
    "imessage.start": (
        "imessage",
        "action",
        "Запустить рассылку iMessage через iPhone. test_phone — только тест на этот номер.",
    ),
    "imessage.control": ("imessage", "action", "Рассылка iMessage: action: pause/resume/stop."),
    "imessage.resolve": (
        "imessage",
        "action",
        "Разобрать отправку «на проверке». job_id, resolution: sent/failed.",
    ),
    "imessage.template_save": (
        "imessage",
        "action",
        "Шаблон iMessage. id (для изменения), name, folder, parts: [{text}].",
    ),
    "imessage.template_delete": ("imessage", "action", "Удалить шаблон iMessage. id."),
    "imessage.template_use": ("imessage", "action", "Вставить шаблон в кампанию iMessage. id."),
    "imessage.reset_memory": (
        "imessage",
        "action",
        "Забыть кампании и отправки iMessage (необратимо).",
    ),
    # ---------- Email ----------
    "email.state": (
        "email",
        "read",
        "Email-рассылка: ящики, получатели, темы, тексты, окно отправки, счётчики.",
    ),
    "email.campaigns": ("email", "read", "Кампании Email."),
    "email.thread": ("email", "read", "Переписка с адресом. address."),
    "email.preview": ("email", "read", "Как уйдёт письмо. index, days."),
    "email.warmup_state": ("email", "read", "Прогрев ящиков: цели, партнёры, доставка."),
    "email.workspace_update": (
        "email",
        "action",
        "Изменить Email-рассылку: recipients: [{email, name}], subjects: [..], bodies: [..], "
        "followups: [..], signature, interval_min_seconds, interval_max_seconds, "
        "window_start_hour, window_end_hour, window_zone, skip_previously_emailed.",
    ),
    "email.add_text": ("email", "action", "Добавить адреса из текста. text."),
    "email.add_leads": ("email", "action", "Артистов базы с email — в получатели. lead_ids/all."),
    "email.start": ("email", "action", "Запустить Email-кампанию."),
    "email.control": ("email", "action", "Email-кампания: campaign_id, action: pause/resume/stop."),
    "email.check_inbox": ("email", "action", "Проверить ответы во входящих сейчас."),
    "email.reply": ("email", "action", "Ответить в ветку письма. address, body."),
    "email.suppress": ("email", "action", "Адреса в стоп-лист. text."),
    "email.account_control": (
        "email",
        "action",
        "Ящик Gmail: id, action: pause/resume/test (проверить вход).",
    ),
    "email.account_remove": ("email", "action", "Удалить ящик Gmail. id."),
    "email.warmup_update": (
        "email",
        "action",
        "Прогрев ящика: id, warm_target, pool_warmup.",
    ),
    "email.warmup_partner_update": (
        "email",
        "action",
        "Партнёр прогрева: id, address, enabled.",
    ),
    "email.warmup_detect": ("email", "action", "Найти партнёров прогрева в Nstbrowser."),
    # ---------- settings and health ----------
    "settings.get": ("settings", "read", "Все настройки приложения."),
    "settings.save": (
        "settings",
        "action",
        "Сохранить настройки: только меняемые ключи из settings.get.",
    ),
    "system.info": ("settings", "read", "Версия, папки данных и журналов, схема базы."),
    "ai.status": ("settings", "read", "AI-классификация парсера: включена ли, модель."),
}
AREAS = sorted({area for area, _, _ in CATALOG.values()})
LOG_LEVELS = ("", "INFO", "WARNING", "ERROR")

CATALOG_TOOLS = [
    {
        "name": "app_functions",
        "description": "Каталог всех функций приложения по разделам (base — база артистов, "
        "crm, instagram — «Рассылка» и ответы Direct, parser, spotify, imessage, email, "
        "settings): метод, чтение или действие и параметры. Пустой area — все разделы.",
        "strict": True,
        "input_schema": _schema({"area": {"type": "string", "enum": ["", *AREAS]}}),
    },
    {
        "name": "app_read",
        "description": "Прочитать данные приложения методом-чтением из app_functions. "
        "params_json — JSON-объект параметров ({} — без параметров).",
        "strict": True,
        "input_schema": _schema({"method": {"type": "string"}, "params_json": {"type": "string"}}),
    },
    {
        "name": "propose_app_action",
        "description": "Предложить действие приложения (метод-действие из app_functions): "
        "запуск и остановка рассылок и парсера, изменения CRM, базы, шаблонов, настроек. "
        "Выполнится после «Выполнить» с проверками приложения. summary — что и зачем, "
        "одной фразой по-русски.",
        "strict": True,
        "input_schema": _schema(
            {
                "method": {"type": "string"},
                "params_json": {"type": "string"},
                "summary": {"type": "string"},
            }
        ),
    },
    {
        "name": "app_logs",
        "description": "Журнал приложения (последние записи, новые в конце): события и "
        "ошибки ядра без текстов сообщений. level — от какого уровня (пусто — все), "
        "contains — подстрока события, limit ≤ 200.",
        "strict": True,
        "input_schema": _schema(
            {
                "level": {"type": "string", "enum": list(LOG_LEVELS)},
                "contains": {"type": "string"},
                "limit": {"type": "integer"},
            }
        ),
    },
]
CATALOG_LABELS = {
    "app_functions": "Смотрю функции приложения",
    "app_read": "Читаю данные приложения",
    "propose_app_action": "Предлагаю действие",
    "app_logs": "Читаю журнал",
}
SEVERITY = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def _params(raw: object) -> dict:
    text = str(raw or "").strip() or "{}"
    try:
        value = json.loads(text)
    except ValueError:
        raise ToolError("params_json: нужен JSON-объект.") from None
    if not isinstance(value, dict):
        raise ToolError("params_json: нужен JSON-объект.")
    return value


def _fit(value: Any) -> Any:
    """The answer as the model gets it: compacted, then cut to the result budget."""
    value = compact(value)
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT:
        return value
    return {"truncated": True, "text": text[:MAX_RESULT]}


def _entry(method: object, kind: str) -> str:
    method = str(method or "").strip()
    entry = CATALOG.get(method)
    if entry is None:
        raise ToolError("Нет такого метода. Посмотрите app_functions.")
    if entry[1] != kind:
        raise ToolError(
            "Это действие: предложите его через propose_app_action."
            if kind == "read"
            else "Это чтение: вызовите app_read."
        )
    return method


class CatalogTools:
    """Mixed into AssistantService."""

    core: Any

    def tool_app_functions(self, params: dict) -> dict:
        area = str(params.get("area") or "")
        return {
            name: f"{kind}: {about}"
            for name, (where, kind, about) in CATALOG.items()
            if not area or where == area
        }

    def tool_app_read(self, params: dict) -> Any:
        method = _entry(params.get("method"), "read")
        return _fit(self.core(method, _params(params.get("params_json"))))

    def tool_propose_app_action(self, params: dict) -> dict:
        method = _entry(params.get("method"), "action")
        arguments = _params(params.get("params_json"))
        if method == "settings.save":
            current = self.core("settings.get", {})
            if not arguments or set(arguments) - set(current):
                raise ToolError("Ключи должны быть из settings.get.")
        summary = " ".join(str(params.get("summary") or "").split())[:300]
        if not summary:
            raise ToolError("summary: что и зачем, одной фразой.")
        shown = json.dumps(arguments, ensure_ascii=False)
        about = CATALOG[method][2].split(".")[0]
        return self._propose(
            "app_call",
            {"method": method, "params": arguments},
            f"{summary} — {about}" + (f" {shown[:160]}" if arguments else ""),
        )

    def _execute_catalog(self, kind: str, params: dict) -> str | None:
        if kind != "app_call":
            return None
        method, arguments = params["method"], params["params"]
        if method not in CATALOG or CATALOG[method][1] != "action":
            raise UserError("Это действие больше недоступно.")
        if method == "settings.save":
            arguments = {**self.core("settings.get", {}), **arguments}
        result = self.core(method, arguments)
        brief = ""
        if isinstance(result, dict):
            numbers = {
                key: value
                for key, value in result.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            brief = ", ".join(f"{key}: {value}" for key, value in list(numbers.items())[:4])
        return "Сделано." + (f" {brief}." if brief else "")

    def tool_app_logs(self, params: dict) -> dict:
        folder = Path(self.core("system.info", {})["log_dir"])
        floor = SEVERITY.get(str(params.get("level") or "").upper(), 0)
        needle = str(params.get("contains") or "").strip().lower()
        limit = max(1, min(int(params.get("limit") or 50), 200))
        files = sorted(folder.glob("application.jsonl*"), key=lambda path: path.name, reverse=True)
        # Rotated files end in .1, .2 …: the oldest first, the live file last.
        files = [path for path in files if path.name != "application.jsonl"] + [
            path for path in files if path.name == "application.jsonl"
        ]
        found: deque = deque(maxlen=limit)
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if SEVERITY.get(str(record.get("level")), 0) < floor:
                    continue
                if needle and needle not in line.lower():
                    continue
                found.append({key: value for key, value in record.items() if value is not None})
        if not found:
            return {"records": [], "note": "Подходящих записей нет."}
        return {"records": list(found), "log_dir": str(folder)}
