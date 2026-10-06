"""Admin actions: accounts, roles, blocking and access keys.

The database functions check the admin role again on the server; account creation and
passwords go through the `admin-users` Edge Function, which holds the service role key.
"""

import re

from ..errors import UserError
from .session import AccountService

USER_ID = re.compile(r"^[0-9a-f-]{36}$")


def _user(params: dict, key: str = "user_id") -> str:
    value = str(params.get(key) or "")
    if not USER_ID.match(value):
        raise UserError("Некорректный пользователь.")
    return value


def _role(params: dict) -> str:
    """The role a new key grants: user unless moderator is asked."""
    role = params.get("role") or "user"
    if role not in ("user", "moderator"):
        raise UserError("Ключ даёт роль пользователя или модератора.")
    return role


class AdminService:
    def __init__(self, account: AccountService):
        self.account = account

    def _token(self) -> str:
        if not self.account.is_admin:
            raise UserError("Доступно только админу.")
        return self.account.token()

    def call(self, method: str, params: dict):
        actions = {
            "admin.overview": self.overview,
            "admin.create_user": self.create_user,
            "admin.set_password": self.set_password,
            "admin.set_role": self.set_role,
            "admin.set_blocked": self.set_blocked,
            "admin.set_name": self.set_name,
            "admin.issue_key": self.issue_key,
            "admin.revoke_key": self.revoke_key,
            "admin.unbind_key": self.unbind_key,
        }
        if method not in actions:
            raise UserError("Неизвестное действие.")
        return actions[method](params)

    def overview(self, params: dict | None = None) -> dict:
        token = self._token()
        client = self.account.client
        users = client.select(
            "profiles",
            {"select": "id,email,display_name,role,blocked,created_at", "order": "created_at.asc"},
            token,
        )
        keys = client.select(
            "license_keys",
            {
                "select": "id,key_hint,user_id,device_id,note,role,"
                "created_at,activated_at,revoked_at",
                "order": "created_at.desc",
            },
            token,
        )
        return {
            "me": self.account.user_id,
            "users": users or [],
            "keys": [
                {
                    **{k: v for k, v in key.items() if k != "device_id"},
                    # Activated by an account (the key belongs to it until freed).
                    "bound": bool(key.get("activated_at")),
                }
                for key in keys or []
            ],
        }

    def _rpc(self, name: str, params: dict):
        return self.account.client.rpc(name, params, self._token())

    def create_user(self, params: dict) -> dict:
        email = str(params.get("email") or "").strip().lower()
        password = str(params.get("password") or "")
        name = " ".join(str(params.get("display_name") or "").split())[:80]
        if "@" not in email or len(email) > 254:
            raise UserError("Введите email.")
        if len(password) < 8:
            raise UserError("Пароль — не короче 8 символов.")
        created = self.account.client.function(
            "admin-users",
            {"action": "create", "email": email, "password": password, "display_name": name},
            self._token(),
        )
        result = {"user": created, "key": None}
        if params.get("issue_key") and isinstance(created, dict) and created.get("id"):
            result["key"] = self._rpc(
                "admin_issue_key",
                {"p_user": created["id"], "p_note": "при создании", "p_role": _role(params)},
            )
        return result

    def set_password(self, params: dict) -> dict:
        password = str(params.get("password") or "")
        if len(password) < 8:
            raise UserError("Пароль — не короче 8 символов.")
        self.account.client.function(
            "admin-users",
            {"action": "set_password", "user_id": _user(params), "password": password},
            self._token(),
        )
        return {"ok": True}

    def set_role(self, params: dict) -> dict:
        role = params.get("role")
        if role not in ("user", "moderator"):
            raise UserError("Можно назначить только пользователя или модератора.")
        self._rpc("admin_set_role", {"p_user": _user(params), "p_role": role})
        return self.overview()

    def set_blocked(self, params: dict) -> dict:
        self._rpc(
            "admin_set_blocked", {"p_user": _user(params), "p_blocked": bool(params.get("blocked"))}
        )
        return self.overview()

    def set_name(self, params: dict) -> dict:
        name = " ".join(str(params.get("display_name") or "").split())[:80]
        self._rpc("admin_set_name", {"p_user": _user(params), "p_name": name})
        return self.overview()

    def issue_key(self, params: dict) -> dict:
        user = _user(params) if params.get("user_id") else None
        note = " ".join(str(params.get("note") or "").split())[:200]
        key = self._rpc(
            "admin_issue_key", {"p_user": user, "p_note": note, "p_role": _role(params)}
        )
        return {"key": key, **self.overview()}

    def revoke_key(self, params: dict) -> dict:
        self._rpc("admin_revoke_key", {"p_id": _user(params, "id")})
        return self.overview()

    def unbind_key(self, params: dict) -> dict:
        self._rpc("admin_unbind_key", {"p_id": _user(params, "id")})
        return self.overview()
