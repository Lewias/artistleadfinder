"""Exercise the Threads UI with the real local service and a stand-in API.

No requests reach Meta, and no real user database or credentials are used.
Usage: .venv/Scripts/python scripts/smoke-threads-ui.py [screenshot-directory]
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from artist_lead_finder.browser_sessions import BrowserSessions
from artist_lead_finder.database import open_database
from artist_lead_finder.threads.service import ThreadsService
from playwright.sync_api import expect, sync_playwright


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    preview = load("ui_preview", ROOT / "scripts/ui-preview.py")
    fixtures = load("threads_fixtures", ROOT / "backend/tests/test_threads.py")
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build/threads-ui-review"
    out.mkdir(parents=True, exist_ok=True)
    vite = subprocess.Popen(
        [
            "node",
            "node_modules/vite/bin/vite.js",
            "--host",
            "127.0.0.1",
            "--port",
            "5199",
            "--strictPort",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        preview.wait_port(5199)
        with tempfile.TemporaryDirectory(prefix="threads-ui-") as temporary:
            engine, sessions = open_database(Path(temporary) / "ui.sqlite3")
            clock = fixtures.Clock()
            server = fixtures.FakeThreads(clock)
            service = ThreadsService(sessions, now=clock, client=server.client)
            service._ensure_thread = lambda: None
            fake_browser = fixtures.FakeBrowser(clock)
            fake_browser.posts = server.posts
            service.browser = fake_browser
            profiles = BrowserSessions(Path(temporary))
            profiles.write(
                fixtures.PROFILE,
                {"name": "Instagram profile", "cookies": [], "proxy": None},
            )
            service.browser_profiles = lambda: profiles.call("browser.list", {})
            background = {
                "close_to_tray": True,
                "autostart": False,
                "autostart_supported": True,
                "start_in_tray": False,
                "notifications": True,
                "keep_awake": False,
            }

            def rpc(method, params):
                return service.call(method, params)

            def native(values):
                if values:
                    background.update(values)
                return dict(background)

            def browser_action(action, params):
                if action in ("threads_open", "save"):
                    return {"ok": True}
                return profiles.call("browser." + action, params)

            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, channel="chromium")
                    page = browser.new_page(viewport={"width": 1360, "height": 900})
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.expose_function("threadsTestRpc", rpc)
                    page.expose_function("threadsTestBackground", native)
                    page.expose_function("threadsTestBrowser", browser_action)
                    page.add_init_script(
                        preview.MOCK
                        + """
                      const original = window.__TAURI_INTERNALS__.invoke;
                      window.__TAURI_INTERNALS__.invoke = async (cmd, args) => {
                        if (cmd === 'browser_action') return window.threadsTestBrowser(args.action, args.params);
                        if (cmd === 'core_request' && args.method === 'outreach.followups')
                          return {items: [], counts: {active: 0, paused: 0, review: 0}};
                        if (cmd === 'core_request' && args.method === 'assistant.state')
                          return {...await original(cmd, args), provider: {provider: 'anthropic', base_url: ''}};
                        if (cmd === 'core_request' && args.method.startsWith('threads.'))
                          return window.threadsTestRpc(args.method, args.params);
                        if (cmd === 'background_settings')
                          return window.threadsTestBackground(args?.values);
                        if (cmd === 'automation') return false;
                        return original(cmd, args);
                      };
                    """
                    )
                    page.goto("http://127.0.0.1:5199", wait_until="networkidle")
                    page.get_by_role("combobox", name="Канал", exact=True).click()
                    page.get_by_role("option", name="Threads", exact=True).click()
                    page.get_by_role(
                        "button", name="Открыть аккаунты", exact=True
                    ).click()
                    expect(
                        page.get_by_text(
                            "Подключение через официальный API", exact=True
                        )
                    ).to_have_count(0)
                    expect(
                        page.get_by_text("Расписание публикаций", exact=True)
                    ).to_have_count(0)
                    profile_row = page.locator("section.account-row").filter(
                        has_text="Instagram profile"
                    )
                    profile_row.get_by_role(
                        "button", name="Браузер", exact=True
                    ).click()
                    expect(
                        profile_row.get_by_text("@beats.daily · Подключён", exact=True)
                    ).to_be_visible()
                    profile_row.get_by_role(
                        "button", name="Ещё для Instagram profile", exact=True
                    ).click()
                    page.get_by_role(
                        "menuitem", name="Название и прокси…", exact=True
                    ).click()
                    settings_dialog = page.get_by_role(
                        "dialog", name="Настройки профиля"
                    )
                    settings_dialog.get_by_label("Название профиля", exact=True).fill(
                        "Рабочий Threads"
                    )
                    settings_dialog.get_by_role(
                        "button", name="Сохранить", exact=True
                    ).click()
                    expect(settings_dialog).not_to_be_visible()
                    expect(
                        page.get_by_role("heading", name="Рабочий Threads", exact=True)
                    ).to_be_visible()
                    page.get_by_role("button", name="Новый профиль", exact=True).click()
                    profile_dialog = page.get_by_role("dialog", name="Новый профиль")
                    profile_dialog.get_by_label("Название профиля", exact=True).fill(
                        "Второй профиль"
                    )
                    profile_dialog.get_by_role(
                        "button", name="Создать профиль", exact=True
                    ).click()
                    expect(profile_dialog).not_to_be_visible()
                    expect(
                        page.get_by_role("heading", name="Второй профиль", exact=True)
                    ).to_be_visible()
                    assert len(service.state()["browser_profiles"]) == 2
                    page.screenshot(
                        path=str(out / "accounts.png"), animations="disabled"
                    )

                    page.get_by_role("button", name="Публикации", exact=True).click()
                    page.get_by_text("Расписание публикаций", exact=True).click()
                    page.get_by_label(
                        "Время публикаций для @beats.daily", exact=True
                    ).fill("10:00, 18:30")
                    page.get_by_role(
                        "button", name="Сохранить расписание", exact=True
                    ).click()
                    expect(
                        page.get_by_role(
                            "button", name="Сохранить расписание", exact=True
                        )
                    ).to_be_enabled()
                    assert service.state()["accounts"][0]["slots"] == ["10:00", "18:30"]
                    page.get_by_role(
                        "button", name="Пауза публикаций", exact=True
                    ).click()
                    expect(
                        page.get_by_role(
                            "button", name="Продолжить публикации", exact=True
                        )
                    ).to_be_visible()
                    assert service.state()["accounts"][0]["paused"]
                    page.get_by_role(
                        "button", name="Продолжить публикации", exact=True
                    ).click()
                    expect(
                        page.get_by_role("button", name="Пауза публикаций", exact=True)
                    ).to_be_visible()
                    assert not service.state()["accounts"][0]["paused"]
                    page.screenshot(
                        path=str(out / "schedule.png"), animations="disabled"
                    )
                    page.get_by_role("button", name="Новый пост", exact=True).click()
                    dialog = page.get_by_role("dialog", name="Новая публикация")
                    dialog.get_by_label("Текст публикации", exact=True).fill(
                        "Новый релиз уже скоро.\nСлушайте превью!"
                    )
                    expect(
                        dialog.get_by_role(
                            "button", name="Опубликовать сейчас", exact=True
                        )
                    ).to_be_enabled()
                    page.screenshot(path=str(out / "composer.png"))
                    dialog.get_by_role(
                        "button", name="Сохранить черновик", exact=True
                    ).click()
                    expect(dialog).not_to_be_visible()
                    page.get_by_label("Показать", exact=True).select_option("draft")
                    row = page.locator("tr").filter(has_text="Новый релиз уже скоро.")
                    expect(row).to_have_count(1)
                    row.get_by_role("button", name="Открыть", exact=True).click()
                    dialog = page.get_by_role("dialog", name="Редактировать публикацию")
                    dialog.get_by_label("Когда опубликовать", exact=True).select_option(
                        "slot"
                    )
                    dialog.get_by_role(
                        "button", name="Запланировать", exact=True
                    ).click()
                    expect(dialog).not_to_be_visible()
                    page.get_by_label("Показать", exact=True).select_option("queue")
                    expect(page.get_by_text("Запланирован", exact=True)).to_be_visible()
                    page.get_by_role("button", name="Пауза", exact=True).click()
                    expect(
                        page.get_by_role("button", name="Продолжить", exact=True)
                    ).to_be_visible()
                    assert service.state()["paused"]
                    page.screenshot(path=str(out / "queue.png"), animations="disabled")
                    page.get_by_role("button", name="Продолжить", exact=True).click()

                    page.get_by_role("button", name="Новый пост", exact=True).click()
                    dialog = page.get_by_role("dialog", name="Новая публикация")
                    dialog.get_by_label("Текст публикации", exact=True).fill(
                        "Слишком длинно " * 50
                    )
                    expect(
                        dialog.get_by_text("Слишком длинно:", exact=False)
                    ).to_be_visible()
                    expect(
                        dialog.get_by_role(
                            "button", name="Опубликовать сейчас", exact=True
                        )
                    ).to_be_disabled()
                    dialog.get_by_label("Текст публикации", exact=True).fill(
                        "Проверка разовой публикации"
                    )
                    expect(
                        dialog.get_by_role(
                            "button", name="Опубликовать сейчас", exact=True
                        )
                    ).to_be_enabled()
                    dialog.get_by_role(
                        "button", name="Опубликовать сейчас", exact=True
                    ).click()
                    expect(dialog).not_to_be_visible()
                    service.tick()
                    assert len(server.posts) == 1
                    page.get_by_label("Показать", exact=True).select_option("published")
                    expect(
                        page.get_by_text("Проверка разовой публикации", exact=True)
                    ).to_be_visible(timeout=10000)
                    page.screenshot(
                        path=str(out / "published.png"), animations="disabled"
                    )

                    page.get_by_role("button", name="Настройки", exact=True).click()
                    expect(
                        page.get_by_role("heading", name="Фоновая работа", exact=True)
                    ).to_be_visible()
                    page.get_by_label(
                        "При закрытии сворачивать в трей", exact=True
                    ).uncheck()
                    expect(
                        page.get_by_label("При закрытии сворачивать в трей", exact=True)
                    ).not_to_be_checked()
                    expect(
                        page.get_by_label("При закрытии сворачивать в трей", exact=True)
                    ).to_be_enabled()
                    assert not background["close_to_tray"]
                    page.screenshot(
                        path=str(out / "background.png"), animations="disabled"
                    )
                    browser.close()
                    if errors:
                        raise AssertionError(json.dumps(errors, ensure_ascii=False))
                print(
                    "Threads UI: profile rows, browser login, create/edit, publication schedule, draft, pause, validation, publish and settings passed"
                )
            finally:
                service.shutdown()
                engine.dispose()
    finally:
        vite.terminate()
        vite.wait(timeout=15)


if __name__ == "__main__":
    main()
