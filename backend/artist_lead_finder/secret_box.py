"""Encryption of local secrets (Instagram sessions, the AI key) bound to the OS user.

Windows: DPAPI, as before. macOS: AES-GCM with a random key kept in the user's login
Keychain; the files carry a prefix so the format is recognised. Keys never enter logs.
"""

import ctypes
import os
import secrets
import subprocess
import sys

from .errors import UserError

MAC_PREFIX = b"ALF1"
KEYCHAIN_SERVICE = "ArtistLeadFinder"
KEYCHAIN_ACCOUNT = "secret-box"


def protect(data: bytes, decrypt: bool = False) -> bytes:
    if os.name == "nt":
        return _dpapi(data, decrypt)
    if sys.platform == "darwin":
        return keychain_box(data, decrypt, _keychain_key)
    raise UserError("Хранилище секретов поддерживается на Windows и macOS.")


def keychain_box(data: bytes, decrypt: bool, key_source) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not decrypt:
        nonce = secrets.token_bytes(12)
        return MAC_PREFIX + nonce + AESGCM(key_source(True)).encrypt(nonce, data, MAC_PREFIX)
    if not data.startswith(MAC_PREFIX) or len(data) < len(MAC_PREFIX) + 12 + 16:
        raise ValueError("Session decryption failed")
    nonce, body = data[4:16], data[16:]
    try:
        return AESGCM(key_source(False)).decrypt(nonce, body, MAC_PREFIX)
    except InvalidTag as error:
        raise ValueError("Session decryption failed") from error


def _keychain_key(create: bool) -> bytes:
    """32-byte key from the login Keychain; created on first encryption."""
    found = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            KEYCHAIN_ACCOUNT,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        capture_output=True,
        text=True,
    )
    if found.returncode == 0 and len(found.stdout.strip()) == 64:
        return bytes.fromhex(found.stdout.strip())
    if not create:
        raise UserError("Ключ шифрования не найден в Keychain.")
    key = secrets.token_bytes(32)
    added = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-a",
            KEYCHAIN_ACCOUNT,
            "-s",
            KEYCHAIN_SERVICE,
            "-U",
            "-w",
            key.hex(),
        ],
        capture_output=True,
    )
    if added.returncode != 0:
        raise UserError("Не удалось сохранить ключ шифрования в Keychain.")
    return key


def _dpapi(data: bytes, decrypt: bool) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("Session encryption failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(output.data)
