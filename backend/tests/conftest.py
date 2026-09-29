import secrets

import pytest

from artist_lead_finder import secret_box


@pytest.fixture(autouse=True)
def isolated_keychain(monkeypatch):
    """On macOS tests never read or write the developer's login Keychain."""
    key = secrets.token_bytes(32)
    monkeypatch.setattr(secret_box, "_keychain_key", lambda create: key)
