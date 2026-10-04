"""Templates and chains of messages for the signed "Verse iMessage" Shortcut.

The Shortcut takes the whole list from `GET /task` and, for every entry, sends its text,
then every file of the list, then waits 20-50 s. It cannot be changed (only a Mac can
sign a new one), so a chain is laid out within that:
- a message without text cannot go first: its files join the text before it;
- several entries for one number are several messages, 20-50 s apart;
- files go after every entry of a launch, so a message with files is a launch of its
  own; the next launch is a new run of the Shortcut, by the same QR code.
"""

from ..errors import UserError

FILE_ID_LENGTH = 32
MAX_PARTS = 10
MAX_PART_ATTACHMENTS = 10
MAX_TEXT = 2000
MAX_NAME = 120
MAX_FOLDER = 60


def parts_from(value: object, *, require_content: bool) -> list[dict]:
    """[{text, attachment_ids}]; a draft chain may keep empty messages, a template not."""
    if not isinstance(value, list):
        raise UserError("Некорректный список сообщений.")
    if len(value) > MAX_PARTS:
        raise UserError(f"Не больше {MAX_PARTS} сообщений в цепочке.")
    result = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise UserError("Некорректное сообщение.")
        text = str(item.get("text") or "").strip()
        if len(text) > MAX_TEXT:
            raise UserError(f"Сообщение №{index} длиннее {MAX_TEXT} символов.")
        files = item.get("attachment_ids") or []
        if not isinstance(files, list) or not all(
            isinstance(file_id, str) and len(file_id) == FILE_ID_LENGTH for file_id in files
        ):
            raise UserError("Некорректное вложение.")
        files = list(dict.fromkeys(files))
        if len(files) > MAX_PART_ATTACHMENTS:
            raise UserError(f"В сообщении №{index} больше {MAX_PART_ATTACHMENTS} файлов.")
        if require_content and not text and not files:
            raise UserError(f"Сообщение №{index} пустое: нужен текст или файл.")
        result.append({"text": text, "attachment_ids": files})
    if require_content and not result:
        raise UserError("Добавьте хотя бы одно сообщение.")
    return result


def plan(parts: list[dict]) -> list[dict]:
    """Messages as the Shortcut sends them: [{text, attachment_ids, run, parts}].

    `parts` are the 1-based numbers of the source messages folded into each step.
    Raises ValueError for what the Shortcut cannot do."""
    steps: list[dict] = []
    for number, part in enumerate(parts, start=1):
        if part["text"]:
            steps.append(
                {
                    "text": part["text"],
                    "attachment_ids": list(part["attachment_ids"]),
                    "parts": [number],
                }
            )
        elif part["attachment_ids"]:
            if not steps:
                raise UserError(
                    "«Verse iMessage» начинает каждое сообщение с текста: в первом сообщении "
                    "нужен текст. Файлы без текста перед ними команда отправить не может."
                )
            steps[-1]["attachment_ids"] += part["attachment_ids"]
            steps[-1]["parts"].append(number)
        else:
            raise UserError(f"Сообщение №{number} пустое: добавьте текст или файл.")
    if not steps:
        raise UserError("Добавьте хотя бы одно сообщение.")
    run, in_run = 0, 0
    for step in steps:
        step["attachment_ids"] = list(dict.fromkeys(step["attachment_ids"]))
        if step["attachment_ids"] and in_run:
            run, in_run = run + 1, 0
        step["run"] = run
        in_run += 1
        if step["attachment_ids"]:
            run, in_run = run + 1, 0
    return steps


def summary(parts: list[dict]) -> dict:
    """What the template or chain means for the Shortcut, for the UI."""
    try:
        steps = plan(parts)
    except ValueError as error:
        return {"messages": 0, "launches": 0, "error": str(error)}
    return {"messages": len(steps), "launches": steps[-1]["run"] + 1, "error": None}
