from types import SimpleNamespace

import pytest

from artist_lead_finder.chromium_runtime import BrowserLaunchError, ChromiumRuntime


def test_proxy_launch_error_never_exposes_playwright_credentials(monkeypatch):
    runtime = ChromiumRuntime(
        SimpleNamespace(
            read=lambda _identifier: {
                "cookies": [],
                "proxy": {
                    "scheme": "socks5",
                    "host": "proxy.example.com",
                    "port": 1080,
                    "username": "user",
                    "password": "secret-do-not-show",
                },
            }
        )
    )

    def fail(**_options):
        raise RuntimeError("Proxy failure: secret-do-not-show")

    monkeypatch.setattr(runtime, "_engine", lambda: SimpleNamespace(launch=fail))
    with pytest.raises(BrowserLaunchError) as error:
        runtime.open("a" * 32, proxy_override="http://127.0.0.1:20000")
    assert "secret-do-not-show" not in str(error.value)
    assert "прокси" in str(error.value)
