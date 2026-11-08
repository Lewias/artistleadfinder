"""Offline pass: the account keeps working for a while when the server cannot be reached.

While the server answers, it issues a pass (supabase/functions/offline-token): the user,
this device, the role and an expiry, signed with the server's Ed25519 key. The app keeps
it encrypted for the OS user and checks the signature with the public key built into the
app (project.OFFLINE_PUBLIC_KEY), so a pass cannot be written or extended by hand.

When the server is unreachable (no network, the server down) a valid pass keeps the app
open until it expires. A server that answers always decides: a block, a revoked key or a
refused session end the pass at once.

Format: base64url(payload JSON) "." base64url(signature over the payload part).
"""

import base64
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VERSION = 1
# A clock set back further than this below the last time the app saw is not trusted.
CLOCK_SKEW = 300
MAX_TOKEN = 4096


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class OfflinePass:
    user_id: str
    device_id: str
    role: str
    email: str
    display_name: str
    issued_at: int
    expires_at: int

    def status(self) -> dict:
        """The last known account status the pass stands for."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "blocked": False,
            "licensed": True,
        }


def public_key(text: str) -> Ed25519PublicKey | None:
    """The app's built-in key (base64 of the 32 raw bytes); None turns passes off."""
    try:
        raw = base64.b64decode(text.strip(), validate=True) if text and text.strip() else b""
        return Ed25519PublicKey.from_public_bytes(raw) if len(raw) == 32 else None
    except ValueError:
        return None


def verify(
    token: str | None,
    key: Ed25519PublicKey | None,
    user_id: str | None,
    device_id: str,
    now: float,
) -> OfflinePass | None:
    """The pass when the signature, owner, device and time all check out, else None."""
    if not token or key is None or len(token) > MAX_TOKEN or token.count(".") != 1:
        return None
    body, signature = token.split(".")
    try:
        key.verify(_b64decode(signature), body.encode("ascii"))
        data = json.loads(_b64decode(body))
    except (InvalidSignature, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict) or data.get("v") != VERSION:
        return None
    try:
        found = OfflinePass(
            user_id=str(data["sub"]),
            device_id=str(data["dev"]),
            role=str(data.get("role") or "user"),
            email=str(data.get("email") or ""),
            display_name=str(data.get("name") or ""),
            issued_at=int(data["iat"]),
            expires_at=int(data["exp"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if found.device_id != device_id or (user_id and found.user_id != user_id):
        return None
    if found.role not in ("user", "moderator", "admin"):
        return None
    if not found.issued_at - CLOCK_SKEW <= now < found.expires_at:
        return None
    return found
