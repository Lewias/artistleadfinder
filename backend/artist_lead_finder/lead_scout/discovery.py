"""Discovery providers: each turns one kind of browser page into candidates or more pages.

A provider never touches the database directly and never saves leads; ScoutService
decides on duplicates, quotas and persistence.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .candidates import ScoutCandidate, clean_username, profile_link
from .settings import ScoutSettings

GUEST_POSTS_PER_SOURCE = 12
MAX_POSTS_PER_SOURCE = 120
MAX_COMMENTS_PER_POST = 200
POST_PATH = re.compile(r"/(?:([a-zA-Z0-9_.]{1,30})/)?(p|reel|reels|tv)/([a-zA-Z0-9_-]{1,80})/?")


def post_url(value: str) -> str:
    """Canonical publication URL for /p/, /reel/, /reels/ and /tv/ links."""
    parsed = urlsplit(value)
    match = POST_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"instagram.com", "www.instagram.com"}
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not match
    ):
        raise ValueError("Invalid publication URL")
    author_prefix = f"{match[1]}/" if match[1] else ""
    kind = "reel" if match[2] == "reels" else match[2]
    return f"https://www.instagram.com/{author_prefix}{kind}/{match[3]}/"


def post_id(url: str) -> str:
    """Shortcode used as the processed-post key."""
    return url.rstrip("/").split("/")[-1]


def source_name(source_url: str) -> str:
    return source_url.rstrip("/").split("/")[-1]


@dataclass
class Context:
    settings: ScoutSettings
    guest: bool
    is_post_processed: callable


@dataclass
class DiscoveryResult:
    # (candidate, comment observation or None) in page order.
    candidates: list[tuple[ScoutCandidate, dict | None]] = field(default_factory=list)
    backlog: list[dict] = field(default_factory=list)
    processed_posts: list[tuple[str, str]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


class Provider:
    name = ""
    kinds: tuple[str, ...] = ()
    available = True
    unavailable_reason = ""

    def enabled(self, settings: ScoutSettings) -> bool:
        return self.name in settings.scout_methods

    def initial_tasks(self, source_url: str, settings: ScoutSettings) -> list[dict]:
        return []

    def handle(self, task: dict, snapshot: dict, ctx: Context) -> DiscoveryResult:
        raise NotImplementedError


def _grid(task: dict, snapshot: dict, ctx: Context, kind: str) -> DiscoveryResult:
    result = DiscoveryResult()
    limit = GUEST_POSTS_PER_SOURCE if ctx.guest else MAX_POSTS_PER_SOURCE
    seen = set()
    for raw in snapshot.get("posts", [])[:limit]:
        try:
            url = post_url(raw)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        if ctx.settings.scout_skip_processed and ctx.is_post_processed(post_id(url)):
            continue
        result.backlog.append({"kind": kind, "url": url, "source": task["source"]})
    if not snapshot.get("posts"):
        result.notices.append("Нет доступных публикаций: " + task["url"])
    elif not result.backlog:
        result.notices.append("Все публикации уже разобраны ранее: " + task["url"])
    return result


class PostsProvider(Provider):
    """Source grid (posts and reels); also feeds the comments method."""

    name = "posts"
    kinds = ("source",)

    def enabled(self, settings):
        return bool({"posts", "comments"} & set(settings.scout_methods))

    def initial_tasks(self, source_url, settings):
        return [{"kind": "source", "url": source_url, "source": source_url}]

    def handle(self, task, snapshot, ctx):
        return _grid(task, snapshot, ctx, "post")


class TaggedProvider(Provider):
    name = "tagged"
    kinds = ("tagged_grid",)

    def initial_tasks(self, source_url, settings):
        return [{"kind": "tagged_grid", "url": source_url + "tagged/", "source": source_url}]

    def handle(self, task, snapshot, ctx):
        return _grid(task, snapshot, ctx, "tagged_post")


class PublicationProvider(Provider):
    """A publication page: author, collaborators and (optionally) comment authors."""

    name = "publication"
    kinds = ("post", "tagged_post")

    def enabled(self, settings):
        return False  # Pages are produced by the grid providers, not per source.

    def handle(self, task, snapshot, ctx):
        result = DiscoveryResult()
        source = source_name(task["source"])
        url = task["url"]
        media = "reel" if "/reel/" in url or "/tv/" in url else "post"
        author = clean_username(snapshot.get("author", ""))
        people = [author] + [clean_username(name) for name in snapshot.get("collaborators", [])]
        people = [name for name in dict.fromkeys(people) if name and name != source]
        if task["kind"] == "tagged_post":
            # A tagged publication belongs to someone else; its author is the candidate.
            if author and author != source:
                result.candidates.append(
                    (ScoutCandidate(author, source, "tagged", post_id(url), url), None)
                )
        else:
            if "posts" in ctx.settings.scout_methods:
                for name in people:
                    result.candidates.append(
                        (ScoutCandidate(name, source, media, post_id(url), url), None)
                    )
            if "comments" in ctx.settings.scout_methods:
                comments = snapshot.get("comments", [])
                if not isinstance(comments, list):
                    raise ValueError("Invalid comments snapshot")
                if not comments:
                    result.notices.append("Нет доступных комментариев: " + url)
                if snapshot.get("comments_limited"):
                    result.notices.append("Прочитана доступная часть комментариев: " + url)
                for comment in comments[:MAX_COMMENTS_PER_POST]:
                    if not isinstance(comment, dict):
                        continue
                    name = clean_username(str(comment.get("profile_url", "")))
                    if not name or name == source:
                        continue
                    observation = {
                        "source": task["source"],
                        "url": url,
                        "caption": str(comment.get("text", ""))[:1500],
                        "published_at": str(comment.get("published_at") or "")[:80] or None,
                        "kind": "comment",
                        "author": name,
                    }
                    result.candidates.append(
                        (ScoutCandidate(name, source, "comment", post_id(url), url), observation)
                    )
        result.processed_posts.append((post_id(url), task["kind"]))
        return result


class FollowProvider(Provider):
    """Followers or following list of a source, paged inside the browser by follow.js."""

    def __init__(self, name: str):
        self.name = name
        self.kinds = (name,)

    def initial_tasks(self, source_url, settings):
        return [
            {
                "kind": self.name,
                "url": f"{source_url}{self.name}/",
                "source": source_url,
                "args": {
                    "pageSize": settings.scout_follow_page_size,
                    "delayMs": settings.scout_follow_delay_seconds * 1000,
                    "max": settings.scout_follow_max,
                },
            }
        ]

    def handle(self, task, snapshot, ctx):
        result = DiscoveryResult()
        source = source_name(task["source"])
        users = snapshot.get("users", [])
        if not isinstance(users, list):
            raise ValueError("Invalid follow list snapshot")
        limit = ctx.settings.scout_follow_max
        for raw in users[:limit]:
            name = clean_username(str(raw))
            if name and name != source:
                result.candidates.append(
                    (ScoutCandidate(name, source, self.name, None, task["url"]), None)
                )
        if not users:
            result.notices.append(f"Список {self.name} недоступен или пуст: {task['url']}")
        elif snapshot.get("limited"):
            result.notices.append(
                f"Список {self.name}: прочитано {len(users)}, достигнут лимит или конец страницы."
            )
        return result


class StoriesProvider(Provider):
    """Interface for stories; disabled because stories cannot be read safely yet.

    Viewing stories through automation marks them as seen and needs frame-by-frame
    interaction; the provider stays switched off without breaking other methods.
    """

    name = "stories"
    kinds = ("story",)
    available = False
    unavailable_reason = (
        "Stories пока не поддерживаются: провайдер отключён, остальные методы работают."
    )

    def enabled(self, settings):
        return False


PROVIDERS: list[Provider] = [
    PostsProvider(),
    TaggedProvider(),
    PublicationProvider(),
    FollowProvider("followers"),
    FollowProvider("following"),
    StoriesProvider(),
]


def provider_for(kind: str) -> Provider:
    for provider in PROVIDERS:
        if kind in provider.kinds:
            return provider
    raise ValueError(f"No provider for page kind {kind}")


def initial_tasks(source_url: str, settings: ScoutSettings) -> tuple[list[dict], list[str]]:
    tasks, notices = [], []
    for provider in PROVIDERS:
        if not provider.available and provider.name in settings.scout_methods:
            notices.append(provider.unavailable_reason)
        elif provider.enabled(settings):
            tasks += provider.initial_tasks(source_url, settings)
    return tasks, notices


def source_url_for(username: str) -> str:
    return profile_link(username)
