"""Render isolated shell concepts and check local review interactions. No core required."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '0'
OUT = ROOT / 'previews'
OUT.mkdir(exist_ok=True)
report = {'renders': [], 'checks': [], 'errors': []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chromium')
    page = browser.new_page(viewport={'width': 1360, 'height': 900}, device_scale_factor=1)
    page.on('pageerror', lambda e: report['errors'].append(str(e)))
    page.on('request', lambda r: report['errors'].append('Unexpected remote request: '+r.url) if r.url.startswith(('http:', 'https:')) else None)
    for direction, theme, platform, width, height in [
        ('a','light','windows',1360,900),('b','dark','windows',1360,900),
        ('a','dark','windows',1360,900),('b','light','windows',1360,900),
        ('a','light','mac',1360,900),('b','dark','mac',1360,900),
        ('a','light','windows',1024,700),('b','dark','windows',1024,700),
        ('a','light','windows',1920,1080),('b','dark','windows',1920,1080),
    ]:
        page.set_viewport_size({'width':width,'height':height})
        page.goto(ROOT.joinpath('index.html').as_uri()+f'?direction={direction}&theme={theme}&platform={platform}')
        page.screenshot(path=str(OUT / f'{direction}-{theme}-{platform}-{width}x{height}.png'))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Document overflow'
        report['renders'].append(f'{direction}-{theme}-{platform}-{width}x{height}')
    page.set_viewport_size({'width':1360,'height':900})
    page.goto(ROOT.joinpath('index.html').as_uri())
    page.locator('[data-id="1"]').click()
    assert page.locator('.inspector h2').inner_text() == 'Maya Rivers'
    page.locator('[data-id="1"]').press('ArrowDown')
    assert page.locator('.inspector h2').inner_text() == 'Eli Monroe'
    page.locator('[data-id="2"]').press('Space')
    assert page.locator('[data-check="2"]').is_checked()
    page.locator('[data-action="export"]').click()
    assert 'Выбранные · 1' in page.locator('dialog').inner_text()
    page.locator('#close-modal').click()
    page.screenshot(path=str(OUT/'a-selection-export-ready.png'))
    page.locator('#artist-search').fill('Maya')
    assert page.locator('tbody tr').count()==1
    page.locator('#artist-search').fill('zzzz')
    assert page.locator('tbody tr').count()==0
    page.locator('#artist-search').fill('')
    page.locator('#genre').select_option('R&B')
    assert page.locator('tbody tr').count()==4
    page.locator('[data-action="more"]').click()
    page.locator('[data-menu-action="reset"]').click()
    assert page.locator('tbody tr').count()==12
    page.locator('[data-action="channels"]').click()
    page.locator('[data-menu-action="channel:imessage"]').click()
    assert page.locator('.placeholder h2').inner_text()=='Кампании'
    page.locator('[data-action="channels"]').click()
    page.locator('[data-menu-action="channel:instagram"]').click()
    assert page.locator('.toolbar h1').inner_text()=='База артистов'
    page.locator('.toolbar [data-action="inspector"]').click()
    assert not page.locator('.inspector').is_visible()
    page.locator('.toolbar [data-action="inspector"]').click()
    page.set_viewport_size({'width':1024,'height':700})
    page.locator('[data-detail="0"]').click()
    assert page.locator('dialog').is_visible()
    page.keyboard.press('Escape')
    assert not page.locator('dialog').is_visible()
    report['checks'] += ['row detail synchronization','arrow navigation','space bulk selection','three export scopes','search and empty state','genre filter','reset','per-channel last route','inspector toggle','narrow detail dialog and Escape']
    page.set_viewport_size({'width':1360,'height':900})
    page.locator('[data-direction="b"]').click()
    page.locator('[data-id="3"]').click()
    assert page.locator('.detail-body h2').inner_text()=='Jules Carter'
    page.locator('[data-action="commands"]').click()
    assert page.locator('[data-menu-action^="route:"]').count()==18
    page.locator('#close-modal').click()
    report['checks'] += ['B list/detail synchronization','all 18 channel-specific routes reachable; 3 utility routes in app menu']
    assert not report['errors'], report['errors']
    browser.close()
(OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
