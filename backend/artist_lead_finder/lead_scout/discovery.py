"""Instagram candidate discovery providers (posts/reels, tagged, stories, follow lists).

The browser is driven step by step by the desktop queue (it opens a page, a page script
reads it, the snapshot comes back here), so a provider is a resumable generator split
into two calls:

    start(source)            -> the first pages to open for a source
    handle(step, snapshot)   -> candidates found on that page, more pages, bookkeeping

State lives in the database between steps, so pause, stop, rate-limit breaks and app
restarts work at every page. A provider never classifies profiles, checks followers,
calls AI or saves leads; ScoutService.process_candidates takes it from there.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from .candidates import (
    CandidateGate,
    ScoutCandidate,
    normalize_instagram_username,
    parse_instagram_post_url,
)
from .settings import ScoutSettings

GUEST_POSTS_PER_SOURCE = 12
MAX_COMMENTS_PER_POST = 200
METRIC_KEYS = (
    "itemsSeen",
    "itemsProcessed",
    "candidatesFound",
    "duplicatesSkipped",
    "alreadyProcessed",
    "failures",
)
# Metrics group of each candidate method (what the Scout UI shows per source).
GROUP_OF_METHOD = {
    "post": "posts",
    "reel": "posts",
    "comment": "comments",
    "tagged": "tagged",
    "story": "stories",
    "followers": "followers",
    "following": "following",
}


def post_url(value: str) -> str:
    """Canonical publication URL for /p/, /reel/, /reels/ and /tv/ links."""
    parsed = parse_instagram_post_url(value)
    if parsed is None:
        raise ValueError("Invalid publication URL")
    return parsed.canonical_url


def source_name(source_url: str) -> str:
    return source_url.rstrip("/").split("/")[-1]


def empty_metrics() -> dict[str, int]:
    return {key: 0 for key in METRIC_KEYS}


@dataclass
class DiscoveryContext:
    settings: ScoutSettings
    guest: bool
    # Per-step, in-memory candidate validation (source, ignore-list, per-run duplicates).
    gate: CandidateGate
    # (source username, "post" | "tagged_post" | "story") -> ids already handled.
    skip: Callable[[str, str], list[str]]


@dataclass
class StepResult:
    group: str
    # (candidate, comment observation or None) in page order.
    candidates: list[tuple[ScoutCandidate, dict | None]] = field(default_factory=list)
    # Pages queued right away (a story's shared publication).
    steps: list[dict] = field(default_factory=list)
    # Publications queued in batches as the run needs them.
    backlog: list[dict] = field(default_factory=list)
    # (shortcode, kind, status, error) for publications; kind is "post" or "tagged_post".
    posts: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    # (story id, status, error)
    stories: list[tuple[str, str, str | None]] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=empty_metrics)
    log: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    debug: dict | None = None


class DiscoveryProvider:
    """Common interface: `method`, the page kinds it reads, `start` and `handle`."""

    method = ""
    group = ""
    kinds: tuple[str, ...] = ()
    available = True

    def enabled(self, settings: ScoutSettings) -> bool:
        return self.group in settings.scout_methods

    def start(self, source_url: str, ctx: DiscoveryContext) -> list[dict]:
        return []

    def handle(self, step: dict, snapshot: dict, ctx: DiscoveryContext) -> StepResult:
        raise NotImplementedError


def _grid_args(ctx: DiscoveryContext, source: str, kind: str) -> dict:
    settings = ctx.settings
    return {
        "maxPosts": GUEST_POSTS_PER_SOURCE if ctx.guest else settings.scout_max_posts_per_source,
        "maxScrollRounds": settings.scout_max_scroll_rounds,
        "scrollDelayMs": settings.scout_scroll_delay_ms,
        "maxNoProgressRounds": settings.scout_max_no_progress_rounds,
        "skip": ctx.skip(source, kind) if settings.scout_skip_processed else [],
        "debug": settings.scout_debug,
    }


def _grid(
    step: dict, snapshot: dict, ctx: DiscoveryContext, kind: str, result: StepResult, title: str
):
    """Grid snapshot -> publications for the backlog (already processed ones are excluded)."""
    source = source_name(step["source"])
    result.log.append(f"[Scout][{title}][@{source}]")
    if snapshot.get("unavailable"):
        result.notices.append(f"Страница недоступна: {step['url']}")
        result.log.append("Page unavailable; nothing to scan.")
        return
    seen = int(snapshot.get("seen") or 0)
    already = int(snapshot.get("already_processed") or 0)
    # The page script already drops processed shortcodes; keep the core strict as well.
    skip = set((step.get("args") or {}).get("skip") or [])
    queued = set()
    for raw in snapshot.get("posts", []):
        parsed = parse_instagram_post_url(str(raw))
        if parsed is None or parsed.shortcode in queued:
            continue
        if parsed.shortcode in skip:
            already += 1
            continue
        queued.add(parsed.shortcode)
        result.backlog.append(
            {
                "kind": kind,
                "url": parsed.canonical_url,
                "source": step["source"],
                "code": parsed.shortcode,
            }
        )
    seen = max(seen, len(queued) + already)
    result.metrics["itemsSeen"] += seen
    result.metrics["alreadyProcessed"] += already
    result.log += [
        f"Found {seen} post tiles",
        f"{already} already processed",
        f"Queued {len(queued)} publications (scroll rounds {snapshot.get('rounds', 0)},"
        f" stop: {snapshot.get('end_reason', 'n/a')})",
    ]
    if not seen:
        result.notices.append(f"Нет доступных публикаций: {step['url']}")
    elif not queued:
        result.notices.append(f"Все публикации уже разобраны ранее: {step['url']}")
    if snapshot.get("debug"):
        result.debug = snapshot["debug"]


def _authors(
    step: dict,
    snapshot: dict,
    ctx: DiscoveryContext,
    method: str,
    evidence: str,
    result: StepResult,
) -> str | None:
    """Author + collaborators of an opened publication through the candidate gate.

    Returns the author, or None when it cannot be determined (never guessed).
    """
    parsed = parse_instagram_post_url(step["url"])
    code = parsed.shortcode if parsed else step["url"]
    author = normalize_instagram_username(snapshot.get("author"))
    if not author:
        result.metrics["failures"] += 1
        result.log.append(
            "ERROR: post author not found"
            f" ({snapshot.get('parse_error', 'unknown')}); not guessing."
        )
        result.debug = snapshot.get("debug") or {"reason": "author_not_found"}
        return None
    collaborators = [
        name
        for name in (
            normalize_instagram_username(value) for value in snapshot.get("collaborators", [])
        )
        if name and name != author
    ]
    result.log += [
        f"Post author: @{author} (via {snapshot.get('author_strategy') or 'unknown'})",
        "Collaborators: " + (", ".join(f"@{name}" for name in collaborators) or "—"),
    ]
    if snapshot.get("collaborators_truncated"):
        result.log.append("Header says 'and others': only listed collaborators are used.")
    before = ctx.gate.duplicates
    emitted = []
    for name, kind in [
        (author, "post_author"),
        *[(name, "collaborator") for name in collaborators],
    ]:
        admitted = ctx.gate.admit(name)
        if admitted:
            emitted.append(admitted)
            result.candidates.append(
                (
                    ScoutCandidate(
                        admitted,
                        ctx.gate.source,
                        method,
                        code,
                        parsed.canonical_url if parsed else step["url"],
                        evidence_type=f"{evidence}{kind}",
                    ),
                    None,
                )
            )
    result.metrics["duplicatesSkipped"] += ctx.gate.duplicates - before
    result.log.append(
        "Candidates emitted: " + (", ".join(f"@{name}" for name in emitted) or "none")
    )
    return author


class PostDiscoveryProvider(DiscoveryProvider):
    """Source posts and reels: author and collaborators of each publication."""

    method = "post"
    group = "posts"
    kinds = ("source", "post")

    def enabled(self, settings):
        return bool({"posts", "comments"} & set(settings.scout_methods))

    def start(self, source_url, ctx):
        args = _grid_args(ctx, source_name(source_url), "post")
        return [{"kind": "source", "url": source_url, "source": source_url, "args": args}]

    def handle(self, step, snapshot, ctx):
        result = StepResult(group=self.group)
        if step["kind"] == "source":
            _grid(step, snapshot, ctx, "post", result, "Posts")
            return result
        parsed = parse_instagram_post_url(step["url"])
        media = parsed.type if parsed else "post"
        source = source_name(step["source"])
        result.log += [
            f"[Scout][Posts][@{source}]",
            f"Opening {media} {parsed.shortcode if parsed else step['url']}",
        ]
        if snapshot.get("unavailable"):
            result.posts.append((parsed.shortcode, "post", "unavailable", None))
            result.log.append("Publication unavailable; skipped.")
            return result
        use_posts = "posts" in ctx.settings.scout_methods
        author = (
            _authors(step, snapshot, ctx, media, "", result)
            if use_posts
            else normalize_instagram_username(snapshot.get("author"))
        )
        if not author:
            if not use_posts:
                result.metrics["failures"] += 1
            result.posts.append(
                (
                    parsed.shortcode,
                    "post",
                    "failed",
                    snapshot.get("parse_error", "author_not_found"),
                )
            )
            return result
        if "comments" in ctx.settings.scout_methods:
            _comments(step, snapshot, ctx, result)
        result.metrics["itemsProcessed"] += 1
        result.posts.append((parsed.shortcode, "post", "processed", None))
        return result


def _comments(step: dict, snapshot: dict, ctx: DiscoveryContext, result: StepResult) -> None:
    """Comment authors of a source publication (the comments method); evidence travels along."""
    comments = snapshot.get("comments", [])
    if not isinstance(comments, list):
        raise ValueError("Invalid comments snapshot")
    if not comments:
        result.notices.append("Нет доступных комментариев: " + step["url"])
    if snapshot.get("comments_limited"):
        result.notices.append("Прочитана доступная часть комментариев: " + step["url"])
    parsed = parse_instagram_post_url(step["url"])
    for comment in comments[:MAX_COMMENTS_PER_POST]:
        if not isinstance(comment, dict):
            continue
        name = ctx.gate.valid(str(comment.get("profile_url", "")))
        if not name:
            continue
        observation = {
            "source": step["source"],
            "url": step["url"],
            "caption": str(comment.get("text", ""))[:1500],
            "published_at": str(comment.get("published_at") or "")[:80] or None,
            "kind": "comment",
            "author": name,
        }
        result.candidates.append(
            (
                ScoutCandidate(
                    name,
                    ctx.gate.source,
                    "comment",
                    parsed.shortcode if parsed else None,
                    step["url"],
                    evidence_type="commenter",
                ),
                observation,
            )
        )


class TaggedDiscoveryProvider(DiscoveryProvider):
    """Publications where the source is tagged: their original author is the candidate."""

    method = "tagged"
    group = "tagged"
    kinds = ("tagged_grid", "tagged_post")

    def start(self, source_url, ctx):
        args = _grid_args(ctx, source_name(source_url), "tagged_post")
        return [
            {
                "kind": "tagged_grid",
                "url": source_url + "tagged/",
                "source": source_url,
                "args": args,
            }
        ]

    def handle(self, step, snapshot, ctx):
        result = StepResult(group=self.group)
        if step["kind"] == "tagged_grid":
            _grid(step, snapshot, ctx, "tagged_post", result, "Tagged")
            return result
        parsed = parse_instagram_post_url(step["url"])
        result.log += [
            f"[Scout][Tagged][@{source_name(step['source'])}]",
            f"Opening tagged {parsed.type} {parsed.shortcode}",
        ]
        if snapshot.get("unavailable"):
            result.posts.append((parsed.shortcode, "tagged_post", "unavailable", None))
            result.log.append("Publication unavailable; skipped.")
            return result
        if not _authors(step, snapshot, ctx, "tagged", "tagged_", result):
            result.posts.append((parsed.shortcode, "tagged_post", "failed", "author_not_found"))
            return result
        result.metrics["itemsProcessed"] += 1
        result.posts.append((parsed.shortcode, "tagged_post", "processed", None))
        return result


class StoryDiscoveryProvider(DiscoveryProvider):
    """Active stories: mention stickers, profile links and authors of shared posts/reels."""

    method = "story"
    group = "stories"
    kinds = ("stories", "story_media")

    def enabled(self, settings):
        return "stories" in settings.scout_methods

    def start(self, source_url, ctx):
        if ctx.guest:
            return []  # Stories need a signed-in session.
        source = source_name(source_url)
        settings = ctx.settings
        return [
            {
                "kind": "stories",
                "url": f"https://www.instagram.com/stories/{source}/",
                "source": source_url,
                "args": {
                    "source": source,
                    "maxStories": settings.scout_max_stories_per_source,
                    "delayMs": settings.scout_story_delay_ms,
                    "skip": ctx.skip(source, "story") if settings.scout_skip_processed else [],
                    "debug": settings.scout_debug,
                },
            }
        ]

    def handle(self, step, snapshot, ctx):
        result = StepResult(group=self.group)
        source = source_name(step["source"])
        threshold = ctx.settings.scout_story_confidence
        result.log.append(f"[Scout][Stories][@{source}]")
        if step["kind"] == "story_media":
            return self._shared_media(step, snapshot, ctx, result)
        stories = snapshot.get("stories", [])
        if not isinstance(stories, list):
            raise ValueError("Invalid stories snapshot")
        result.log.append(
            f"Frames seen: {len(stories)} (stop: {snapshot.get('end_reason', 'n/a')})"
        )
        for story in stories[: ctx.settings.scout_max_stories_per_source]:
            story_id = str(story.get("id") or "")
            if not story_id.isdigit():
                continue
            result.metrics["itemsSeen"] += 1
            if story.get("skipped"):
                result.metrics["alreadyProcessed"] += 1
                continue
            if story.get("error"):
                result.metrics["failures"] += 1
                result.stories.append((story_id, "failed", story["error"]))
                result.log.append(f"Story ID: {story_id} — {story['error']}")
                continue
            result.log.append(f"Story ID: {story_id}")
            before = ctx.gate.duplicates
            for found in story.get("candidates", [])[:20]:
                confidence = float(found.get("confidence") or 0)
                evidence = str(found.get("evidenceType") or "unknown")[:40]
                if confidence < threshold:
                    result.log.append(
                        f"Ignored @{found.get('username')}"
                        f" ({evidence}, confidence {confidence:.2f})"
                    )
                    continue
                name = ctx.gate.admit(found.get("username"))
                if name:
                    result.candidates.append(
                        (
                            ScoutCandidate(
                                name,
                                source,
                                "story",
                                story_id,
                                story.get("url"),
                                evidence_type=evidence,
                                confidence=confidence,
                            ),
                            None,
                        )
                    )
                    result.log.append(
                        f"Detected {evidence.replace('_', ' ')}: @{name} — candidate emitted."
                    )
            result.metrics["duplicatesSkipped"] += ctx.gate.duplicates - before
            for media in story.get("shared_media", [])[:5]:
                parsed = parse_instagram_post_url(str(media.get("url", "")))
                if parsed:
                    result.steps.append(
                        {
                            "kind": "story_media",
                            "url": parsed.canonical_url,
                            "source": step["source"],
                            "story_id": story_id,
                            "story_url": story.get("url"),
                            "args": {"comments": False, "debug": ctx.settings.scout_debug},
                        }
                    )
                    result.log.append(
                        f"Detected shared {parsed.type}; opening it for the original author."
                    )
            result.metrics["itemsProcessed"] += 1
            result.stories.append((story_id, "processed", None))
        return result

    def _shared_media(self, step, snapshot, ctx, result):
        parsed = parse_instagram_post_url(step["url"])
        if snapshot.get("unavailable"):
            result.log.append("Shared publication unavailable.")
            return result
        author = normalize_instagram_username(snapshot.get("author"))
        if not author:
            result.metrics["failures"] += 1
            result.log.append(
                "ERROR: original author of the shared publication not found; not guessing."
            )
            result.debug = snapshot.get("debug") or {"reason": "author_not_found"}
            return result
        result.log.append(f"Original author: @{author}")
        before = ctx.gate.duplicates
        name = ctx.gate.admit(author)
        if name:
            result.candidates.append(
                (
                    ScoutCandidate(
                        name,
                        ctx.gate.source,
                        "story",
                        step.get("story_id"),
                        step["url"],
                        evidence_type=f"shared_{parsed.type}_author",
                        confidence=0.95,
                    ),
                    None,
                )
            )
            result.log.append("Candidate emitted.")
        result.metrics["duplicatesSkipped"] += ctx.gate.duplicates - before
        return result


class FollowDiscoveryProvider(DiscoveryProvider):
    """Followers or following list of a source, paged inside the browser by follow.js."""

    def __init__(self, name: str):
        self.method = name
        self.group = name
        self.kinds = (name,)

    def start(self, source_url, ctx):
        settings = ctx.settings
        return [
            {
                "kind": self.method,
                "url": f"{source_url}{self.method}/",
                "source": source_url,
                "args": {
                    "pageSize": settings.scout_follow_page_size,
                    "delayMs": settings.scout_follow_delay_seconds * 1000,
                    "max": settings.scout_follow_max,
                },
            }
        ]

    def handle(self, step, snapshot, ctx):
        result = StepResult(group=self.group)
        users = snapshot.get("users", [])
        if not isinstance(users, list):
            raise ValueError("Invalid follow list snapshot")
        result.metrics["itemsSeen"] += len(users)
        before = ctx.gate.duplicates
        for raw in users[: ctx.settings.scout_follow_max]:
            name = ctx.gate.admit(str(raw))
            if name:
                result.candidates.append(
                    (ScoutCandidate(name, ctx.gate.source, self.method, None, step["url"]), None)
                )
        result.metrics["duplicatesSkipped"] += ctx.gate.duplicates - before
        result.metrics["itemsProcessed"] += 1
        result.log += [
            f"[Scout][{self.method.capitalize()}][@{ctx.gate.source}]",
            f"Read {len(users)} accounts",
        ]
        if not users:
            result.notices.append(f"Список {self.method} недоступен или пуст: {step['url']}")
        elif snapshot.get("limited"):
            result.notices.append(
                f"Список {self.method}: прочитано {len(users)}, достигнут лимит или конец страницы."
            )
        return result


PROVIDERS: list[DiscoveryProvider] = [
    PostDiscoveryProvider(),
    TaggedDiscoveryProvider(),
    StoryDiscoveryProvider(),
    FollowDiscoveryProvider("followers"),
    FollowDiscoveryProvider("following"),
]


def provider_for(kind: str) -> DiscoveryProvider:
    for provider in PROVIDERS:
        if kind in provider.kinds:
            return provider
    raise ValueError(f"No provider for page kind {kind}")


def initial_tasks(source_url: str, ctx: DiscoveryContext) -> tuple[list[dict], list[str]]:
    tasks, notices = [], []
    for provider in PROVIDERS:
        if provider.enabled(ctx.settings):
            started = provider.start(source_url, ctx)
            if not started and provider.group == "stories":
                notices.append("Stories требуют сохранённой сессии Instagram; для гостя пропущены.")
            tasks += started
    return tasks, notices
