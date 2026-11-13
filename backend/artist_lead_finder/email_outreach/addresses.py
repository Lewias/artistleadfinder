"""Email addresses: normalised, validated and read from pasted text or a table."""

import re

MAX_ADDRESS = 254
ADDRESS = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,24}"
)
FIND = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
# Nobody reads these; a cold email there only costs reputation.
NO_REPLY = re.compile(r"^(?:no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster)@")
NAME_COLUMNS = ("name", "имя", "контакт", "contact", "full name", "artist", "артист")
EMAIL_COLUMNS = ("email", "e-mail", "почта", "mail")


def normalize(value: str) -> str | None:
    """Lower-case address, or None when it is not a usable one."""
    address = (value or "").strip().strip("<>.,;:\"'()[]").lower()
    if address.startswith("mailto:"):
        address = address[7:]
    if len(address) > MAX_ADDRESS or not ADDRESS.fullmatch(address):
        return None
    if NO_REPLY.match(address) or ".." in address:
        return None
    return address


def from_text(text: str) -> list[dict]:
    """Recipients from pasted lines: «email», «email, Name» or «Name <email>»."""
    found: list[dict] = []
    for line in (text or "").splitlines():
        match = FIND.search(line)
        if not match:
            continue
        address = normalize(match.group(0))
        if not address:
            continue
        rest = (line[: match.start()] + " " + line[match.end() :]).strip()
        name = re.sub(r"[<>,;\t|\"]+", " ", rest)
        found.append({"email": address, "name": re.sub(r"\s+", " ", name).strip()[:160]})
    return found


def from_table(rows: list[list[str]]) -> list[dict]:
    """Recipients from a CSV/XLSX sheet: an «email» column (or any cell with an address)
    and an optional name column; the first row is the header when it names a column."""
    if not rows:
        return []
    header = [cell.casefold().strip() for cell in rows[0]]
    email_at = next((header.index(name) for name in EMAIL_COLUMNS if name in header), None)
    name_at = next((header.index(name) for name in NAME_COLUMNS if name in header), None)
    body = rows[1:] if email_at is not None or name_at is not None else rows
    found = []
    for row in body:
        cells = [cell or "" for cell in row]
        if email_at is not None and email_at < len(cells):
            address = normalize(cells[email_at])
        else:
            address = next((normalize(cell) for cell in cells if normalize(cell)), None)
        if not address:
            continue
        name = cells[name_at].strip() if name_at is not None and name_at < len(cells) else ""
        found.append({"email": address, "name": name[:160]})
    return found
