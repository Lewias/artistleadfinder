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
            "wait_seconds": 0, "wait_reason": None, "profile_id": "a" * 32,
            "stats": {"discovered": 47, "resolved": 39, "classified": 36, "analyzed": 39, "leads": 12,
                      "leads_updated": 3, "skipped": 24, "errors": 1,
                      "skips": {"WRONG_PROFILE_TYPE": 11, "FOLLOWERS_TOO_LOW": 6, "NO_CONTACT": 4,
                                "ALREADY_PROCESSED": 3, "DUPLICATE_LEAD": 3},
                      "current_source": "https://www.instagram.com/rapgoat.tv/",
                      "current_profile": "lil_nova",
                      "sources": [f"https://www.instagram.com/{n}/" for n in range(10)],
                      "sources_done": [f"https://www.instagram.com/{n}/" for n in range(3)],
                      "providers": {
                          "rapgoat.tv": {
                              "posts": {"itemsSeen": 42, "itemsProcessed": 17, "candidatesFound": 9,
                                        "duplicatesSkipped": 3, "alreadyProcessed": 11, "failures": 1},
                              "tagged": {"itemsSeen": 12, "itemsProcessed": 8, "candidatesFound": 6,
                                         "duplicatesSkipped": 1, "alreadyProcessed": 2, "failures": 0},
                              "stories": {"itemsSeen": 5, "itemsProcessed": 5, "candidatesFound": 3,
                                          "duplicatesSkipped": 0, "alreadyProcessed": 0, "failures": 0}}}}}
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
                  "source_value": f"https://www.instagram.com/p/C{i}x/", "search_job_id": 7}],
     "scout_profile": {"profile_type": "producer" if i == 3 else "artist", "confidence": 94 - i * 3,
                       "emails": ["walloforevermusic@gmail.com"] if i == 1 else [],
                       "phones": ["+14045550199"] if i == 2 else [],
                       "source_username": "rapgoat.tv", "discovery_method": "comment",
                       "last_seen_at": "2026-09-28T10:00:00+00:00"}}
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
            "profiles_per_run": 0, "rate_limit_pause_minutes": 10,
            "scout_methods": ["posts", "tagged"], "scout_profile_type": "artists",
            "scout_min_followers": 0, "scout_max_followers": 1000000, "scout_only_contacts": False,
            "scout_allow_unknown_followers": True, "scout_min_confidence": 0, "scout_lead_status": "new",
            "scout_skip_processed": True, "scout_skip_recent_sources": True,
            "scout_source_cooldown_hours": 24, "scout_sources_per_run": 5, "scout_follow_page_size": 12,
            "scout_follow_delay_seconds": 2, "scout_followers_max": 100, "scout_following_max": 100,
            "scout_use_followers_range": True, "scout_rotate_sources": True,
            "scout_add_to_outreach": True, "scout_ai_mode": "uncertain",
            "scout_ai_model": "anthropic/claude-haiku-4.5", "scout_ai_timeout_seconds": 15,
            "scout_ai_concurrency": 2, "scout_ai_min_confidence": 55, "scout_max_posts_per_source": 120,
            "scout_max_scroll_rounds": 15, "scout_scroll_delay_ms": 1200, "scout_max_no_progress_rounds": 2,
            "scout_max_retries": 2, "scout_max_item_failures": 3,
            "scout_debug": False, "scout_ignore_usernames": [],
            "scout_profile_api": True, "scout_profile_cache_hours": 12,
            "scout_recent_captions": 3}
LOG = """[Scout][@rapgoat.tv][@lil_nova]

method: comment

Resolved:
followers 1900
email yes
phone no

Classification:
artist 89% (local)

Filters:
source account: pass
ignored username: pass
already processed: pass
duplicate lead: pass
required data: pass
followers: pass (1900)
profile type: pass (artist)
confidence: pass (89%)
contacts: pass (email)

RESULT:
LEAD CREATED"""
EVENTS = [
    {"id": i, "job_id": 7, "type": t, "payload": pl, "created_at": f"2026-09-28T10:{i:02d}:00+00:00"}
    for i, (t, pl) in enumerate([
        ("scout:run-started", {"sources": ["rapgoat.tv", "topdailyrap"]}),
        ("scout:source-started", {"source": "rapgoat.tv"}),
        ("scout:candidate-found", {"username": "lil_nova", "method": "comment", "source": "rapgoat.tv"}),
        ("scout:profile-resolving", {"username": "lil_nova", "source": "rapgoat.tv"}),
        ("scout:profile-resolved", {"username": "lil_nova", "source": "rapgoat.tv", "followers": 1900}),
        ("scout:classification-completed", {"username": "lil_nova", "category": "artist", "confidence": 89}),
        ("scout:lead-created", {"username": "lil_nova", "source": "rapgoat.tv", "category": "artist",
                                "confidence": 89, "log": LOG}),
        ("scout:profile-skipped", {"username": "johnbeats", "reason": "WRONG_PROFILE_TYPE",
                                   "category": "producer", "confidence": 91,
                                   "log": LOG.replace("lil_nova", "johnbeats").replace(
                                       "LEAD CREATED", "SKIPPED\n\nReason:\nWRONG_PROFILE_TYPE")}),
        ("scout:profile-skipped", {"username": "randomshop", "reason": "WRONG_PROFILE_TYPE",
                                   "category": "other", "confidence": 98}),
        ("scout:profile-skipped", {"username": "artistx", "reason": "FOLLOWERS_TOO_LOW",
                                   "details": "245 < min 500", "category": "artist", "confidence": 88}),
        ("scout:lead-updated", {"username": "wallo4vr", "source": "topdailyrap", "change": "new source",
                                "category": "artist", "confidence": 92}),
        ("scout:error", {"reason": "RATE_LIMITED", "kind": "rate_limit", "profile": "some_user"}),
        ("scout:discovery-page", {"source": "rapgoat.tv", "method": "posts", "log": "\n".join([
            "[Scout][Posts][@rapgoat.tv]", "Opening reel CxABC123", "Post author: @artist123 (via metadata)",
            "Collaborators: @artist456", "Candidates emitted: @artist123, @artist456"])}),
    ], start=1)
]
CORE = {
    "system.info": {"version": "0.1.0", "data_dir": "C:/data", "log_dir": "C:/data/logs", "transport": "stdio"},
    "scout.sources": [f"https://www.instagram.com/{s}/" for s in SOURCES],
    "scout.accounts": ACCOUNTS,
    "scout.source_list": [
        {"url": f"https://www.instagram.com/{s}/", "username": s, "enabled": i != 3,
         "last_scanned_at": None if i > 4 else "2026-09-28T08:00:00+00:00",
         "status": ["done", "scanning", "queued", "new", "rate_limited"][i % 5], "leads_found": (i * 7) % 23,
         "candidates_found": (i * 13) % 71, "profiles_resolved": (i * 11) % 60,
         "profiles_skipped": (i * 5) % 37, "errors_count": i % 3}
        for i, s in enumerate(SOURCES[:9])
    ],
    "scout.events": EVENTS,
    "ai.status": {"configured": False},
    "scout.results": RESULTS,
    "capture.latest": None,
    "leads.list": {"total": 1606, "items": LEADS},
    "leads.detail": {**LEADS[0], "scout": {"explanation": RESULTS[0]["explanation"], "services": SERVICES},
                     "classification": {"category": "artist", "confidence": 94, "decided_by": "local",
                                        "reasons": ["Bio contains strong artist term: rapper",
                                                    "Spotify link found"], "ai_model": None,
                                        "local_category": "artist", "local_confidence": 94},
                     "found_via": [
                         {"source_username": "rapgoat.tv", "discovery_method": "post", "origin_url": None,
                          "first_seen_at": "2026-09-20T10:00:00+00:00",
                          "last_seen_at": "2026-09-27T10:00:00+00:00", "times_seen": 2},
                         {"source_username": "hiphopdaily", "discovery_method": "tagged", "origin_url": None,
                          "first_seen_at": "2026-09-28T09:00:00+00:00",
                          "last_seen_at": "2026-09-28T09:00:00+00:00", "times_seen": 1}],
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
SENDER_ROWS = [
    {"id": "a" * 32, "name": "Рабочий Instagram", "has_session": True, "open": True, "status": "active",
     "reason": None, "until": None, "last_sent_at": "2026-09-29T09:40:00+00:00", "sent_24h": 12,
     "daily_limit": 20},
    {"id": "b" * 32, "name": "Второй аккаунт", "has_session": True, "open": False, "status": "rate_limited",
     "reason": "Instagram ограничил действия аккаунта", "until": "2026-09-29T15:40:00+00:00",
     "last_sent_at": "2026-09-29T09:10:00+00:00", "sent_24h": 19, "daily_limit": 20},
]
TEMPLATE = {"id": 1, "name": "Intro", "enabled": True, "created_at": "2026-09-28T10:00:00+00:00",
            "updated_at": "2026-09-28T10:00:00+00:00",
            "body": "Hey {{firstName}}, checked your music out — wanted to reach out real quick."}
CAMPAIGN = {"id": 3, "name": "Artist Outreach September", "status": "running",
            "created_at": "2026-09-29T08:00:00+00:00", "started_at": "2026-09-29T08:05:00+00:00",
            "finished_at": None, "scheduled_at": None, "template_id": 1, "followup_sequence_id": 1,
            "sender_strategy": "round_robin", "sender_ids": ["a" * 32, "b" * 32], "total_recipients": 127,
            "queued_count": 82, "sent_count": 31, "skipped_count": 9, "failed_count": 5, "replied_count": 3}
PREVIEW = {"total": 127, "eligible": 118, "skipped": {"ALREADY_CONTACTED": 6, "DO_NOT_CONTACT": 3},
           "invalid_messages": 0, "accounts": 2, "active_accounts": 1, "estimated_queued": 118,
           "examples": [{"lead_id": i, "username": u, "display_name": n, "sender_id": "a" * 32,
                         "sender_name": "Рабочий Instagram", "fallbacks": [] if n else ["firstName"],
                         "message": f"Hey {n.split()[0] if n else 'there'}, checked your music out — "
                                    "wanted to reach out real quick."}
                        for i, (u, n) in enumerate([("artistone", "Jay Carter"), ("lil_nova", "Nova"),
                                                    ("wallo4vr", "")], start=1)]}
RECIPIENTS = {"total": 5, "items": [
    {"id": i, "campaign_id": 3, "lead_id": i, "username": u, "display_name": n, "followers": f,
     "profile_type": "artist", "sender_account_id": "a" * 32, "sender_name": "Рабочий Instagram",
     "status": st, "skip_reason": sk, "failure_reason": fr, "reason_details": d,
     "needs_review": fr == "SEND_ERROR", "queued_at": "2026-09-29T08:05:00+00:00",
     "sent_at": "2026-09-29T09:40:00+00:00" if st == "sent" else None, "failed_at": None,
     "replied_at": "2026-09-29T10:10:00+00:00" if i == 4 else None,
     "rendered_message": "Hey Jay, checked your music out — wanted to reach out real quick." if st != "skipped"
     else None}
    for i, (u, n, f, st, sk, fr, d) in enumerate([
        ("artistone", "Jay Carter", 12500, "sent", None, None, None),
        ("artisttwo", "Kim", 3400, "skipped", "ALREADY_CONTACTED", None, "Лиду уже писали раньше"),
        ("artistthree", "Tre", 2100, "failed", None, "SENDER_UNAVAILABLE", "Окно браузера аккаунта закрыто"),
        ("artistfour", "Four", 8800, "sent", None, None, None),
        ("artistfive", "Five", 1900, "failed", None, "SEND_ERROR", "Нет ответа Instagram — отправка не подтверждена"),
    ], start=1)]}
OUTREACH_EVENTS = [
    {"id": i, "campaign_id": 3, "type": t, "payload": pl, "created_at": f"2026-09-29T10:{i:02d}:00+00:00"}
    for i, (t, pl) in enumerate([
        ("recipient:sent", {"username": "artistone"}),
        ("recipient:skipped", {"username": "artisttwo", "reason": "ALREADY_CONTACTED"}),
        ("recipient:failed", {"username": "artistthree", "reason": "SENDER_UNAVAILABLE"}),
        ("recipient:replied", {"username": "artistfour"}),
        ("sender:unavailable", {"sender_id": "b" * 32, "reason": "RATE_LIMITED",
                                "details": "Instagram ограничил действия аккаунта"}),
    ], start=1)
]
CORE.update({
    "outreach.campaigns": [CAMPAIGN, {**CAMPAIGN, "id": 2, "name": "Producers test", "status": "draft",
                                      "sent_count": 0, "queued_count": 0, "total_recipients": 40,
                                      "skipped_count": 0, "failed_count": 0, "replied_count": 0}],
    "outreach.campaign": {**CAMPAIGN, "template": TEMPLATE,
                          "sequence": {"id": 1, "name": "Two bumps", "enabled": True,
                                       "steps": [{"delay_days": 3, "template_id": 1}]},
                          "senders": [{k: r[k] for k in ("id", "name", "status", "reason", "until", "sent_24h",
                                                         "daily_limit")} for r in SENDER_ROWS],
                          "reasons": {"ALREADY_CONTACTED": 6, "DO_NOT_CONTACT": 3, "SENDER_UNAVAILABLE": 4,
                                      "SEND_ERROR": 1}},
    "outreach.recipients": RECIPIENTS,
    "outreach.events": OUTREACH_EVENTS,
    "outreach.senders": SENDER_ROWS,
    "outreach.templates": [TEMPLATE],
    "outreach.sequences": [{"id": 1, "name": "Two bumps", "enabled": True,
                            "steps": [{"delay_days": 3, "template_id": 1}, {"delay_days": 4, "template_id": 1}]}],
    "outreach.template_render": {"text": "Hey Jay, checked your music out — wanted to reach out real quick.",
                                 "valid": True, "errors": [], "fallbacks": [], "length": 66},
    "outreach.preview": PREVIEW,
    "outreach.audience": {"total": len(LEADS), "ids": [lead["id"] for lead in LEADS], "items": [
        {"id": lead["id"], "username": lead["username"], "display_name": lead["display_name"],
         "followers": lead["followers"], "status": lead["status"], "do_not_contact": False,
         "contacted": False, "created_at": lead["created_at"], "profile_type": "artist", "confidence": 88,
         "email": "mgmt@mail.com", "phone": None, "source_username": "rapgoat.tv",
         "discovery_method": "post"} for lead in LEADS]},
})
CORE["outreach.workspace"] = {"usernames": [{"username": "getabag.bo", "status": "sent", "reason": None, "details": None}, {"username": "shotbyjae_", "status": "sent", "reason": None, "details": None}, {"username": "1sttake_pro", "status": "sent", "reason": None, "details": None}, {"username": "_capturedbydanny", "status": "queued", "reason": None, "details": None}, {"username": "vezolotti", "status": "queued", "reason": None, "details": None}, {"username": "kukookklan", "status": "skipped", "reason": "ALREADY_CONTACTED", "details": None}, {"username": "1knownoah", "status": "failed", "reason": "SEND_ERROR", "details": None}, {"username": "tmill_music", "status": "new", "reason": None, "details": None}, {"username": "bigupbigup_", "status": "new", "reason": None, "details": None}, {"username": "un1kemee", "status": "new", "reason": None, "details": None}, {"username": "nolani.visuals", "status": "new", "reason": None, "details": None}, {"username": "etxrnixx", "status": "new", "reason": None, "details": None}, {"username": "i_stormo", "status": "new", "reason": None, "details": None}, {"username": "foedeucee_", "status": "new", "reason": None, "details": None}, {"username": "xprime_16", "status": "new", "reason": None, "details": None}, {"username": "issawave24", "status": "new", "reason": None, "details": None}, {"username": "dimi_guss", "status": "new", "reason": None, "details": None}, {"username": "davoiceofdastreetz", "status": "new", "reason": None, "details": None}, {"username": "tymaxxtheopp", "status": "new", "reason": None, "details": None}, {"username": "reddishyellow_", "status": "new", "reason": None, "details": None}, {"username": "shooterzmuzik", "status": "new", "reason": None, "details": None}, {"username": "esco6_ar", "status": "new", "reason": None, "details": None}, {"username": "watchthislouey", "status": "new", "reason": None, "details": None}, {"username": "boxboyzceo", "status": "new", "reason": None, "details": None}, {"username": "urhiness77", "status": "new", "reason": None, "details": None}, {"username": "countrycody_", "status": "new", "reason": None, "details": None}, {"username": "mal216", "status": "new", "reason": None, "details": None}, {"username": "thedriver1800", "status": "new", "reason": None, "details": None}, {"username": "ryanvital_official", "status": "new", "reason": None, "details": None}, {"username": "csonpzz", "status": "new", "reason": None, "details": None}, {"username": "elevatedexplorer05", "status": "new", "reason": None, "details": None}, {"username": "vvsvinskii", "status": "new", "reason": None, "details": None}, {"username": "347marz", "status": "new", "reason": None, "details": None}, {"username": "makdesigns_mk", "status": "new", "reason": None, "details": None}, {"username": "hiphopfienz", "status": "new", "reason": None, "details": None}, {"username": "magik_mic", "status": "new", "reason": None, "details": None}, {"username": "tyriehames", "status": "new", "reason": None, "details": None}, {"username": "johnnydane4eg", "status": "new", "reason": None, "details": None}, {"username": "mn_standupofficial", "status": "new", "reason": None, "details": None}, {"username": "passdatea", "status": "new", "reason": None, "details": None}, {"username": "victoria_laurey", "status": "new", "reason": None, "details": None}, {"username": "lil.karma76o", "status": "new", "reason": None, "details": None}, {"username": "purp_art2.0", "status": "new", "reason": None, "details": None}, {"username": "mrjayjonesjrent", "status": "new", "reason": None, "details": None}, {"username": "justbizness4k", "status": "new", "reason": None, "details": None}, {"username": "wearecrave", "status": "new", "reason": None, "details": None}, {"username": "jilani070", "status": "new", "reason": None, "details": None}, {"username": "djdrez_", "status": "new", "reason": None, "details": None}, {"username": "toomuchmoneycd", "status": "new", "reason": None, "details": None}], "messages": ["Yo bro I was scrolling and your sound actually caught me fr I fw it heavy lets work Drop me your # and lets cook", "Yo fam fw ur sound heavy, got some beats for u. Whats ur #", "Damn bro your vibe is dope asf I really like what you doing We should cook something whats your # br", "Yo gang lets work whats ur #?", "Ay I randomly ran into your music and it hit different you got that real shit Lets collab, send me your # br", "Fw ur music bro, tryna send u some beats. Whats the best way to reach u?", "Yo your flow crazy no cap I been listening and I think we can make some fire together Hit me with your #", "Yo bro got some heat for u, whats ur #?", "Been bumpin ur shit, got beats that fit ur sound. Whats ur #?"], "sender_ids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "campaign": {"id": 3, "name": "Первичная рассылка", "status": "running", "created_at": "2026-09-29T08:00:00+00:00", "started_at": "2026-09-29T08:05:00+00:00", "finished_at": None, "scheduled_at": None, "template_id": 1, "followup_sequence_id": None, "sender_strategy": "single", "sender_ids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "total_recipients": 49, "queued_count": 42, "sent_count": 3, "skipped_count": 1, "failed_count": 1, "replied_count": 0}, "running": True}
CORE["leads.detail"]["outreach"] = {
    "do_not_contact": False, "contacted": True, "last_contacted_at": "2026-09-29T09:40:00+00:00",
    "sender_name": "Рабочий Instagram", "conversation_status": "waiting_reply",
    "campaign": {"id": 3, "name": "Artist Outreach September", "status": "sent", "reason": None,
                 "needs_review": False},
    "pending_followups": 1,
    "messages": [{"direction": "outbound", "type": "initial", "sent_at": "2026-09-29T09:40:00+00:00",
                  "body": "Hey Jay, checked your music out — wanted to reach out real quick."}],
}
# iMessage: state of a running campaign taken from the real service (loopback, no sends).
CORE.update(json.loads((ROOT / "scripts" / "ui-preview-imessage.json").read_text(encoding="utf-8")))
# CRM: four contacts saved through the real CrmService (scratch database).
CORE.update(json.loads((ROOT / "scripts" / "ui-preview-crm.json").read_text(encoding="utf-8")))
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
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle", timeout=90000)
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
                if os.environ.get("ALF_PREVIEW_OPEN_DETAILS"):
                    # Show collapsed sections (settings, metrics) in the screenshots.
                    page.eval_on_selector_all("details", "els => els.forEach(el => { el.open = true; })")
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
                if page.locator("text=Журнал профилей").count():
                    page.locator("text=Журнал профилей").first.click()
                    page.wait_for_timeout(300)
                    page.locator(".scout-activity").scroll_into_view_if_needed()
                    page.screenshot(path=str(out / "state-log.png"))
                page.locator("nav button", has_text="База артистов").click()
                page.wait_for_timeout(600)
                page.locator(".profile-cell").first.click()
                page.wait_for_timeout(600)
                page.screenshot(path=str(out / "state-drawer.png"))
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                page.locator("nav button", has_text="Первичная рассылка").click()
                page.wait_for_timeout(700)
                page.eval_on_selector(".main", "el => el.scrollTo(0, 0)")
                page.screenshot(path=str(out / "state-outreach.png"))
                page.eval_on_selector(".main", "el => el.scrollTo(0, 10000)")
                page.screenshot(path=str(out / "state-outreach-2.png"))
                page.locator(".outreach-board button", has_text="Настройки").click()
                page.wait_for_timeout(500)
                page.screenshot(path=str(out / "state-outreach-settings.png"))
                page.keyboard.press("Escape")
                page.set_viewport_size({"width": 1100, "height": 900})
                page.wait_for_timeout(400)
                page.screenshot(path=str(out / "state-outreach-narrow.png"), full_page=True)
            print("errors:", errors or "none")
            browser.close()
    finally:
        subprocess.run(["taskkill", "/PID", str(vite.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    main()
