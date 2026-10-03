"""CSV and XLSX tables of plain text cells, without third-party packages.

XLSX writing makes the smallest valid workbook (inline strings, one sheet); reading takes
the first sheet with shared or inline strings, numbers and booleans. Dates stay text.
"""

import csv
import io
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

MAX_FILE_BYTES = 20 * 1024 * 1024
# Uncompressed size of one XLSX part: protects against zip bombs.
MAX_PART_BYTES = 80 * 1024 * 1024
MAX_ROWS = 50000
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CELL = re.compile(r"([A-Z]+)(\d+)")


def read_table(path: Path) -> list[list[str]]:
    """Rows of the first sheet (XLSX) or of the file (CSV), cells as stripped text."""
    if not path.is_file():
        raise ValueError("Файл не найден.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Файл больше 20 МБ.")
    suffix = path.suffix.casefold()
    if suffix == ".xlsx":
        rows = _read_xlsx(path)
    elif suffix == ".csv":
        rows = _read_csv(path)
    else:
        raise ValueError("Выберите файл .xlsx или .csv.")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Не больше {MAX_ROWS} строк в одном файле.")
    return [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]


def write_table(path: Path, header: list[str], rows: list[list[str]]) -> None:
    suffix = path.suffix.casefold()
    if suffix not in (".xlsx", ".csv"):
        raise ValueError("Сохраните файл как .xlsx или .csv.")
    # A sibling temporary file keeps the user's file whole if writing fails.
    temporary = path.with_name(path.name + ".artist-lead-finder.tmp")
    try:
        if suffix == ".csv":
            with temporary.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(header)
                writer.writerows(rows)
        else:
            temporary.write_bytes(_xlsx_bytes([header, *rows]))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


# ---------- CSV ----------


def _read_csv(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Не удалось прочитать кодировку CSV (нужна UTF-8 или Windows-1251).")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


# ---------- XLSX ----------


def _column(letters: str) -> int:
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - 64
    return index - 1


def _letters(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _part(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise ValueError("Лист XLSX слишком большой.")
    return archive.read(info)


def _text(node) -> str:
    """Text of a shared or inline string: plain <t> or rich-text runs."""
    return "".join(item.text or "" for item in node.iter(f"{{{NS['m']}}}t"))


def _first_sheet(archive: zipfile.ZipFile) -> str:
    workbook = _part(archive, "xl/workbook.xml")
    rels = _part(archive, "xl/_rels/workbook.xml.rels")
    if workbook and rels:
        sheet = ElementTree.fromstring(workbook).find("m:sheets/m:sheet", NS)
        if sheet is not None:
            rel_id = sheet.get(f"{{{REL_NS}}}id")
            for rel in ElementTree.fromstring(rels):
                if rel.get("Id") == rel_id:
                    target = rel.get("Target", "").lstrip("/")
                    return target if target.startswith("xl/") else "xl/" + target
    return "xl/worksheets/sheet1.xml"


def _cell_value(cell, shared: list[str]) -> str:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        node = cell.find("m:is", NS)
        return _text(node) if node is not None else ""
    value = cell.find("m:v", NS)
    raw = value.text if value is not None and value.text else ""
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    if kind == "n" and raw:
        number = float(raw)
        return str(int(number)) if number.is_integer() else raw
    return raw


def _read_xlsx(path: Path) -> list[list[str]]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise ValueError("Файл XLSX повреждён.") from error
    with archive:
        shared_xml = _part(archive, "xl/sharedStrings.xml")
        shared = (
            [_text(item) for item in ElementTree.fromstring(shared_xml).findall("m:si", NS)]
            if shared_xml
            else []
        )
        sheet_xml = _part(archive, _first_sheet(archive))
        if sheet_xml is None:
            raise ValueError("В файле XLSX нет листа.")
        rows: list[list[str]] = []
        for row in ElementTree.fromstring(sheet_xml).iterfind("m:sheetData/m:row", NS):
            values: dict[int, str] = {}
            for position, cell in enumerate(row.findall("m:c", NS)):
                match = CELL.match(cell.get("r", ""))
                values[_column(match.group(1)) if match else position] = _cell_value(cell, shared)
            if values:
                rows.append([values.get(index, "") for index in range(max(values) + 1)])
            if len(rows) > MAX_ROWS:
                break
        return rows


def excel_date(text: str) -> datetime | None:
    """A date cell as Excel stores it: days since 1899-12-30."""
    try:
        serial = float(text)
    except ValueError:
        return None
    if not 1 <= serial <= 80000:
        return None
    return datetime(1899, 12, 30) + timedelta(days=serial)


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    sheet_rows = []
    for number, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{_letters(index)}{number}" t="inlineStr"><is><t xml:space="preserve">'
            f"{escape(_xml_safe(str(value)))}</t></is></c>"
            for index, value in enumerate(row)
            if value != ""
        )
        sheet_rows.append(f'<row r="{number}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS["m"]}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{NS["m"]}" xmlns:r="{REL_NS}">'
            '<sheets><sheet name="CRM" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _xml_safe(text: str) -> str:
    """Control characters are not allowed in XML 1.0."""
    return "".join(char for char in text if char in "\t\n\r" or ord(char) >= 32)
