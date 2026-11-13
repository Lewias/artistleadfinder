"""Exercise the opt-in development fixture UI; never contacts the live core.
Run VITE_DESIGN_FIXTURES=1 npm run dev first, then this script.
Browser screenshots complement, and do not replace, native evidence.
"""
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/redesign/evidence/shell-leads/browser"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1360, "height": 900}, color_scheme="dark")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:1420/", wait_until="networkidle")
        assert page.locator("html").get_attribute("data-fixture") == "true", "Refuse non-fixture UI"
        page.get_by_role("button", name="База артистов").click()
        table = page.get_by_role("table", name="База артистов")
        expect(table.locator("tbody tr")).to_have_count(30)
        expect(page.get_by_text("Страница 1 из 54", exact=True)).to_be_visible()
        page.screenshot(path=str(OUT / "01-table.png"))
        # Real filters and pagination over the synthetic dataset.
        page.get_by_role("combobox", name="Статус", exact=True).select_option("qualified")
        expect(table.locator("tbody tr").first).to_contain_text("Подходит")
        expect(table.locator("tbody tr")).to_have_count(30)
        page.get_by_role("combobox", name="Статус", exact=True).select_option("")
        page.get_by_role("button", name="Далее", exact=True).click()
        expect(page.get_by_text("Страница 2 из 54", exact=True)).to_be_visible()
        expect(table.locator("tbody input").first).not_to_have_attribute("aria-label", "Выбрать amara.sol")
        page.get_by_role("button", name="Назад", exact=True).click()
        expect(page.get_by_text("Страница 1 из 54", exact=True)).to_be_visible()
        expect(table.locator("tbody input").first).to_have_attribute("aria-label", "Выбрать amara.sol")
        first = table.locator("tbody input[type=checkbox]").first
        first.check()
        expect(page.get_by_role("checkbox", name="Выбрать страницу", exact=True)).to_be_checked(indeterminate=True)
        page.screenshot(path=str(OUT / "02-selection.png"))
        page.get_by_role("button", name="Экспорт CSV", exact=True).click()
        expect(page.get_by_role("menuitem", name="По текущим фильтрам")).to_be_focused()
        # The first enabled menu item is the filtered export.
        page.keyboard.press("ArrowDown")
        expect(page.get_by_role("menuitem", name="Выбранные (1)")).to_be_focused()
        page.screenshot(path=str(OUT / "03-export-menu.png"))
        page.keyboard.press("Escape")
        expect(page.get_by_role("button", name="Экспорт CSV", exact=True)).to_be_focused()
        page.get_by_role("button", name="Удалить", exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("button", name="Отмена")).to_be_focused()
        page.keyboard.press("Tab")
        assert dialog.evaluate("el => el.contains(document.activeElement)")
        page.screenshot(path=str(OUT / "04-delete-dialog.png"))
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role("button", name="Удалить", exact=True)).to_be_focused()
        # Keep selection/list context while inspecting, with missing follower value.
        table.locator(".profile-cell").first.click()
        inspector = page.get_by_role("dialog")
        expect(inspector).to_contain_text("Подписчики не прочитаны")
        expect(table).to_be_visible()
        page.screenshot(path=str(OUT / "05-inspector.png"))
        page.get_by_role("button", name="Закрыть профиль").click()
        expect(table.locator(".profile-cell").first).to_be_focused()
        search = page.get_by_role("textbox", name="Поиск профиля")
        search.fill("!offline")
        expect(page.get_by_role("alert")).to_contain_text("Показаны последние загруженные данные")
        page.screenshot(path=str(OUT / "06-error.png"))
        search.fill("no-such-artist-fixture")
        expect(page.get_by_text("Нет профилей с такими условиями.", exact=False)).to_be_visible()
        page.screenshot(path=str(OUT / "07-empty.png"))
        search.fill("")
        expect(table.locator("tbody tr")).to_have_count(30)
        page.get_by_text("Ещё фильтры", exact=True).click()
        page.screenshot(path=str(OUT / "08-filters.png"))
        page.get_by_text("Ещё фильтры", exact=True).click()
        for width, height in [(1024, 700), (1920, 1080)]:
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(OUT / f"09-table-{width}.png"))
            table.locator(".profile-cell").first.click()
            expect(page.get_by_role("dialog")).to_contain_text("Подписчики не прочитаны")
            page.screenshot(path=str(OUT / f"10-inspector-{width}.png"))
            page.keyboard.press("Escape")
        assert not errors, errors
        print("PASS: filters, 54-page dataset, selection, menu keys, dialog focus/return, inspector, error recovery, empty state, 1024/1920 layouts; no page errors")
        browser.close()


if __name__ == "__main__":
    main()
