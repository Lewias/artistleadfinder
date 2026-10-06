"""Bounded JSON Lines RPC. Logs never share stdout with responses."""

import json
import logging
import secrets
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from .account import project
from .account.client import SupabaseClient
from .account.runtime import Runtime
from .account.session import AccountService
from .browser_sessions import BrowserSessions
from .chromium_runtime import BrowserLaunchError, ChromiumRuntime
from .database import NewerDatabaseError, application_data_dir, open_database
from .errors import UserError
from .service import ApplicationService, system_info


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    method: str = Field(max_length=80)
    params: dict = Field(default_factory=dict)


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        # Only allow event names and bounded identifiers, never raw exception bodies.
        return json.dumps(
            {
                "time": self.formatTime(record),
                "level": record.levelname,
                "event": record.msg if isinstance(record.msg, str) else "event",
                "job_id": getattr(record, "job_id", None),
                "provider": getattr(record, "provider", None),
                "error_type": getattr(record, "error_type", None),
                "method": getattr(record, "method", None),
                "where": getattr(record, "where", None),
                "candidates": getattr(record, "candidates", None),
                "analyzed": getattr(record, "analyzed", None),
                "qualified": getattr(record, "qualified", None),
                # Outreach audit: ids and structured reasons only, never message text.
                "campaign_id": getattr(record, "campaign_id", None),
                "recipient_id": getattr(record, "recipient_id", None),
                "reason": getattr(record, "reason", None),
                # Code shown to the user next to a generic error, to find this line.
                "ref": getattr(record, "ref", None),
                # What went wrong in the app's own words: user-facing error texts, run
                # notices, stop reasons. Never cookies, tokens, message texts or numbers.
                "detail": _bounded(getattr(record, "detail", None)),
            }
        )


def _bounded(value):
    return None if value is None else str(value)[:300]


def failure_context(method: str | None, error: BaseException) -> dict:
    """Method, exception class and innermost raise site; never the exception text."""
    frames = traceback.extract_tb(error.__traceback__)
    where = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else None
    return {"method": method, "error_type": type(error).__name__, "where": where}


def describe_error(method: str | None, error: BaseException) -> str:
    """Text for the UI. A UserError is shown as written; anything else may carry paths or
    parser output, so it becomes a plain explanation with a code that is also logged."""
    if isinstance(error, (UserError, BrowserLaunchError)):
        logging.warning(
            "user_error",
            extra={**failure_context(method, error), "detail": str(error)},
        )
        return str(error)
    if isinstance(error, ValidationError):
        details = error.errors()
        for item in details:
            cause = (item.get("ctx") or {}).get("error")
            if isinstance(cause, UserError):
                logging.warning(
                    "user_error",
                    extra={"method": method, "error_type": "UserError", "detail": str(cause)},
                )
                return str(cause)
        field = ".".join(str(part) for part in details[0]["loc"]) if details else ""
        suffix = f" (поле {field})" if field else ""
        logging.warning(
            "invalid_request",
            extra={"method": method, "error_type": "ValidationError", "detail": field or None},
        )
        return f"Проверьте формат и диапазоны полей{suffix}."
    ref = secrets.token_hex(3)
    context = {**failure_context(method, error), "ref": ref}
    if isinstance(error, OperationalError) and "locked" in str(error.orig).lower():
        logging.warning("database_busy", extra=context)
        return f"База данных занята другой операцией. Повторите через пару секунд (код {ref})."
    if isinstance(error, SQLAlchemyError):
        logging.error("database_error", extra=context)
        return f"Ошибка базы данных (код {ref}). Подробности в журнале."
    if isinstance(error, PermissionError):
        logging.warning("invalid_request", extra=context)
        return (
            f"Нет доступа к файлу: он открыт в другой программе или защищён от записи (код {ref})."
        )
    if isinstance(error, FileNotFoundError):
        logging.warning("invalid_request", extra=context)
        return f"Файл или папка не найдены (код {ref})."
    if isinstance(error, OSError):
        logging.warning("invalid_request", extra=context)
        return f"Ошибка доступа к диску или сети (код {ref}). Подробности в журнале."
    if isinstance(error, (ValueError, KeyError, TypeError)):
        logging.warning("invalid_request", extra=context)
        return f"Некорректные данные запроса (код {ref}). Подробности в журнале."
    logging.error("application_error", extra=context)
    return f"Внутренняя ошибка приложения (код {ref}). Подробности в журнале."


def serve_failure(message: str) -> None:
    """The core could not start: it still answers, so the window shows why instead of
    "connection lost" after the process exits."""
    for line in iter(lambda: sys.stdin.buffer.readline(2_000_001), b""):
        try:
            request = json.loads(line)
            request_id, method = request.get("id"), request.get("method")
        except (ValueError, AttributeError):
            request_id, method = None, None
        if method == "system.shutdown":
            sys.stdout.write(json.dumps({"id": request_id, "result": {"ok": True}}) + "\n")
            sys.stdout.flush()
            break
        sys.stdout.write(json.dumps({"id": request_id, "error": message}, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def open_service(folder: Path, log_dir: Path):
    """The local app of one account (or of the whole install without accounts)."""
    engine, sessions = open_database(folder / "artist-leads.sqlite3")
    try:
        service = ApplicationService(sessions, folder, log_dir)
        service.chromium.prepare()
    except Exception:
        engine.dispose()
        raise

    def close() -> None:
        try:
            service.shutdown()
        finally:
            engine.dispose()

    return service, close


def build_runtime(data_dir: Path, log_dir: Path) -> Runtime:
    url, key = project.SUPABASE_URL, project.SUPABASE_ANON_KEY
    client = SupabaseClient(url, key) if url and key else None
    account = AccountService(data_dir, client)
    return Runtime(
        data_dir,
        account,
        lambda folder: open_service(folder, log_dir),
        lambda: system_info(data_dir, log_dir),
        lambda: ChromiumRuntime(BrowserSessions(data_dir / "probe")),
    )


def run() -> None:
    data_dir = application_data_dir()
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "application.jsonl", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.info("application_startup")
    runtime = build_runtime(data_dir, log_dir)
    try:
        runtime.start()
    except Exception as error:
        if isinstance(error, NewerDatabaseError):
            logging.error("startup_failed", extra=failure_context("startup", error))
            message = str(error)
        else:
            message = describe_error("startup", error).replace(
                "Внутренняя ошибка приложения", "Ядро не смогло запуститься"
            )
        serve_failure(message)
        return
    try:
        while True:
            line = sys.stdin.buffer.readline(2_000_001)
            if not line:
                break
            request_id = None
            method = None
            try:
                if len(line) > 2_000_000 or not line.endswith(b"\n"):
                    raise UserError("Запрос слишком большой.")
                request = Request.model_validate_json(line)
                request_id = request.id
                method = request.method
                if request.method == "system.shutdown":
                    runtime.shutdown()
                    response = {"id": request_id, "result": {"ok": True}}
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    break
                result = runtime.call(request.method, request.params)
                response = {"id": request_id, "result": result}
            except Exception as error:
                response = {"id": request_id, "error": describe_error(method, error)}
            sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
            sys.stdout.flush()
    finally:
        runtime.shutdown()
        logging.info("application_shutdown")
