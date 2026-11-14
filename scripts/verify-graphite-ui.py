"""Visual checks against a running Tauri WebView2 review instance, with real RPC data.

Start Vite and Tauri with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222.
No API stubs or fabricated records; only read-only UI interactions. Captures WebView viewports.
"""
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/design/graphite/review'
OUT.mkdir(parents=True, exist_ok=True)
report = {'runtime': 'Tauri 2 / real WebView2 / actual core RPC', 'viewports': [], 'checks': [], 'page_errors': []}
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://127.0.0.1:9222')
    page = next(page for context in browser.contexts for page in context.pages if '1420' in page.url)
    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
    page.reload(wait_until='domcontentloaded')
    page.get_by_role('button', name=re.compile('^База артистов')).click()
    page.locator('.alf-reference-information').wait_for(timeout=20000)
    page.wait_for_timeout(400)
    for w,h in [(1360,900),(1600,1000),(1024,700)]:
        page.set_viewport_size({'width':w,'height':h})
        page.wait_for_timeout(200)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('.sidebar').evaluate('el=>el.scrollWidth<=el.clientWidth')
        assert page.locator('.alf-reference-information').is_visible()
        assert page.locator('.alf-reference-statistics').is_visible()
        assert page.locator('.alf-reference-tools .alf-panel').count()==3
        page.screenshot(path=str(OUT/f'reference-{w}x{h}.png'))
        report['viewports'].append({'width':w,'height':h,'document_overflow':False})
    page.set_viewport_size({'width':1360,'height':900})
    chooser=page.get_by_role('combobox',name='Выбранный артист',exact=True)
    first=chooser.inner_text()
    chooser.click();chooser.press('ArrowDown');chooser.press('Enter')
    page.wait_for_timeout(400)
    assert chooser.inner_text()!=first
    page.get_by_role('button',name='Открыть анализ',exact=True).click()
    assert page.get_by_text('Анализ и соответствие',exact=True).is_visible()
    page.screenshot(path=str(OUT/'reference-analysis.png'))
    page.get_by_role('button',name='История общения',exact=True).click()
    assert page.get_by_text('История общения',exact=True).last.is_visible()
    page.locator('.alf-reference-database').evaluate('el=>el.scrollTo(0,0)')
    page.get_by_role('button',name='Все артисты',exact=True).click()
    page.locator('.alf-artist-open').first.wait_for()
    assert page.get_by_role('searchbox',name='Поиск профиля').is_visible()
    page.get_by_role('combobox',name='Жанр',exact=True).click()
    assert page.get_by_role('listbox',name='Жанр').is_visible()
    page.get_by_role('combobox',name='Жанр',exact=True).press('Escape')
    page.locator('.alf-artist-row').first.get_by_role('checkbox').check()
    assert page.get_by_role('button',name='Добавить выбранных в рассылку',exact=True).is_enabled()
    page.screenshot(path=str(OUT/'reference-collection-selection.png'))
    page.locator('.alf-artist-row').first.get_by_role('checkbox').uncheck()
    page.get_by_role('button',name='Фильтры',exact=True).click()
    page.get_by_role('combobox',name='Источник',exact=True).click()
    page.get_by_role('option',name='Все источники',exact=True).click()
    page.get_by_role('button',name='Готово',exact=True).click()
    assert page.get_by_role('button',name='Фильтры',exact=True).evaluate('el=>el===document.activeElement')
    page.get_by_role('combobox',name='Экспорт CSV',exact=True).click()
    assert page.get_by_role('option',name='Вся база',exact=True).is_visible()
    page.get_by_role('combobox',name='Экспорт CSV',exact=True).press('Escape')
    page.get_by_role('searchbox',name='Поиск профиля').fill('__alf_visual_review_no_match__')
    page.get_by_text('Артисты не найдены',exact=True).wait_for(timeout=15000)
    page.screenshot(path=str(OUT/'reference-empty-search.png'))
    page.get_by_role('button',name='Сбросить фильтры',exact=True).last.click()
    page.locator('.alf-artist-open').first.wait_for(timeout=15000)
    page.locator('.alf-artist-open').first.click()
    page.locator('.alf-reference-information').wait_for()
    page.locator('.alf-reference-fields .alf-dropdown').focus()
    page.keyboard.press('Control+f')
    assert page.get_by_role('searchbox',name='Поиск профиля').is_visible()
    page.get_by_role('button',name='Вернуться к профилю',exact=True).click()
    page.locator('.alf-reference-database').evaluate('el=>el.scrollTo(0,0)')
    page.locator('.account-button').click()
    assert page.get_by_role('menuitem',name='Выйти из аккаунта').is_visible()
    page.locator('.account-button').click()
    page.get_by_role('combobox',name='Канал',exact=True).click()
    page.get_by_role('option',name='Email',exact=True).click()
    assert page.get_by_role('button',name=re.compile('^Кампании')).is_visible()
    page.get_by_role('combobox',name='Канал',exact=True).click()
    page.get_by_role('option',name='Instagram',exact=True).click()
    page.locator('.alf-reference-information').wait_for()
    page.locator('.alf-reference-database').evaluate('el=>el.scrollTo(0,0)')
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT/'reference-final.png'))
    report['checks']=['real Tauri/core data','reference composition at three sizes','no document/sidebar overflow','artist chooser with keyboard','analysis and history access','full artist list/search','bulk selection','filters and focus return','all export scopes available','empty search and reset','Ctrl+F opens collection','sidebar account menu remains accessible','per-channel last-page memory']
    assert not report['page_errors'], report['page_errors']
    browser.close()
(OUT/'reference-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
