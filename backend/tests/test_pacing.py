from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from artist_lead_finder.database import open_database
from artist_lead_finder.pacing import Pacer, PacingSettings
from artist_lead_finder.service import ApplicationService

START = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
SOURCE = "https://www.instagram.com/music_news/"


class Clock:
    def __init__(self):
        self.value = START

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


def test_page_delay_hourly_cap_and_rate_limit_break():
    clock = Clock()
    pacer = Pacer(now=clock, jitter=lambda low, high: high)
    settings = PacingSettings(page_delay_min=5, page_delay_max=10, profiles_per_hour=2)
    assert pacer.wait(settings, "profile") == (0.0, None)
    pacer.page_done(settings, profile=True)
    assert pacer.wait(settings, "post")[0] == 10
    clock.advance(seconds=10)
    assert pacer.wait(settings, "profile") == (0.0, None)
    pacer.page_done(settings, profile=True)
    clock.advance(seconds=10)
    # Two profiles within the hour: the next profile waits, other pages do not.
    wait, reason = pacer.wait(settings, "profile")
    assert wait == 3600 - 20 and "2 профилей в час" in reason
    assert pacer.wait(settings, "post") == (0.0, None)
    clock.advance(seconds=wait)
    assert pacer.wait(settings, "profile") == (0.0, None)
    pacer.rate_limited(settings)
    wait, reason = pacer.wait(settings, "post")
    assert wait == 30 * 60 and reason.startswith("Instagram ограничил запросы")


def test_pacing_settings_are_validated():
    with pytest.raises(ValidationError):
        PacingSettings(page_delay_min=20, page_delay_max=10)
    with pytest.raises(ValidationError):
        PacingSettings(page_delay_min=1)


def test_run_limit_state_wait_and_rate_limit_via_service(tmp_path):
    engine, sessions = open_database(tmp_path / "pace.db")
    service = ApplicationService(sessions, tmp_path)
    clock = Clock()
    service.scout.pacer_factory = lambda: Pacer(now=clock, jitter=lambda low, high: low)
    saved = service.call("settings.save", {"profiles_per_run": 2, "profiles_per_hour": 0})
    assert saved["page_delay_min"] == 8 and saved["profiles_per_run"] == 2
    with pytest.raises(ValueError):
        service.call("settings.save", {"page_delay_min": 30, "page_delay_max": 10})
    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]

    def commit(snapshot):
        service.call("scout.commit_internal", {"id": job, "snapshot": snapshot})
        clock.advance(seconds=8)

    assert service.call("capture.state", {"id": job})["wait_seconds"] == 0
    service.call(
        "scout.commit_internal",
        {
            "id": job,
            "snapshot": dict(url=SOURCE, ready=True, posts=["https://www.instagram.com/p/a/"]),
        },
    )
    state = service.call("capture.state", {"id": job})
    assert state["wait_seconds"] == 8 and state["wait_reason"] == "Пауза между страницами."
    clock.advance(seconds=8)
    commit(
        dict(
            url="https://www.instagram.com/p/a/",
            ready=True,
            author="music_news",
            comments=[
                dict(profile_url=f"https://www.instagram.com/fan{i}/", text="hi") for i in range(4)
            ],
        )
    )
    for i in range(2):
        commit(
            dict(
                url=f"https://www.instagram.com/fan{i}/",
                ready=True,
                title=f"Fan (@fan{i})",
                header="10 followers",
                description="Just a fan",
            )
        )
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed"
    assert "Достигнут лимит 2 профилей за запуск; не проверено: 2." in state["notices"]

    job = service.call("scout.start_internal", {"sources": [SOURCE], "profile_id": "0" * 32})["id"]
    service.call("capture.error_internal", {"id": job, "reason": "rate_limited"})
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "paused" and "ограничил запросы" in state["error"]
    service.call("jobs.control", {"id": job, "action": "resume"})
    state = service.call("capture.state", {"id": job})
    assert state["wait_seconds"] == 30 * 60
    service.shutdown()
    engine.dispose()
