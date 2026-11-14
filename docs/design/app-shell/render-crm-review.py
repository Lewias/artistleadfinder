"""Render and verify the isolated CRM exploration; no RPC or external requests."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'previews'
os.environ['PLAYWRIGHT_BROWSERS_PATH']='0'
report={'renders':[],'checks':[],'errors':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chromium')
    page=browser.new_page(viewport={'width':1360,'height':900},device_scale_factor=1)
    page.on('pageerror',lambda e:report['errors'].append(str(e)))
    page.on('request',lambda r:report['errors'].append('Unexpected remote request: '+r.url) if r.url.startswith(('http:','https:')) else None)
    for direction,theme,platform,w,h in [
        ('a','dark','windows',1360,900),('b','dark','windows',1360,900),
        ('a','light','windows',1360,900),('b','light','windows',1360,900),
        ('a','dark','windows',1024,700),('b','dark','windows',1024,700),
        ('a','dark','windows',1920,1080),('b','dark','windows',1920,1080),
        ('a','light','mac',1360,900),('b','dark','mac',1360,900),
    ]:
        page.set_viewport_size({'width':w,'height':h})
        page.goto(ROOT.joinpath('index.html').as_uri()+f'?page=crm&direction={direction}&theme={theme}&platform={platform}')
        assert page.locator('.crm-content,.crm-focus').count()==1
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'Document overflow'
        name=f'crm-{direction}-{theme}-{platform}-{w}x{h}'
        page.screenshot(path=str(OUT/(name+'.png')))
        report['renders'].append(name)
    page.set_viewport_size({'width':1360,'height':900})
    page.goto(ROOT.joinpath('index.html').as_uri()+'?page=crm&theme=dark')
    assert page.locator('tbody tr').count()==8
    page.locator('[data-crm-row="0"]').click()
    assert page.locator('.crm-inspector h2').inner_text()=='Noah Bennett'
    page.locator('[data-crm-row="0"]').press('ArrowDown')
    assert page.locator('.crm-inspector h2').inner_text()=='Oliver Lane'
    page.locator('[data-crm-row="6"]').press('Space')
    assert page.locator('[data-crm-check="6"]').is_checked()
    page.locator('#crm-search').fill('Maya')
    assert page.locator('tbody tr').count()==1
    assert page.locator('.crm-inspector h2').inner_text()=='Maya Rivers'
    page.locator('#crm-search').fill('zzzz')
    assert page.locator('tbody tr').count()==0
    page.locator('#crm-search').fill('')
    page.locator('#crm-status-filter').select_option('Клиент')
    assert page.locator('tbody tr').count()==2
    page.locator('#crm-status-filter').select_option('')
    page.locator('[data-crm-tab="attention"]').click()
    assert page.locator('tbody tr').count()==3
    page.locator('[data-crm-tab="trash"]').click()
    assert page.locator('tbody tr').count()==1
    page.locator('[data-crm-action="restore"]').click()
    assert page.locator('tbody tr').count()==0
    page.locator('[data-crm-tab="all"]').click()
    assert page.locator('tbody tr').count()==9
    page.locator('[data-crm-action="new"]').click()
    page.locator('[name="name"]').fill('Demo Contact')
    page.locator('[name="email"]').fill('demo@example.com')
    page.locator('[name="notes"]').fill('Local design review only')
    page.locator('#crm-edit-form button[type="submit"]').click()
    assert page.locator('tbody tr').count()==10
    assert page.locator('.crm-inspector h2').inner_text()=='Demo Contact'
    page.locator('.crm-inspector [data-crm-action="contact-menu"]').click()
    page.locator('[data-menu-action="crm:trash"]').click()
    assert page.locator('tbody tr').count()==9
    page.locator('[data-direction="b"]').click()
    page.locator('[data-crm-row="1"]').click()
    assert page.locator('.detail-body h2').inner_text()=='Maya Rivers'
    page.locator('[data-crm-row="1"]').press('Enter')
    assert page.locator('#crm-edit-form').is_visible()
    page.keyboard.press('Escape')
    page.locator('[data-channel="email"]').click()
    page.locator('[data-route="email-crm"]').click()
    assert page.locator('.crm-focus').is_visible()
    assert page.locator('button[data-crm-action="write"]').inner_text()=='Переписка'
    assert page.locator('.detail-body h2').inner_text()=='Maya Rivers'
    report['checks']=['CRM tab reachable in both shells','row-detail sync','arrow navigation and Space selection','search, empty state and selection reset','status filter','attention tab','trash and local restore','local new contact form','local trash action','B detail selection and Enter edit','Email CRM conversation action','separate channel demo books']
    assert not report['errors'],report['errors']
    browser.close()
(OUT/'crm-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
