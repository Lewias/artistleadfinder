"""Instagram candidate discovery providers (posts/reels, tagged, follow lists).

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

from . import feed as grid_feed
from .candidates import (
    CandidateGate,
    ScoutCandidate,
    normalize_instagram_username,
    parse_instagram_post_url,
)
from .settings import ScoutSettings

GUEST_POSTS_PER_SOURCE = 12
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
    "tagged": "tagged",
    "followers": "followers",
    "following": "following",
    "profile": "profiles",
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
    # (source username, "post" | "tagged_post") -> ids already handled.
    skip: Callable[[str, str], list[str]]


@dataclass
class StepResult:
    group: str
    # (candidate, caption observation or None) in page order.
    candidates: list[tuple[ScoutCandidate, dict | None]] = field(default_factory=list)
    # Publications queued in batches as the run needs them.
    backlog: list[dict] = field(default_factory=list)
    # (shortcode, kind, status, error) for publications; kind is "post" or "tagged_post".
    posts: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=empty_metrics)
    log: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    debug: dict | None = None
    # The source's own page is unavailable: its other pages are not opened either.
    source_unavailable: bool = False


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
    step: dict,
    snapshot: dict,
    ctx: DiscoveryContext,
    kind: str,
    result: StepResult,
    title: str,
    resolve: Callable | None = None,
):
    """Grid snapshot -> publications for the backlog (already processed ones are excluded).

    `resolve(parsed, facts, result)` handles a publication from the data the grid page
    received ("done") or leaves it to be opened as before (None)."""
    source = source_name(step["source"])
    result.log.append(f"[Scout][{title}][@{source}]")
    if snapshot.get("unavailable"):
        result.notices.append(f"Страница недоступна: {step['url']}")
        result.log.append("Page unavailable; nothing to scan.")
        result.source_unavailable = step["kind"] == "source"
        return
    seen = int(snapshot.get("seen") or 0)
    already = int(snapshot.get("already_processed") or 0)
    # The page script already drops processed shortcodes; keep the core strict as well.
    skip = set((step.get("args") or {}).get("skip") or [])
    facts_by_code = grid_feed.from_snapshot(snapshot) if resolve else {}
    queued = set()
    inline = 0
    for raw in snapshot.get("posts", []):
        parsed = parse_instagram_post_url(str(raw))
        if parsed is None or parsed.shortcode in queued:
            continue
        if parsed.shortcode in skip:
            already += 1
            continue
        queued.add(parsed.shortcode)
        facts = facts_by_code.get(parsed.shortcode)
        outcome = resolve(parsed, facts, result) if facts and resolve else None
        if outcome == "done":
            inline += 1
            continue
        item = {
            "kind": kind,
            "url": parsed.canonical_url,
            "source": step["source"],
            "code": parsed.shortcode,
        }
        result.backlog.append(item)
    seen = max(seen, len(queued) + already)
    result.metrics["itemsSeen"] += seen
    result.metrics["alreadyProcessed"] += already
    result.log += [
        f"Found {seen} post tiles",
        f"{already} already processed",
        f"Queued {len(queued) - inline} publications"
        f" (scroll rounds {snapshot.get('rounds', 0)}, stop: {snapshot.get('end_reason', 'n/a')})",
    ]
    if resolve:
        result.log.append(
            f"From grid data without opening: {inline}; to open: {len(queued) - inline}"
            f" (publication data received: {len(facts_by_code)})"
        )
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
    emitted = _emit_authors(
        ctx,
        result,
        author,
        collaborators,
        code,
        parsed.canonical_url if parsed else step["url"],
        method,
        evidence,
    )
    result.log.append(
        "Candidates emitted: " + (", ".join(f"@{name}" for name in emitted) or "none")
    )
    return author


def _emit_authors(
    ctx: DiscoveryContext,
    result: StepResult,
    author: str,
    collaborators: list[str],
    code: str,
    url: str,
    method: str,
    evidence: str,
) -> list[str]:
    """Author and collaborators of one publication through the candidate gate."""
    before = ctx.gate.duplicates
    emitted = []
    named = [(author, "post_author"), *[(name, "collaborator") for name in collaborators]]
    for name, kind in named:
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
                        url,
                        evidence_type=f"{evidence}{kind}",
                    ),
                    None,
                )
            )
    result.metrics["duplicatesSkipped"] += ctx.gate.duplicates - before
    return emitted


def _posts_from_grid(ctx: DiscoveryContext):
    """Source publications from grid data: their author and collaborators, no page needed."""

    def resolve(parsed, facts, result) -> str:
        _emit_authors(
            ctx,
            result,
            facts.author,
            facts.collaborators,
            parsed.shortcode,
            parsed.canonical_url,
            parsed.type,
            "",
        )
        result.metrics["itemsProcessed"] += 1
        result.posts.append((parsed.shortcode, "post", "processed", None))
        return "done"

    return resolve


def _tagged_from_grid(ctx: DiscoveryContext):
    """Tagged publications from grid data: their author is the candidate, no page needed."""

    def resolve(parsed, facts, result) -> str:
        _emit_authors(
            ctx,
            result,
            facts.author,
            facts.collaborators,
            parsed.shortcode,
            parsed.canonical_url,
            "tagged",
            "tagged_",
        )
        result.metrics["itemsProcessed"] += 1
        result.posts.append((parsed.shortcode, "tagged_post", "processed", None))
        return "done"

    return resolve


class PostDiscoveryProvider(DiscoveryProvider):
    """Source posts and reels: author and collaborators of each publication."""

    method = "post"
    group = "posts"
    kinds = ("source", "post")

    def start(self, source_url, ctx):
        args = _grid_args(ctx, source_name(source_url), "post")
        return [{"kind": "source", "url": source_url, "source": source_url, "args": args}]

    def handle(self, step, snapshot, ctx):
        result = StepResult(group=self.group)
        if step["kind"] == "source":
            _grid(step, snapshot, ctx, "post", result, "Posts", _posts_from_grid(ctx))
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
        if not _authors(step, snapshot, ctx, media, "", result):
            result.posts.append(
                (
                    parsed.shortcode,
                    "post",
                    "failed",
                    snapshot.get("parse_error", "author_not_found"),
                )
            )
            return result
        result.metrics["itemsProcessed"] += 1
        result.posts.append((parsed.shortcode, "post", "processed", None))
        return result


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
            _grid(step, snapshot, ctx, "tagged_post", result, "Tagged", _tagged_from_grid(ctx))
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


class FollowDiscoveryProvider(DiscoveryProvider):
    """Followers or following list of a source, paged inside the browser by follow.js."""

    def __init__(self, name: str):
        self.method = name
        self.group = name
        self.kinds = (name,)

    def limit(self, settings: ScoutSettings) -> int:
        if self.method == "followers":
            return settings.scout_followers_max
        return settings.scout_following_max

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
                    "max": self.limit(settings),
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
        for raw in users[: self.limit(ctx.settings)]:
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


class ProfileCheckProvider(DiscoveryProvider):
    """«Проверить профили»: the source account itself becomes the only profile step."""

    method = "profile"
    group = "profiles"

    def start(self, source_url, ctx):
        name = source_name(source_url)
        return [ScoutCandidate(name, name, self.method, None, source_url).task()]


PROFILE_CHECK = ProfileCheckProvider()
PROVIDERS: list[DiscoveryProvider] = [
    PostDiscoveryProvider(),
    TaggedDiscoveryProvider(),
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
    if PROFILE_CHECK.enabled(ctx.settings):
        # The profile check does not walk the source's posts or connections.
        return PROFILE_CHECK.start(source_url, ctx), notices
    for provider in PROVIDERS:
        if provider.enabled(ctx.settings):
            tasks += provider.start(source_url, ctx)
    return tasks, notices
