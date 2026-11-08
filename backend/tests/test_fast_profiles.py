"""Profile JSON from page data, fast profile requests and skips from page data."""

import json

import pytest
from sqlalchemy import select

from artist_lead_finder import profile_requests
from artist_lead_finder.database import open_database
from artist_lead_finder.lead_scout.decision import hint_skip
from artist_lead_finder.lead_scout.profiles import page_data
from artist_lead_finder.lead_scout.settings import ScoutSettings
from artist_lead_finder.models import Lead, LeadScoutProfile, ScoutProcessedProfile
from artist_lead_finder.pacing import Pacer, PacingSettings
from artist_lead_finder.service import ApplicationService

SOURCE = "https://www.instagram.com/rapdaily/"
POST = "https://www.instagram.com/p/P1/"
ACCOUNT = "a" * 32


def web_user(name="artist123", **extra):
    return {
        "id": "4242",
        "username": name,
        "full_name": "Artist One",
        "biography": "Rapper. new single out now",
        "edge_followed_by": {"count": 2612},
        "edge_follow": {"count": 120},
        "edge_owner_to_timeline_media": {"count": 41, "edges": []},
        "business_email": "Booking@Artist.com",
        # Newer accounts: no external_url, the link lives in bio_links.
        "external_url": None,
        "bio_links": [{"url": "https://linktr.ee/artist123"}],
        "is_private": False,
        "profile_pic_url_hd": "https://cdn/x.jpg",
        **extra,
    }


# ---------- page data ----------


def test_the_profile_owner_comes_from_any_response_shape():
    stub = json.dumps({"data": {"items": [{"user": {"username": "artist123", "id": "4242"}}]}})
    full = json.dumps({"data": {"user": web_user()}})
    feed = json.dumps(
        {
            "items": [
                {"code": "C1", "caption": {"text": "new drop"}, "user": {"username": "artist123"}}
            ]
        }
    )
    user = page_data.profile_user(["not json", stub, "for (;;);" + full, feed], "Artist123")
    assert user["biography"] == "Rapper. new single out now"
    assert user["_captions"] == ["new drop"]
    assert "profile_pic_url_hd" not in user
    partial = page_data.partial_from_user(user, 3)
    assert (partial.followers_count, partial.posts_count) == (2612, 41)
    assert partial.emails == ["Booking@Artist.com"]
    assert partial.external_url == "https://linktr.ee/artist123"
    assert page_data.profile_user([stub], "artist123") is None
    assert page_data.profile_user([full], "someone_else") is None


def test_people_on_a_page_keep_only_stated_facts():
    page = json.dumps(
        {
            "users": [
                {"username": "closed_one", "is_private": True, "is_verified": False},
                {"username": "open_one", "is_private": False, "follower_count": 900},
                {"username": "just_a_name"},
            ]
        }
    )
    people = page_data.people([page])
    assert people == {
        "closed_one": {"is_private": True, "is_verified": False},
        "open_one": {"is_private": False, "followers": 900},
    }
    assert page_data.clean_hint({"is_private": "yes", "followers": -1, "x": 1}) is None


def test_hints_skip_only_what_the_profile_check_would_skip():
    settings = ScoutSettings(scout_min_followers=1000, scout_max_followers=50000)
    assert hint_skip({"is_private": True}, settings)[0] == "PROFILE_PRIVATE"
    assert hint_skip({"followers": 10}, settings)[0] == "FOLLOWERS_TOO_LOW"
    assert hint_skip({"followers": 90000}, settings)[0] == "FOLLOWERS_TOO_HIGH"
    assert hint_skip({"followers": 5000, "is_private": False}, settings) is None
    assert hint_skip({"is_verified": True}, settings) is None
    off = ScoutSettings(scout_use_followers_range=False)
    assert hint_skip({"followers": 10}, off) is None


# ---------- request templates ----------

HEADERS = {
    "x-ig-app-id": "936619743392459",
    "x-csrftoken": "tok",
    "x-asbd-id": "129477",
    "cookie": "sessionid=secret",
    "user-agent": "UA",
    "sec-fetch-mode": "cors",
    "referer": "https://www.instagram.com/artist123/",
}


def test_a_profile_query_is_retargeted_to_another_username():
    url = "https://www.instagram.com/api/v1/users/web_profile_info/?username=artist123"
    template = profile_requests.learn(url, "GET", HEADERS, None, "artist123", "4242")
    assert template is not None and template.kind == "page_request"
    # Cookies and browser-computed headers are never kept.
    assert set(template.headers) == {"x-ig-app-id", "x-csrftoken", "x-asbd-id"}
    new_url, body, count = profile_requests.retarget(template, "next.one")
    assert count == 1 and new_url.endswith("?username=next.one") and body is None


def test_a_graphql_form_body_is_retargeted():
    variables = json.dumps({"username": "artist123", "render_surface": "PROFILE"})
    body = f"av=1&fb_dtsg=abc&doc_id=99&variables={profile_requests.quote(variables)}"
    headers = {**HEADERS, "content-type": "application/x-www-form-urlencoded"}
    template = profile_requests.learn(
        "https://www.instagram.com/graphql/query", "POST", headers, body, "artist123", "4242"
    )
    assert template is not None
    _, new_body, count = profile_requests.retarget(template, "next.one")
    assert count == 1
    pairs = dict(profile_requests.parse_qsl(new_body))
    assert json.loads(pairs["variables"])["username"] == "next.one"
    assert pairs["fb_dtsg"] == "abc" and pairs["doc_id"] == "99"


def test_the_profile_query_by_user_id_is_retargeted_with_the_id():
    # The profile page asks for its owner by id: {"id": ..., "render_surface": "PROFILE"}.
    variables = json.dumps({"id": "4242", "render_surface": "PROFILE"})
    body = f"doc_id=99&variables={profile_requests.quote(variables)}"
    template = profile_requests.learn(
        "https://www.instagram.com/graphql/query", "POST", HEADERS, body, "artist123", "4242"
    )
    assert template.kind == "page_query" and template.user_id == "4242"
    url, new_body, count = profile_requests.retarget(template, "next.one", "777")
    pairs = dict(profile_requests.parse_qsl(new_body))
    assert count == 1 and json.loads(pairs["variables"]) == {
        "id": "777",
        "render_surface": "PROFILE",
    }
    # Without the candidate's id it cannot be retargeted.
    assert profile_requests.retarget(template, "next.one")[2] == 0
    # Username and id together, and the id as a JSON number, swap as well.
    both = json.dumps({"username": "artist123", "id": 4242})
    template = profile_requests.learn(
        "https://www.instagram.com/graphql/query",
        "POST",
        HEADERS,
        f"variables={profile_requests.quote(both)}",
        "artist123",
        "4242",
    )
    _, new_body, count = profile_requests.retarget(template, "next.one", "777")
    assert count == 2
    assert json.loads(dict(profile_requests.parse_qsl(new_body))["variables"]) == {
        "username": "next.one",
        "id": 777,
    }
    by_path = "https://www.instagram.com/api/v1/users/4242/info/"
    template = profile_requests.learn(by_path, "GET", HEADERS, None, "artist123", "4242")
    assert profile_requests.retarget(template, "next.one", "777")[0].endswith("/users/777/info/")


def test_requests_that_cannot_be_retargeted_or_off_site_are_not_learned():
    # The id inside a blob that is not a value of its own cannot be swapped cleanly.
    blob = json.dumps({"key": "user_4242_profile", "id": "4242"})
    body = f"variables={profile_requests.quote(blob)}"
    assert (
        profile_requests.learn(
            "https://www.instagram.com/graphql/query", "POST", HEADERS, body, "artist123", "4242"
        )
        is None
    )
    assert (
        profile_requests.learn(
            "https://evil.test/api/v1/x/?username=artist123",
            "GET",
            HEADERS,
            None,
            "artist123",
            None,
        )
        is None
    )
    no_id = "https://www.instagram.com/api/v1/feed/timeline/"
    assert profile_requests.learn(no_id, "GET", HEADERS, None, "artist123", "4242") is None


def test_borrowed_headers_build_web_profile_info():
    template = profile_requests.borrowed(HEADERS, "artist123")
    assert template.kind == "web_profile_info" and "cookie" not in template.headers
    url, _, count = profile_requests.retarget(template, "next.one")
    assert count == 1 and url.endswith("web_profile_info/?username=next.one")
    assert profile_requests.borrowed({"accept": "*/*"}, "artist123") is None
    # A GraphQL request's own headers describe that query and are not carried over.
    graph = {
        **HEADERS,
        "x-fb-friendly-name": "PolarisProfilePageContentQuery",
        "x-root-field-name": "fetch__XDTUserDict",
        "x-fb-lsd": "abc",
    }
    kept = profile_requests.borrowed(graph, "artist123").headers
    assert "x-ig-app-id" in kept and not any(key.startswith("x-fb") for key in kept)
    assert "x-root-field-name" not in kept


def test_a_failed_replay_says_why():
    assert profile_requests.describe({"status": 400, "text": ""}) == "HTTP 400"
    assert profile_requests.describe({"error": "network"}) == "network"
    assert profile_requests.describe(None) == "no answer"


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        ({"status": 200, "path": "/api/v1/x/", "text": "{}"}, "ok"),
        ({"status": 429, "path": "/api/v1/x/", "text": ""}, "limited"),
        ({"status": 200, "path": "/accounts/login/", "text": ""}, "limited"),
        (
            {"status": 400, "path": "/x", "text": '{"message":"Please wait a few minutes"}'},
            "limited",
        ),
        ({"status": 500, "path": "/x", "text": ""}, "failed"),
        ({"error": "timeout"}, "failed"),
    ],
)
def test_replay_outcomes(answer, verdict):
    assert profile_requests.outcome(answer) == verdict


def test_a_fast_request_waits_a_shorter_pause():
    pacer = Pacer(jitter=lambda low, high: high)
    settings = PacingSettings(page_delay_min=4, page_delay_max=10)
    pacer.page_done(settings, profile=True, fast=True)
    assert pacer.wait(settings, "profile")[0] == pytest.approx(4.0, abs=0.5)
    pacer.page_done(settings, profile=True)
    assert pacer.wait(settings, "profile")[0] == pytest.approx(10.0, abs=0.5)


# ---------- the scout pipeline with a browser that has page data ----------


class FakeBrowser:
    def __init__(self, user=None, people=None, fast=False, fetched=None, needs_id=False):
        self.user, self.people, self.fast, self.fetched = user, people or {}, fast, fetched
        self.needs_id = needs_id
        self.requests = []

    def page_profile(self, profile_id, username):
        return self.user

    def page_people(self, profile_id):
        return self.people

    def can_fetch_profile(self, profile_id, user_id=None):
        return self.fast and (bool(user_id) or not self.needs_id)

    def fetch_profile(self, profile_id, username, user_id=None):
        self.requests.append((username, user_id) if self.needs_id else username)
        return self.fetched


@pytest.fixture
def scout(tmp_path):
    engine, sessions = open_database(tmp_path / "scout.db")
    service = ApplicationService(sessions, tmp_path)
    service.scout.ai = None
    yield service, sessions
    service.shutdown()
    engine.dispose()


def start(service, browser, author="artist123"):
    service.scout.browser = browser
    service.call(
        "settings.save",
        {"profiles_per_hour": 0, "scout_methods": ["posts"], "scout_profile_type": "everyone"},
    )
    service.call("scout.source_add", {"values": [SOURCE]})
    job = service.call("scout.start_internal", {"profile_id": ACCOUNT})["id"]
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=SOURCE, ready=True, posts=[POST])}
    )
    service.call(
        "scout.commit_internal",
        {"id": job, "snapshot": dict(url=POST, ready=True, author=author, collaborators=[])},
    )
    return job


def dom_page(name="artist123"):
    return dict(
        url=f"https://www.instagram.com/{name}/",
        ready=True,
        title=f"{name} (@{name})",
        header=f"{name}\n2.6K followers 100 following 40 posts\nRapper. new single out now",
        description='2.6K Followers - Artist on Instagram: "Rapper. new single out now"',
        links=[],
        external_url="",
        captions=[],
        user_id="4242",
    )


def scout_row(sessions, name="artist123"):
    with sessions() as session:
        lead = session.scalar(select(Lead).where(Lead.username == name))
        return lead, session.get(LeadScoutProfile, lead.id)


def test_page_json_fills_what_the_dom_cannot_see(scout):
    service, sessions = scout
    job = start(service, FakeBrowser(user=web_user()))
    service.call("scout.commit_internal", {"id": job, "snapshot": dom_page()})
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed" and state["stats"]["resolver"]["merged"] == 1
    lead, row = scout_row(sessions)
    # Exact count from the JSON instead of the rounded "2.6K"; the hidden business email.
    assert lead.followers == 2612
    assert row.emails == ["booking@artist.com"]
    assert row.bio_links == [] and lead.external_url == "https://linktr.ee/artist123"


def test_a_fast_request_needs_no_page(scout):
    service, sessions = scout
    browser = FakeBrowser(fast=True, fetched={"user": web_user(), "kind": "page_request"})
    job = start(service, browser)
    state = service.call("capture.state", {"id": job})
    assert state["kind"] == "profile" and state["access"] == "request"
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=state["url"], ready=True)}
    )
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed" and state["stats"]["resolver"]["request"] == 1
    assert browser.requests == ["artist123"]
    lead, row = scout_row(sessions)
    assert lead.followers == 2612 and row.emails == ["booking@artist.com"]


def test_the_candidate_id_from_the_source_page_feeds_the_query_by_id(scout):
    service, _ = scout
    fetched = {"user": web_user(), "kind": "page_query"}
    browser = FakeBrowser(
        fast=True, fetched=fetched, needs_id=True, people={"artist123": {"id": "4242"}}
    )
    job = start(service, browser)
    state = service.call("capture.state", {"id": job})
    assert state["access"] == "request"
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=state["url"], ready=True)}
    )
    assert browser.requests == [("artist123", "4242")]
    assert service.call("capture.state", {"id": job})["stats"]["resolver"]["request"] == 1
    # Without the id on the source page the profile page is opened.
    assert page_data.clean_hint({"id": "12ab"}) is None
    assert page_data.people([json.dumps({"username": "x.y", "pk": 99})]) == {"x.y": {"id": "99"}}


def test_a_refused_fast_request_opens_the_page_without_a_pause(scout):
    service, sessions = scout
    browser = FakeBrowser(user=web_user(), fast=True, fetched={"fallback": "refused"})
    job = start(service, browser)
    url = service.call("capture.state", {"id": job})["url"]
    service.call("scout.commit_internal", {"id": job, "snapshot": dict(url=url, ready=True)})
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "running" and state["access"] == "navigate" and state["url"] == url
    service.call("scout.commit_internal", {"id": job, "snapshot": dom_page()})
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed"
    assert state["stats"]["resolver"]["request_fallback"] == 1
    assert scout_row(sessions)[0].followers == 2612


def test_a_private_candidate_from_page_data_is_never_opened(scout):
    service, sessions = scout
    job = start(service, FakeBrowser(people={"artist123": {"is_private": True}}))
    state = service.call("capture.state", {"id": job})
    assert state["kind"] == "profile" and state["access"] == "none"
    service.call(
        "scout.commit_internal", {"id": job, "snapshot": dict(url=state["url"], ready=True)}
    )
    assert service.call("capture.state", {"id": job})["status"] == "completed"
    with sessions() as session:
        assert session.get(ScoutProcessedProfile, "artist123").reason == "PROFILE_PRIVATE"


def test_without_a_browser_runtime_the_page_path_is_unchanged(scout):
    service, sessions = scout
    job = start(service, None)
    assert service.call("capture.state", {"id": job})["access"] == "navigate"
    service.call("scout.commit_internal", {"id": job, "snapshot": dom_page()})
    state = service.call("capture.state", {"id": job})
    assert state["status"] == "completed" and state["stats"]["resolver"]["browser"] == 1


# ---------- the browser side: learning and replaying the page's request in Chromium ----------


def test_chromium_learns_the_page_request_and_replays_it():
    import os

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    sync_api = pytest.importorskip("playwright.sync_api")
    from artist_lead_finder.chromium_runtime import ChromiumRuntime, Window, watch_data

    script = (
        "<script>fetch('/graphql/query', {method: 'POST', headers: {"
        "'x-ig-app-id': '936619743392459', 'x-csrftoken': 'tok',"
        "'content-type': 'application/x-www-form-urlencoded'},"
        "body: 'doc_id=7&variables=' + encodeURIComponent(JSON.stringify("
        "{username: 'artist123', render_surface: 'PROFILE'}))})"
        ".then(r => r.text()).then(() => document.body.dataset.done = '1')</script>"
    )
    html = f"<html><body><main></main>{script}</body></html>"
    seen, refuse = [], []

    def answer(route):
        request = route.request
        if request.url.startswith("https://www.instagram.com/graphql/query"):
            pairs = dict(profile_requests.parse_qsl(request.post_data or ""))
            name = json.loads(pairs["variables"])["username"]
            seen.append((name, request.headers.get("x-csrftoken"), request.headers.get("referer")))
            if refuse:
                route.fulfill(status=429, body="")
                return
            body = json.dumps({"data": {"user": web_user(name)}})
            route.fulfill(status=200, content_type="application/json", body=body)
        elif request.url.split("?")[0] == "https://www.instagram.com/artist123/":
            route.fulfill(status=200, content_type="text/html", body=html)
        else:
            route.abort()

    try:
        manager = sync_api.sync_playwright().start()
        browser = manager.chromium.launch(headless=True, channel="chromium")
    except Exception as error:  # pragma: no cover - depends on the local Chromium install
        pytest.skip(f"Bundled Chromium unavailable: {error}")
    try:
        context_ = browser.new_context()
        context_.route("**/*", answer)
        page = context_.new_page()
        window = Window(browser, context_, page)
        watch_data(window)
        runtime = ChromiumRuntime(sessions=None)
        runtime.windows["w"] = window
        page.goto("https://www.instagram.com/artist123/")
        page.wait_for_function("document.body.dataset.done === '1'")

        user = runtime.page_profile("w", "artist123")
        assert user["biography"] == "Rapper. new single out now"
        assert window.profile_template.kind == "page_request"
        assert runtime.can_fetch_profile("w")

        fetched = runtime.fetch_profile("w", "next.one")
        assert fetched["user"]["username"] == "next.one" and fetched["kind"] == "page_request"
        # The replay carries the app's own headers and the target's page as referrer.
        assert seen[-1] == ("next.one", "tok", "https://www.instagram.com/next.one/")

        refuse.append(True)
        assert runtime.fetch_profile("w", "third.one")["fallback"].startswith("refused")
        assert not runtime.can_fetch_profile("w") and window.rate_limited is False
    finally:
        runtime.windows.clear()
        browser.close()
        manager.stop()


def test_chromium_replays_the_query_by_id_and_drops_a_missing_address():
    import os

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    sync_api = pytest.importorskip("playwright.sync_api")
    from artist_lead_finder.chromium_runtime import ChromiumRuntime, Window, watch_data

    script = (
        "<script>fetch('/graphql/query', {method: 'POST', headers: {"
        "'x-ig-app-id': '936619743392459', 'x-csrftoken': 'tok',"
        "'content-type': 'application/x-www-form-urlencoded'},"
        "body: 'doc_id=7&variables=' + encodeURIComponent(JSON.stringify("
        "{id: '4242', render_surface: 'PROFILE'}))})"
        ".then(r => r.text()).then(() => document.body.dataset.done = '1')</script>"
    )
    html = f"<html><body><main></main>{script}</body></html>"
    names = {"4242": "artist123", "777": "next.one"}
    asked = []

    def answer(route):
        request = route.request
        if request.url.startswith("https://www.instagram.com/graphql/query"):
            pairs = dict(profile_requests.parse_qsl(request.post_data or ""))
            uid = json.loads(pairs["variables"])["id"]
            asked.append(uid)
            body = json.dumps({"data": {"user": web_user(names[uid], id=uid)}})
            route.fulfill(status=200, content_type="application/json", body=body)
        elif "web_profile_info" in request.url:
            asked.append("web_profile_info")
            route.fulfill(status=404, body="")
        elif request.url.split("?")[0] == "https://www.instagram.com/artist123/":
            route.fulfill(status=200, content_type="text/html", body=html)
        else:
            route.abort()

    try:
        manager = sync_api.sync_playwright().start()
        browser = manager.chromium.launch(headless=True, channel="chromium")
    except Exception as error:  # pragma: no cover - depends on the local Chromium install
        pytest.skip(f"Bundled Chromium unavailable: {error}")
    try:
        context_ = browser.new_context()
        context_.route("**/*", answer)
        page = context_.new_page()
        window = Window(browser, context_, page)
        watch_data(window)
        runtime = ChromiumRuntime(sessions=None)
        runtime.windows["w"] = window
        page.goto("https://www.instagram.com/artist123/")
        page.wait_for_function("document.body.dataset.done === '1'")

        assert runtime.page_profile("w", "artist123")["biography"]
        assert window.profile_template.kind == "page_query"
        # A candidate without a known id is not asked for.
        assert not runtime.can_fetch_profile("w") and runtime.can_fetch_profile("w", "777")
        fetched = runtime.fetch_profile("w", "next.one", "777")
        assert fetched["user"]["username"] == "next.one" and asked[-1] == "777"
        # An id that turns out to be someone else's is not taken for this candidate.
        assert "user" not in runtime.fetch_profile("w", "third.one", "777")

        # web_profile_info that answers 404 is not tried again in this window.
        window.profile_template = profile_requests.borrowed(
            {"x-ig-app-id": "936619743392459"}, "artist123"
        )
        assert runtime.fetch_profile("w", "next.one")["status"] == 404
        assert window.borrowed_off and window.profile_template is None
        window.session_headers = {"x-ig-app-id": "936619743392459"}
        from artist_lead_finder.chromium_runtime import learn_profile_request

        learn_profile_request(window, "artist123", web_user())
        # The page's own query is learned again; web_profile_info never comes back.
        assert window.profile_template.kind == "page_query"
        window.profile_template = None
        learn_profile_request(window, "someone.else", web_user("someone.else"))
        assert window.profile_template is None
    finally:
        runtime.windows.clear()
        browser.close()
        manager.stop()
