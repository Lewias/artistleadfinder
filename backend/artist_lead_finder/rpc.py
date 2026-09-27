"""Bounded JSON Lines RPC. Logs never share stdout with responses."""

import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .chromium_runtime import BrowserLaunchError
from .database import application_data_dir, open_database
from .service import ApplicationService


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
                "candidates": getattr(record, "candidates", None),
                "analyzed": getattr(record, "analyzed", None),
                "qualified": getattr(record, "qualified", None),
            }
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
    engine, sessions = open_database()
    service = ApplicationService(sessions, data_dir)
    try:
        while True:
            line = sys.stdin.buffer.readline(2_000_001)
            if not line:
                break
            request_id = None
            try:
                if len(line) > 2_000_000 or not line.endswith(b"\n"):
                    raise ValueError("Запрос слишком большой.")
                request = Request.model_validate_json(line)
                request_id = request.id
                if request.method == "system.shutdown":
                    service.shutdown()
                    response = {"id": request_id, "result": {"ok": True}}
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    break
                result = service.call(request.method, request.params)
                response = {"id": request_id, "result": result}
            except ValidationError:
                response = {"id": request_id, "error": "Проверьте формат и диапазоны полей."}
            except SQLAlchemyError:
                logging.error("database_error")
                response = {"id": request_id, "error": "База данных недоступна. См. журнал."}
            except BrowserLaunchError as error:
                response = {"id": request_id, "error": str(error)}
            except (ValueError, KeyError, OSError):
                logging.warning("invalid_request")
                response = {
                    "id": request_id,
                    "error": "Не удалось выполнить действие. Проверьте параметры и доступ к файлу.",
                }
            except Exception:
                logging.error("application_error")
                response = {"id": request_id, "error": "Ошибка приложения. Подробности в журнале."}
            sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
            sys.stdout.flush()
    finally:
        service.shutdown()
        engine.dispose()
        logging.info("application_shutdown")
