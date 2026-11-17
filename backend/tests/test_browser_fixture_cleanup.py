"""Missing Chromium must not poison subsequent Playwright fixtures on CI."""

import asyncio

import pytest
from playwright.sync_api import BrowserType, Error, sync_playwright
from test_discovery_fixtures import browser as discovery_browser
from test_profile_fixtures import browser as profile_browser


@pytest.mark.parametrize("fixture", [discovery_browser, profile_browser])
def test_missing_chromium_closes_the_driver_loop(monkeypatch, fixture):
    def missing(*args, **kwargs):
        raise Error("Executable doesn't exist at fixture-missing")

    monkeypatch.setattr(BrowserType, "launch", missing)
    with pytest.raises(pytest.skip.Exception):
        next(fixture.__wrapped__())
    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()
    # Starting a different fixture must work after the skipped one.
    with sync_playwright() as playwright:
        assert playwright.chromium.name == "chromium"
