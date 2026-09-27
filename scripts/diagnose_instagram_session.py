"""Print bounded, non-secret diagnostics for one saved Instagram session."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from playwright.sync_api import sync_playwright  # noqa: E402

from artist_lead_finder.browser_sessions import BrowserSessions  # noqa: E402
from artist_lead_finder.database import application_data_dir  # noqa: E402


def main() -> None:
    profile_id = sys.argv[1]
    url = sys.argv[2]
    record = BrowserSessions(application_data_dir()).read(profile_id)
    print("cookie_names:", sorted(cookie["name"] for cookie in record["cookies"]))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        )
        try:
            context = browser.new_context()
            cookies = []
            for row in record["cookies"]:
                cookie = {key: row[key] for key in ("name", "value", "domain", "path")}
                cookie["secure"] = row.get("secure", True)
                cookie["httpOnly"] = row.get("httpOnly", False)
                cookie["sameSite"] = {"strict": "Strict", "none": "None", "no_restriction": "None"}.get(
                    str(row.get("sameSite") or "Lax").lower(), "Lax"
                )
                if row.get("expires", -1) > 0:
                    cookie["expires"] = row["expires"]
                cookies.append(cookie)
            context.add_cookies(cookies)
            page = context.new_page()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(5000)
                print("http_status:", response.status if response else None)
                print("final_url:", page.url)
                print("title:", page.title()[:120])
                print("state:", page.evaluate("document.readyState"))
                print("og_title_present:", page.locator('meta[property="og:title"]').count() > 0)
                scout_script = (Path(__file__).resolve().parents[1] / "src-tauri" / "src" / "scout.js").read_text(encoding="utf-8")
                snapshot = page.evaluate(scout_script)
                print("scout_ready:", snapshot.get("ready"))
                print("scout_blocked:", snapshot.get("blocked"))
                print("scout_post_count:", len(snapshot.get("posts", [])))
                print("scout_author:", snapshot.get("author", ""))
                print("scout_caption_present:", bool(snapshot.get("caption", "").strip()))
                if "/p/" in url or "/reel/" in url:
                    description = page.locator('meta[property="og:description"]').get_attribute("content") or ""
                    print("description_length:", len(description))
                    print("description_prefix:", ascii(description[:350]))
                    print("description_suffix:", ascii(description[-150:]))
                    print("article_h1_count:", page.locator("article h1").count())
                    print("article_header_links:", page.locator("article header a[href]").evaluate_all(
                        "elements => elements.slice(0, 3).map(element => new URL(element.href).pathname)"
                    ))
                print("post_links:", page.locator('main a[href*="/p/"], main a[href*="/reel/"]').count())
                print("sample_paths:", page.locator('main a[href*="/p/"], main a[href*="/reel/"]').evaluate_all(
                    "elements => elements.slice(0, 3).map(element => new URL(element.href).pathname)"
                ))
                print("body_length:", len(page.locator("body").inner_text()))
            except Exception as exc:
                print("navigation_error_type:", type(exc).__name__)
                print("final_url:", page.url)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
