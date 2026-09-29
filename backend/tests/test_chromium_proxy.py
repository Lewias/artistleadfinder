import threading
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


def test_first_run_download_blocks_launch_until_ready():
    started = threading.Event()
    release = threading.Event()

    def slow_install():
        started.set()
        return release.wait(5)

    runtime = ChromiumRuntime(SimpleNamespace(), installer=slow_install)
    runtime.installed = False
    runtime.prepare()
    assert started.wait(5)
    with pytest.raises(BrowserLaunchError, match="скачивается"):
        runtime._ready(wait=0.01)
    release.set()
    runtime._ready(wait=5)
    assert runtime.installed


def test_failed_download_reports_and_retries():
    attempts = []
    runtime = ChromiumRuntime(SimpleNamespace(), installer=lambda: attempts.append(1) and False)
    runtime.installed = False
    with pytest.raises(BrowserLaunchError, match="Не удалось скачать"):
        runtime._ready(wait=5)
    runtime.installer = lambda: True
    runtime._ready(wait=5)
    assert runtime.installed and attempts == [1]
