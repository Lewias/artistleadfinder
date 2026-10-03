"""The improved Shortcut (protocol v2): an unsigned workflow plist and the build guide.

The plist is a source artifact in the format of the decoded original
(`Verse_iMessage_decoded.json`). iOS imports only signed `.shortcut` files, so it is
not installable as is; the guide builds the same Shortcut by hand.
Both come from one list of steps, so they cannot drift apart.
"""

import plistlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# The signed original Verse Shortcut (legacy protocol). iOS imports it as is.
SIGNED = Path(__file__).with_name("Verse iMessage.shortcut")

NAMESPACE = uuid.UUID("5f0c9a8e-3b0f-4a51-9d1e-7c2f6a1b8e40")
MESSAGES_APP = {
    "BundleIdentifier": "com.apple.MobileSMS",
    "Name": "Messages",
    "TeamIdentifier": "0000000000",
}
OBJECT = "￼"
# WFCondition codes of the classic "If" action.
IS = 4


def _uuid(name: str) -> str:
    """Stable UUIDs: the artifact is the same on every export."""
    return str(uuid.uuid5(NAMESPACE, name)).upper()


def _output(name: str, label: str) -> dict:
    return {"OutputUUID": _uuid(name), "Type": "ActionOutput", "OutputName": label}


def _variable(name: str) -> dict:
    return {"VariableName": name, "Type": "Variable"}


def _attachment(value: dict) -> dict:
    return {"Value": value, "WFSerializationType": "WFTextTokenAttachment"}


def _token_string(value: dict) -> dict:
    """One variable as the whole text field, as the Shortcuts editor stores it."""
    return {
        "Value": {"string": OBJECT, "attachmentsByRange": {"{0, 1}": value}},
        "WFSerializationType": "WFTextTokenString",
    }


def _as_file(value: dict) -> dict:
    """The variable typed as a file (Variable → Тип → Файл), so Messages gets the file."""
    return {
        **value,
        "Aggrandizements": [
            {
                "Type": "WFCoercionVariableAggrandizement",
                "CoercionItemClass": "WFGenericFileContentItem",
            }
        ],
    }


@dataclass
class Step:
    identifier: str
    parameters: dict
    # The guide: what to add in the Shortcuts editor and how to set it.
    text: str
    # Number of the matching action in the decoded original (1-based), if there is one.
    original: int | None = None
    depth: int = 0
    notes: list[str] = field(default_factory=list)


def _get(key: str, source: dict, name: str, depth: int, original: int | None = None) -> Step:
    return Step(
        "is.workflow.actions.getvalueforkey",
        {
            "WFInput": _attachment(source),
            "WFDictionaryKey": key,
            "CustomOutputName": key,
            "UUID": _uuid(name),
        },
        f"«Получить значение для ключа» `{key}` из `job` → переименуйте вывод в `{key}`.",
        original,
        depth,
    )


def _download(url: dict, name: str, text: str, depth: int, original: int | None = None) -> Step:
    return Step(
        "is.workflow.actions.downloadurl",
        {"WFURL": _token_string(url), "UUID": _uuid(name)},
        text,
        original,
        depth,
    )


def steps() -> list[Step]:
    job = _variable("job")
    state = _output("state", "state")
    recipient = _output("recipient", "recipient")
    return [
        Step(
            "is.workflow.actions.setvariable",
            {"WFVariableName": "nextUrl", "WFInput": _attachment({"Type": "ExtensionInput"})},
            "«Задать переменную» `nextUrl` = **Входные данные быстрой команды** "
            "(адрес задания из ссылки или QR-кода).",
            original=1,
        ),
        Step(
            "is.workflow.actions.repeat.count",
            {"WFRepeatCount": 1000, "GroupingIdentifier": _uuid("loop"), "WFControlFlowMode": 0},
            "«Повторить» **1000** раз. Внутри — шаги 3–30; Shortcut выходит из цикла сам, "
            "когда сервер отвечает `stopped` или `finished`.",
            notes=[
                "В исходном цикл шёл по всем контактам сразу (действие 11); "
                "здесь — по одному заданию."
            ],
        ),
        _download(
            _variable("nextUrl"),
            "next",
            "«Получить содержимое URL» из `nextUrl` (метод GET).",
            1,
            1,
        ),
        Step(
            "is.workflow.actions.detect.dictionary",
            {
                "WFInput": _attachment(_output("next", "Contents of URL")),
                "UUID": _uuid("dictionary"),
            },
            "«Получить словарь из» содержимого URL.",
            original=2,
            depth=1,
        ),
        Step(
            "is.workflow.actions.setvariable",
            {"WFVariableName": "job", "WFInput": _attachment(_output("dictionary", "Dictionary"))},
            "«Задать переменную» `job` = Словарь.",
            depth=1,
        ),
        _get("state", job, "state", 1),
        Step(
            "is.workflow.actions.conditional",
            {
                "WFInput": {"Type": "Variable", "Variable": _attachment(state)},
                "WFCondition": IS,
                "WFConditionalActionString": "ready",
                "GroupingIdentifier": _uuid("if-ready"),
                "WFControlFlowMode": 0,
            },
            "«Если» `state` **является** `ready`.",
            depth=1,
        ),
        _get("recipient", job, "recipient", 2, 12),
        _get("message", job, "message", 2, 13),
        Step(
            "is.workflow.actions.sendmessage",
            {
                "IntentAppDefinition": MESSAGES_APP,
                "WFSendMessageActionRecipients": _attachment(recipient),
                "WFSendMessageContent": _token_string(_output("message", "message")),
                "ShowWhenRun": True,
                "UUID": _uuid("send-text"),
            },
            "«Отправить сообщение»: Сообщение = `message`, Получатели = `recipient` "
            "(переменная, не контакт из списка). **Показывать при выполнении — включено.**",
            original=14,
            depth=2,
        ),
        _get("textAckUrl", job, "textAckUrl", 2),
        _download(
            _output("textAckUrl", "textAckUrl"),
            "text-ack",
            "«Получить содержимое URL» из `textAckUrl` — сервер отмечает, что шаг текста пройден.",
            2,
            16,
        ),
        _get("attachments", job, "attachments", 2, 5),
        Step(
            "is.workflow.actions.repeat.each",
            {
                "WFInput": _attachment(_output("attachments", "attachments")),
                "GroupingIdentifier": _uuid("files"),
                "WFControlFlowMode": 0,
            },
            "«Повторить с каждым» объектом в `attachments`.",
            original=6,
            depth=2,
        ),
        Step(
            "is.workflow.actions.getvalueforkey",
            {
                "WFInput": _attachment(_variable("Repeat Item 2")),
                "WFDictionaryKey": "downloadUrl",
                "UUID": _uuid("download-url"),
            },
            "«Получить значение для ключа» `downloadUrl` из **Repeat Item 2** (элемент этого, "
            "вложенного, цикла — не внешнего).",
            original=7,
            depth=3,
        ),
        _download(
            _output("download-url", "Dictionary Value"),
            "file",
            "«Получить содержимое URL» из значения словаря — это файл с именем и типом от сервера.",
            3,
            8,
        ),
        Step(
            "is.workflow.actions.sendmessage",
            {
                "IntentAppDefinition": MESSAGES_APP,
                "WFSendMessageActionRecipients": _attachment(recipient),
                "WFSendMessageContent": _token_string(_as_file(_output("file", "Contents of URL"))),
                "ShowWhenRun": True,
                "UUID": _uuid("send-file"),
            },
            "«Отправить сообщение»: в поле Сообщение — **только** переменная «Содержимое URL» "
            "из шага 16, без пробелов и текста; нажмите на неё → Тип → **Файл**. "
            "Получатели = `recipient`. Показывать при выполнении — включено.",
            original=18,
            depth=3,
            notes=[
                "В исходном файлы сначала собирались в переменную `files` (действие 9), а затем "
                "отправлялись из неё; здесь каждый файл уходит сразу после загрузки.",
                "Если вставить переменную рядом с текстом, Команды превратят файл в его "
                "текстовое описание — поэтому поле должно содержать только её.",
            ],
        ),
        Step(
            "is.workflow.actions.repeat.each",
            {"GroupingIdentifier": _uuid("files"), "WFControlFlowMode": 2},
            "Конец «Повторить с каждым».",
            original=10,
            depth=2,
        ),
        _get("ackUrl", job, "ackUrl", 2, 15),
        _download(
            _output("ackUrl", "ackUrl"),
            "ack",
            "«Получить содержимое URL» из `ackUrl` — подтверждение **после** текста и всех "
            "вложений (в исходном оно шло до вложений).",
            2,
            16,
        ),
        _get("delaySeconds", job, "delaySeconds", 2),
        Step(
            "is.workflow.actions.delay",
            {"WFDelayTime": _attachment(_output("delaySeconds", "delaySeconds"))},
            "«Ждать» `delaySeconds` секунд (задаётся в приложении; после последнего — 0).",
            original=21,
            depth=2,
            notes=[
                "В исходном пауза была случайной, 20–50 с (действия 20–21); "
                "здесь — фиксированная, из приложения."
            ],
        ),
        Step(
            "is.workflow.actions.conditional",
            {"GroupingIdentifier": _uuid("if-ready"), "WFControlFlowMode": 1},
            "«Иначе».",
            depth=1,
        ),
        Step(
            "is.workflow.actions.conditional",
            {
                "WFInput": {"Type": "Variable", "Variable": _attachment(state)},
                "WFCondition": IS,
                "WFConditionalActionString": "paused",
                "GroupingIdentifier": _uuid("if-paused"),
                "WFControlFlowMode": 0,
            },
            "Вложенное «Если» `state` **является** `paused`.",
            depth=2,
        ),
        _get("retrySeconds", job, "retrySeconds", 3),
        Step(
            "is.workflow.actions.delay",
            {"WFDelayTime": _attachment(_output("retrySeconds", "retrySeconds"))},
            "«Ждать» `retrySeconds` (15 с) — затем цикл снова спросит сервер.",
            depth=3,
        ),
        Step(
            "is.workflow.actions.conditional",
            {"GroupingIdentifier": _uuid("if-paused"), "WFControlFlowMode": 1},
            "«Иначе» (`stopped`, `finished` или ошибка сервера).",
            depth=2,
        ),
        Step(
            "is.workflow.actions.exit",
            {},
            "«Остановить эту быструю команду».",
            depth=3,
        ),
        Step(
            "is.workflow.actions.conditional",
            {"GroupingIdentifier": _uuid("if-paused"), "WFControlFlowMode": 2},
            "Конец вложенного «Если».",
            depth=2,
        ),
        Step(
            "is.workflow.actions.conditional",
            {"GroupingIdentifier": _uuid("if-ready"), "WFControlFlowMode": 2},
            "Конец «Если» `ready`.",
            depth=1,
        ),
        Step(
            "is.workflow.actions.repeat.count",
            {"GroupingIdentifier": _uuid("loop"), "WFControlFlowMode": 2},
            "Конец «Повторить».",
        ),
    ]


def workflow() -> dict:
    return {
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowClientVersion": "4711",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": -23508481,
            "WFWorkflowIconGlyphNumber": 61440,
        },
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": step.identifier,
                "WFWorkflowActionParameters": step.parameters,
            }
            for step in steps()
        ],
        # The deep link passes the task URL as text.
        "WFWorkflowInputContentItemClasses": ["WFStringContentItem", "WFURLContentItem"],
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowTypes": [],
        "WFWorkflowImportQuestions": [],
        "WFQuickActionSurfaces": [],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowHasOutputFallback": False,
    }


def unsigned_plist() -> bytes:
    return plistlib.dumps(workflow(), fmt=plistlib.FMT_XML, sort_keys=True)


def guide() -> list[dict]:
    return [
        {
            "number": number,
            "identifier": step.identifier,
            "text": step.text,
            "original": step.original,
            "depth": step.depth,
            "notes": step.notes,
        }
        for number, step in enumerate(steps(), start=1)
    ]


def save_signed(path: str) -> dict:
    target = Path(path)
    if target.suffix.lower() != ".shortcut":
        raise ValueError("Сохраните команду с расширением .shortcut.")
    data = SIGNED.read_bytes()
    if not data.startswith(b"AEA1"):
        raise ValueError("Встроенный файл команды повреждён.")
    target.write_bytes(data)
    return {"path": str(target), "size": len(data)}


def export(path: str) -> dict:
    target = Path(path)
    if target.suffix.lower() != ".plist":
        raise ValueError("Сохраните исходник с расширением .plist.")
    target.write_bytes(unsigned_plist())
    return {"path": str(target), "actions": len(steps())}
