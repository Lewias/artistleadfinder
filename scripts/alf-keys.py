"""Access keys of Artist Lead Finder from the console: issue, list, revoke.

Signs in as an admin with email and password (the same account as in the app) and calls
the admin functions of the server. No server secrets are stored here; the password is asked
every run and kept only in memory. The last email is remembered next to this script.
"""

import getpass
import json
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
REMEMBER = Path(__file__).with_name(".alf-keys-email")


def project() -> tuple[str, str]:
    """URL and public key of the server, read from the app's own settings."""
    source = ROOT / "backend" / "artist_lead_finder" / "account" / "project.py"
    text = source.read_text(encoding="utf-8")
    url = re.search(r'^SUPABASE_URL = "([^"]*)"', text, re.MULTILINE)
    # The key may be split over several string literals; it is the last setting in the file.
    _, found, rest = text.partition("\nSUPABASE_ANON_KEY =")
    anon = "".join(re.findall(r'"([^"]*)"', rest)) if found else ""
    if not url or not url.group(1) or not anon:
        sys.exit("В project.py не указан сервер.")
    return url.group(1).rstrip("/"), anon


URL, ANON = project()
CONTEXT = ssl.create_default_context()


class Refused(Exception):
    pass


def request(method: str, path: str, body=None, token: str | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("apikey", ANON)
    req.add_header("Authorization", f"Bearer {token or ANON}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20, context=CONTEXT) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read() or b"{}")
        except ValueError:
            payload = {}
        message = str(
            payload.get("message")
            or payload.get("msg")
            or payload.get("error_description")
            or ""
        )
        if re.search(r"credential|grant", message, re.IGNORECASE):
            message = "Неверный email или пароль."
        elif re.search(r"jwt|token", message, re.IGNORECASE) or error.code == 401:
            message = "Сессия истекла."
        raise Refused(message or f"Сервер ответил {error.code}.") from None
    except (urllib.error.URLError, OSError):
        raise Refused("Нет связи с сервером. Проверьте интернет.") from None
    return json.loads(raw) if raw else None


# ---------- session ----------


class Session:
    def __init__(self):
        self.token = None

    def login(self):
        remembered = (
            REMEMBER.read_text(encoding="utf-8").strip() if REMEMBER.exists() else ""
        )
        while True:
            hint = f" [{remembered}]" if remembered else ""
            email = input(f"Email админа{hint}: ").strip().lower() or remembered
            password = getpass.getpass("Пароль (не отображается): ")
            try:
                tokens = request(
                    "POST",
                    "/auth/v1/token?grant_type=password",
                    {"email": email, "password": password},
                )
                self.token = tokens["access_token"]
                me = request("POST", "/rest/v1/rpc/session_status", {}, self.token)
            except Refused as error:
                print(f"  ! {error}\n")
                continue
            if not me or me.get("role") != "admin":
                print("  ! Этот аккаунт не админ.\n")
                continue
            REMEMBER.write_text(email, encoding="utf-8")
            print(f"\nВы вошли: {me.get('display_name') or email} (админ)\n")
            return

    def call(self, method, path, body=None):
        try:
            return request(method, path, body, self.token)
        except Refused as error:
            if str(error) != "Сессия истекла.":
                raise
            print("Сессия истекла, войдите снова.")
            self.login()
            return request(method, path, body, self.token)

    def rpc(self, name, body):
        return self.call("POST", f"/rest/v1/rpc/{name}", body)

    def select(self, table, query):
        return self.call("GET", f"/rest/v1/{table}?{urlencode(query)}")


# ---------- data ----------


def users(session):
    return session.select(
        "profiles",
        {"select": "id,email,display_name,role,blocked", "order": "created_at.asc"},
    )


def keys(session):
    return session.select(
        "license_keys",
        {
            "select": "id,key_hint,user_id,note,role,created_at,activated_at,revoked_at",
            "order": "created_at.desc",
        },
    )


def name_of(user):
    return user.get("display_name") or user.get("email") or "—"


def has_access(user_id, all_keys):
    return any(
        k["user_id"] == user_id and k["activated_at"] and not k["revoked_at"]
        for k in all_keys
    )


def copy(text):
    try:
        subprocess.run("clip", input=text.encode("ascii"), check=True, shell=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def show_issued(issued):
    print()
    for key in issued:
        print(f"   {key}")
    if copy("\r\n".join(issued)):
        print("\n   Скопировано в буфер обмена.")
    print(
        "   Ключ показывается один раз: сохраните его или сразу отправьте человеку.\n"
    )


def ask_int(prompt, default, low, high):
    value = input(f"{prompt} [{default}]: ").strip()
    if not value:
        return default
    if value.isdigit() and low <= int(value) <= high:
        return int(value)
    print(f"  ! Введите число от {low} до {high}.")
    return None


# ---------- actions ----------


def ask_role():
    print("Роль по ключу:  1. Пользователь (только своя CRM)   2. Модератор (CRM всех)")
    choice = ask_int("Роль", 1, 1, 2)
    return None if choice is None else ("user", "moderator")[choice - 1]


def issue_free(session):
    count = ask_int("Сколько ключей", 1, 1, 50)
    if count is None:
        return
    role = ask_role()
    if role is None:
        return
    note = input("Заметка (кому, необязательно): ").strip()
    body = {"p_user": None, "p_note": note, "p_role": role}
    issued = [session.rpc("admin_issue_key", body) for _ in range(count)]
    print(
        f"\nГотово, ключей: {len(issued)}. Первый аккаунт, который введёт ключ, получит доступ."
    )
    show_issued(issued)


def issue_for_user(session):
    all_keys = keys(session)
    waiting = [
        u
        for u in users(session)
        if not u["blocked"] and not has_access(u["id"], all_keys)
    ]
    if not waiting:
        print("\nУ всех зарегистрированных уже есть доступ.\n")
        return
    print("\nБез доступа:")
    for index, user in enumerate(waiting, start=1):
        extra = f"  {user['email']}" if user.get("display_name") else ""
        print(f"  {index}. {name_of(user)}{extra}")
    choice = ask_int("Номер пользователя", 1, 1, len(waiting))
    if choice is None:
        return
    user = waiting[choice - 1]
    role = ask_role()
    if role is None:
        return
    note = input("Заметка (необязательно): ").strip()
    key = session.rpc(
        "admin_issue_key", {"p_user": user["id"], "p_note": note, "p_role": role}
    )
    print(f"\nКлюч для {name_of(user)} (работает только у этого аккаунта):")
    show_issued([key])


def list_keys(session):
    people = {u["id"]: u for u in users(session)}
    rows = keys(session)
    if not rows:
        print("\nКлючей пока нет.\n")
        return
    print(f"\n  {'Ключ':<12}{'Роль':<14}{'Состояние':<17}{'Кому':<28}Заметка")
    for key in rows:
        if key["revoked_at"]:
            state = "отозван"
        elif key["activated_at"]:
            state = "активирован"
        elif key["user_id"]:
            state = "ждёт активации"
        else:
            state = "свободный"
        owner = name_of(people[key["user_id"]]) if key["user_id"] in people else "—"
        role = "модератор" if key.get("role") == "moderator" else "пользователь"
        print(
            f"  …{key['key_hint']:<11}{role:<14}{state:<17}{owner[:26]:<28}{key['note'] or ''}"
        )
    print()


def list_users(session):
    all_keys = keys(session)
    print()
    for user in users(session):
        role = {"admin": "админ", "moderator": "модератор"}.get(
            user["role"], "пользователь"
        )
        if user["blocked"]:
            access = "заблокирован"
        else:
            access = (
                "есть доступ" if has_access(user["id"], all_keys) else "нет доступа"
            )
        print(f"  {name_of(user)[:26]:<28}{user['email'][:32]:<34}{role:<14}{access}")
    print()


def revoke(session):
    hint = input("Последние 4 символа ключа: ").strip().upper()
    found = [k for k in keys(session) if k["key_hint"] == hint and not k["revoked_at"]]
    if not found:
        print("  ! Действующий ключ с такими символами не найден.\n")
        return
    if len(found) > 1:
        print("  ! Таких ключей несколько, отзовите его в приложении.\n")
        return
    if input(f"Отозвать ключ …{hint} навсегда? (да/нет): ").strip().lower() not in (
        "да",
        "д",
        "y",
    ):
        return
    session.rpc("admin_revoke_key", {"p_id": found[0]["id"]})
    print(
        "Ключ отозван: аккаунт потеряет доступ при следующей проверке (до 5 минут).\n"
    )


MENU = [
    ("Выпустить свободный ключ", issue_free),
    ("Выпустить ключ зарегистрированному пользователю", issue_for_user),
    ("Список ключей", list_keys),
    ("Список пользователей", list_users),
    ("Отозвать ключ", revoke),
]


def main():
    print(f"Ключи Artist Lead Finder — {URL}\n")
    session = Session()
    session.login()
    while True:
        for index, (label, _) in enumerate(MENU, start=1):
            print(f"  {index}. {label}")
        print("  0. Выход")
        choice = input("> ").strip()
        if choice in ("0", ""):
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(MENU):
            continue
        try:
            MENU[int(choice) - 1][1](session)
        except Refused as error:
            print(f"  ! {error}\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
