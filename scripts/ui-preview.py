"""Screenshot every page of the frontend against mocked core responses.

Usage: .venv/Scripts/python scripts/ui-preview.py OUT_DIR [WIDTH HEIGHT] [PAGE_LABEL ...]
Runs the Vite dev server, replaces window.__TAURI_INTERNALS__ with fixtures,
and saves one PNG per navigation entry (plus full-page variants).
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 5199

SOURCES = [
    "rapremiere", "rapgoat.tv", "topdailyrap", "carolinarap_news", "raphitsusa", "rapczn",
    "viralraps", "femalerap.tv", "hybridtrapmusic", "rapsmedia.tv", "rrapstarrs", "raphouse_tv_",
    "raphiphopplug.tv", "rapper_star_ig", "rapontharize", "rapstreamz", "stlrapvideos",
    "rapmedia__tv", "rapupdatestv", "rapstarplug",
]
PROFILES = [
    {"id": "a" * 32, "name": "Dmitrii", "cookie_count": 14,
     "proxy": {"scheme": "socks5", "host": "185.22.14.9", "port": 1080, "has_password": True}},
    {"id": "b" * 32, "name": "Dima2", "cookie_count": 9, "proxy": None},
    {"id": "c" * 32, "name": "dima3", "cookie_count": 0, "proxy": None},
]


def run(status, **extra):
    base = {"id": 7, "scout": True, "kind": "profile", "status": status, "stage": "scout_reading",
            "cursor": 41, "total": 58, "url": "https://www.instagram.com/lil_nova/", "error": None,
            "candidates": 44, "found": 12, "backlog": 36, "notices": [
                "https://www.instagram.com/p/C9x1/: новых кандидатов 30, повторов 4",
                "@beatstore_x: недостаточно признаков исполнителя."],
            "wait_seconds": 0, "wait_reason": None, "profile_id": "a" * 32}
    base.update(extra)
    return base


ACCOUNTS = [
    {"profile": PROFILES[0], "target": 100, "found": 37,
     "run": run("running", wait_seconds=12.4, wait_reason="Пауза между страницами.")},
    {"profile": PROFILES[1], "target": 30, "found": 8,
     "run": run("paused", error="Требуется вход или подтверждение. Проверьте браузер и продолжите очередь.",
                profile_id="b" * 32)},
    {"profile": PROFILES[2], "target": 50, "found": 0, "run": None},
]
LEADS = [
    {"id": i, "username": u, "platform": "instagram", "display_name": n, "bio": b,
     "profile_url": f"https://www.instagram.com/{u}/", "avatar_url": "", "external_url": e,
     "followers": f, "following": 300, "is_private": False, "is_verified": False,
     "artist_probability": 0.9, "primary_genre": g, "genres": [g], "lead_score": s,
     "status": st, "last_activity_at": "2026-09-25T10:00:00+00:00",
     "created_at": "2026-09-27T10:00:00+00:00", "unknown_fields": [],
     "sources": [{"id": i, "source_provider": "instagram_scout", "source_type": "comment",
                  "source_value": f"https://www.instagram.com/p/C{i}x/", "search_job_id": 7}]}
    for i, (u, n, b, e, f, g, s, st) in enumerate([
        ("wallo4vr", "Wallo", "Independent rapper · bookings walloforevermusic@gmail.com",
         "https://open.spotify.com/artist/x", 12400, "Hip-Hop", 92, "new"),
        ("pimpindown", "Pimpin Down", "Rapper from ATL. New single out now", "", 3400, "Trap", 81, "reviewed"),
        ("deals_loudnclear", "Deals", "Artist · producer", "https://linktr.ee/deals", 8800, "Hip-Hop", 74, "new"),
        ("real.sinu", "Sinu", "Singer / songwriter · sinubookings@gmail.com", "", 21000, "R&B", 69, "qualified"),
        ("lil_nova", "Lil Nova", "rapper | new album soon", "", 1900, "Trap", 55, "new"),
    ], start=1)
]
JOBS = [
    {"id": 7, "name": "Лиды из скаут-источников", "status": "running", "stage": "scout_reading",
     "seed_accounts": [f"https://www.instagram.com/{s}/" for s in SOURCES[:3]], "keywords": [],
     "hashtags": [], "genres": [], "min_followers": 1000, "max_followers": 50000, "activity_days": 30,
     "minimum_score": 70, "target_leads": 100, "candidates_found": 44, "profiles_analyzed": 41,
     "artists_detected": 14, "qualified_leads": 12, "created_at": "2026-09-28T09:00:00+00:00",
     "started_at": "2026-09-28T09:00:00+00:00", "completed_at": None, "errors": []},
    {"id": 6, "name": "Лиды из скаут-источников", "status": "completed", "stage": "completed",
     "seed_accounts": [], "keywords": [], "hashtags": [], "genres": [], "min_followers": 1000,
     "max_followers": 50000, "activity_days": 30, "minimum_score": 70, "target_leads": 30,
     "candidates_found": 120, "profiles_analyzed": 118, "artists_detected": 40, "qualified_leads": 30,
     "created_at": "2026-09-27T09:00:00+00:00", "started_at": "2026-09-27T09:00:00+00:00",
     "completed_at": "2026-09-27T11:00:00+00:00", "errors": []},
]
SERVICES = {
    "beats": {"score": 90, "reasons": [{"text": "Просит биты в комментарии", "score": 60, "evidence": {
        "source": "https://www.instagram.com/rapgoat.tv/", "url": "https://www.instagram.com/p/C1x/",
        "caption": "I need beats for my next tape, dm me", "published_at": "2026-09-26T00:00:00Z"}}]},
    "mixing": {"score": 40, "reasons": [{"text": "Скоро релиз", "score": 20, "evidence": None}]},
    "promotion": {"score": 65, "reasons": [{"text": "Небольшая аудитория", "score": 30, "evidence": None}]},
}
RESULTS = [
    {"id": lead["id"], "username": lead["username"], "name": lead["display_name"], "status": lead["status"],
     "priority": 90 - lead["id"] * 5, "contacts": ["walloforevermusic@gmail.com"] if lead["id"] == 1 else [],
     "explanation": "Исполнитель: в биографии указано «rapper», комментарии под публикациями источников.",
     "services": SERVICES, "evidence": [SERVICES["beats"]["reasons"][0]["evidence"]]}
    for lead in LEADS[:3]
]
SETTINGS = {"min_followers": 1000, "max_followers": 50000, "activity_days": 30, "minimum_score": 70,
            "target_leads": 500, "enabled_providers": ["mock"],
            "weights": {"artist": 30, "music_bio": 15, "music_link": 15, "recent_activity": 15,
                        "followers": 10, "genre": 10, "strong_activity": 5},
            "page_delay_min": 8, "page_delay_max": 20, "profiles_per_hour": 60,
            "profiles_per_run": 0, "rate_limit_pause_minutes": 30}
CORE = {
    "system.info": {"version": "0.1.0", "data_dir": "C:/data", "log_dir": "C:/data/logs", "transport": "stdio"},
    "scout.sources": [f"https://www.instagram.com/{s}/" for s in SOURCES],
    "scout.accounts": ACCOUNTS,
    "scout.results": RESULTS,
    "capture.latest": None,
    "leads.list": {"total": 1606, "items": LEADS},
    "leads.detail": {**LEADS[0], "scout": {"explanation": RESULTS[0]["explanation"], "services": SERVICES},
                     "analysis": None, "breakdown": [{"rule": "artist", "reason": "rapper в биографии", "points": 30}]},
    "jobs.list": JOBS,
    "dashboard.get": {"total": 1606, "qualified": 141, "today": 23, "active": 1,
                      "genres": [{"name": "Hip-Hop", "count": 820}, {"name": "Trap", "count": 410},
                                 {"name": "R&B", "count": 160}],
                      "distribution": [300, 700, 450, 156], "jobs": JOBS, "leads": LEADS},
    "settings.get": SETTINGS,
    "providers.health": [{"provider": "mock", "status": "Healthy", "last_error": None},
                         {"provider": "imported", "status": "Unavailable", "last_error": None},
                         {"provider": "meta_instagram", "status": "Unavailable", "last_error": None}],
}
MOCK = """
(() => {
  const core = %s;
  const profiles = %s;
  let next = 1;
  window.__TAURI_INTERNALS__ = {
    transformCallback: () => next++,
    unregisterCallback: () => {},
    convertFileSrc: path => path,
    metadata: { currentWindow: { label: 'main' }, currentWebview: { label: 'main' } },
    invoke: async (cmd, args) => {
      if (cmd === 'core_request') {
        if (args.method in core) return structuredClone(core[args.method]);
        return { ok: true };
      }
      if (cmd === 'browser_action') return args.action === 'list' ? profiles : { ok: true };
      return null;
    },
  };
})();
""" % (json.dumps(CORE, ensure_ascii=False), json.dumps(PROFILES, ensure_ascii=False))


def wait_port(port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise RuntimeError("Vite did not start")


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    width, height = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1280, 800)
    only = sys.argv[4:]
    vite = subprocess.Popen(
        ["npx.cmd", "vite", "--host", "127.0.0.1", "--port", str(PORT), "--strictPort"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_port(PORT)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel="chromium")
            page = browser.new_page(viewport={"width": width, "height": height}, color_scheme="dark")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(MOCK)
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_timeout(800)
            labels = page.eval_on_selector_all(
                "nav button", "els => els.map(e => e.innerText.split('\\n')[0].trim())")
            print("nav:", labels)
            for index, label in enumerate(labels):
                if only and label not in only:
                    continue
                page.locator("nav button").nth(index).click()
                page.wait_for_timeout(700)
                slug = f"{index:02d}"
                page.eval_on_selector(".main", "el => el.scrollTo(0, 0)")
                page.wait_for_timeout(200)
                page.screenshot(path=str(out / f"{slug}-view.png"))
                # The app scrolls inside .main, so capture the rest screen by screen.
                for part in range(1, 5):
                    moved = page.eval_on_selector(
                        ".main",
                        "el => { const y = el.scrollTop; el.scrollBy(0, el.clientHeight - 80);"
                        " return el.scrollTop - y; }",
                    )
                    if moved <= 0:
                        break
                    page.wait_for_timeout(250)
                    page.screenshot(path=str(out / f"{slug}-part{part}.png"))
            if not only:
                # Interaction states: bulk source input and the lead drawer.
                page.locator("nav button").nth(0).click()
                page.wait_for_timeout(500)
                page.eval_on_selector(".main", "el => el.scrollTo(0, 0)")
                page.locator("text=Массовый").first.click(force=True)
                page.wait_for_timeout(300)
                page.screenshot(path=str(out / "state-bulk.png"))
                page.locator("nav button", has_text="База артистов").click()
                page.wait_for_timeout(600)
                page.locator(".profile-cell").first.click()
                page.wait_for_timeout(600)
                page.screenshot(path=str(out / "state-drawer.png"))
            print("errors:", errors or "none")
            browser.close()
    finally:
        subprocess.run(["taskkill", "/PID", str(vite.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    main()
