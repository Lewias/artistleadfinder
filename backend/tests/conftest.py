import secrets

import pytest

from artist_lead_finder import chromium_runtime, secret_box


@pytest.fixture(autouse=True)
def isolated_keychain(monkeypatch):
    """On macOS tests never read or write the developer's login Keychain."""
    key = secrets.token_bytes(32)
    monkeypatch.setattr(secret_box, "_keychain_key", lambda create: key)


@pytest.fixture(autouse=True)
def no_chromium_download(monkeypatch):
    """Tests never download Chromium (the macOS first-run path); they opt in with a fake."""
    monkeypatch.setattr(chromium_runtime, "DOWNLOADS_CHROMIUM", False)
