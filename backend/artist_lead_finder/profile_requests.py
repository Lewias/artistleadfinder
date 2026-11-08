"""Fast profile requests: the profile page's own data request, repeated for another user.

A hand-built `web_profile_info` call was answered with HTTP 429 at once: it lacked the
headers the web app sends with every call. Instead, when a profile page is opened, the
request that brought its owner's data is remembered as a template (method, URL, body and
the app's own x-* headers) and replayed from the same tab with only the username
changed. The browser adds cookies, origin and fetch metadata itself; nothing here reads
or stores cookies.

The profile page asks for its owner by the numeric user id (a GraphQL query). Such a
request is kept as a "page_query" template: the id (and the username, where it also
appears) is swapped for the candidate's, whose id comes from the discovery page's own
data. A request naming the user any other way (escaped, inside a blob) is ignored.

When no profile request was seen, the session headers of any other data request the
app made serve a `web_profile_info` call instead; that address answers 404 to many
sessions, which turns it off for the rest of the run.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote_plus, urlencode, urlsplit, urlunsplit

INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}
DATA_PATH = re.compile(r"^/(?:api/v1/|graphql|api/graphql)")
# Headers the page cannot set or that the browser computes per request.
SKIP_HEADERS = re.compile(
    r"^(?::|cookie$|host$|content-length$|connection$|origin$|referer$|user-agent$"
    r"|accept-encoding$|sec-|priority$|te$|upgrade|proxy-|dnt$)",
    re.I,
)
MAX_BODY = 20000
WEB_PROFILE_INFO = "https://www.instagram.com/api/v1/users/web_profile_info/?username={}"
# Session headers the web app sends with its /api/v1/ calls. The others of a GraphQL
# request (x-fb-friendly-name, x-root-field-name, x-fb-lsd, ...) describe that query
# and make /api/v1/ refuse the call.
SESSION_HEADERS = re.compile(
    r"^(?:x-ig-app-id|x-asbd-id|x-csrftoken|x-ig-www-claim|x-requested-with"
    r"|x-web-device-id|x-web-session-id|x-instagram-ajax|accept-language)$",
    re.I,
)


@dataclass
class ProfileTemplate:
    url: str
    method: str
    headers: dict[str, str]
    body: str | None
    username: str
    # "page_request" (the page's own request, by username), "page_query" (by user id)
    # or "web_profile_info".
    kind: str
    # The user id the template asks for ("page_query" only).
    user_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "method": self.method,
            "headers": self.headers,
            "body": self.body,
            "kind": self.kind,
        }


def _clean_headers(headers: dict) -> dict[str, str]:
    return {
        str(key).lower(): str(value)[:2000]
        for key, value in (headers or {}).items()
        if not SKIP_HEADERS.match(str(key))
    }


def _swap_json(value, old: str, new: str) -> tuple[object, int]:
    if isinstance(value, str):
        return (new, 1) if value.lower() == old else (value, 0)
    if isinstance(value, int) and not isinstance(value, bool) and old.isdigit():
        # A user id written as a JSON number.
        return (int(new), 1) if str(value) == old and new.isdigit() else (value, 0)
    if isinstance(value, list):
        items = [_swap_json(item, old, new) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    if isinstance(value, dict):
        out, total = {}, 0
        for key, item in value.items():
            out[key], count = _swap_json(item, old, new)
            total += count
        return out, total
    return value, 0


def _swap_value(value: str, old: str, new: str) -> tuple[str, int]:
    """One form or query value: the username itself, or a JSON document holding it."""
    if value.lower() == old:
        return new, 1
    if value.startswith(("{", "[")):
        try:
            data = json.loads(value)
        except ValueError:
            return value, 0
        data, count = _swap_json(data, old, new)
        if count:
            return json.dumps(data, separators=(",", ":"), ensure_ascii=False), count
    return value, 0


def _swap_pairs(text: str, old: str, new: str) -> tuple[str, int]:
    pairs, total = [], 0
    for key, value in parse_qsl(text, keep_blank_values=True):
        value, count = _swap_value(value, old, new)
        pairs.append((key, value))
        total += count
    return urlencode(pairs, quote_via=quote, safe=""), total


def _swap(url: str, body: str | None, json_body: bool, old: str, new: str):
    parts = urlsplit(url)
    total = 0
    path_parts = []
    for part in parts.path.split("/"):
        if part.lower() == old:
            path_parts.append(new)
            total += 1
        else:
            path_parts.append(part)
    query, count = _swap_pairs(parts.query, old, new) if parts.query else ("", 0)
    total += count
    url = urlunsplit((parts.scheme, parts.netloc, "/".join(path_parts), query, ""))
    if body:
        body, count = _swap_value(body, old, new) if json_body else _swap_pairs(body, old, new)
        total += count
    return url, body, total


def retarget(
    template: ProfileTemplate, username: str, user_id: str | None = None
) -> tuple[str, str | None, int]:
    """(url, body, replacements) of the template for another user.

    A "page_query" template needs the user's id; without one nothing is replaced."""
    body = template.body
    json_body = bool(body) and (
        "json" in template.headers.get("content-type", "") or body.lstrip().startswith(("{", "["))
    )
    url, total = template.url, 0
    if template.user_id:
        if not (user_id and user_id.isdigit()):
            return url, body, 0
        url, body, total = _swap(url, body, json_body, template.user_id, user_id)
        if not total:
            return url, body, 0
    url, body, count = _swap(url, body, json_body, template.username.lower(), username)
    return url, body, total + count


def _names_id(text: str, user_id: str | None) -> bool:
    """The numeric user id appears as a value (quoted, a query value or a path part)."""
    if not user_id:
        return False
    return re.search(rf"(?<![\d]){re.escape(user_id)}(?![\d])", text) is not None


def learn(
    url: str,
    method: str,
    headers: dict,
    body: str | None,
    username: str,
    user_id: str | None,
) -> ProfileTemplate | None:
    """A template from the request that answered with the profile owner's data, or None."""
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in INSTAGRAM_HOSTS:
        return None
    if not DATA_PATH.match(parts.path) or method.upper() not in {"GET", "POST"}:
        return None
    if body is not None and len(body) > MAX_BODY:
        return None
    template = ProfileTemplate(
        url=url,
        method=method.upper(),
        headers=_clean_headers(headers),
        body=body,
        username=username.lower(),
        kind="page_request",
    )
    if _clean(template, username, user_id, None):
        return template
    # Asked by the user id: kept when the id (and the username) swap out cleanly.
    if user_id and user_id.isdigit():
        template.kind, template.user_id = "page_query", user_id
        if _clean(template, username, user_id, "9" * len(user_id)):
            return template
    return None


def _clean(template: ProfileTemplate, username: str, user_id, other_id) -> bool:
    """Retargeted to a made-up user, nothing of the real one is left in the request."""
    new_url, new_body, count = retarget(template, "x" * len(username), other_id)
    if not count:
        return False
    # Checked on the decoded text: "%22" before an id would hide it from a digit boundary.
    rest = unquote_plus(new_url + "\n" + (new_body or ""))
    # Still naming this user (by id, or another spelling of the username: escaped,
    # inside a blob) means the request cannot be retargeted cleanly.
    if _names_id(rest, user_id):
        return False
    return username.lower() not in rest.lower()


def borrowed(headers: dict, username: str) -> ProfileTemplate | None:
    """`web_profile_info` with the session headers of another request of the web app."""
    kept = {
        key: value for key, value in _clean_headers(headers).items() if SESSION_HEADERS.match(key)
    }
    if "x-ig-app-id" not in kept:
        return None
    kept.setdefault("x-requested-with", "XMLHttpRequest")
    kept["accept"] = "*/*"
    return ProfileTemplate(
        url=WEB_PROFILE_INFO.format(quote(username.lower())),
        method="GET",
        headers=kept,
        body=None,
        username=username.lower(),
        kind="web_profile_info",
    )


RATE_LIMIT_TEXT = re.compile(
    r"wait a few minutes|try again later|too many requests|rate.?limit|feedback_required"
    r"|please wait",
    re.I,
)
BLOCK_TEXT = re.compile(r"checkpoint_required|challenge_required|login_required", re.I)


def describe(answer) -> str:
    """Why a replayed request gave no profile, for the profile log: "HTTP 400",
    "network", "timeout"."""
    if not isinstance(answer, dict):
        return "no answer"
    if answer.get("error"):
        return str(answer["error"])[:40]
    return f"HTTP {answer.get('status')}"


def outcome(answer: dict) -> str:
    """What a replayed request's answer means: "ok", "limited" or "failed"."""
    if not isinstance(answer, dict) or answer.get("error"):
        return "failed"
    path = str(answer.get("path") or "")
    if path.startswith(("/accounts/login", "/challenge")):
        return "limited"
    status = answer.get("status")
    text = str(answer.get("text") or "")[:4000]
    if status == 429 or (status != 200 and RATE_LIMIT_TEXT.search(text)):
        return "limited"
    if status in {401, 403} or BLOCK_TEXT.search(text[:2000]):
        return "limited"
    if status != 200:
        return "failed"
    return "ok"


# The replay itself, run in the open Instagram tab.
REPLAY = """async ({url, method, headers, body, referrer}) => {
  if (!['instagram.com', 'www.instagram.com'].includes(location.hostname)) {
    return {error: 'no_instagram_tab'};
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, {
      method, headers, body: body ?? undefined, credentials: 'include',
      referrer, signal: controller.signal,
    });
    let text = '';
    try { text = await response.text(); } catch { text = ''; }
    return {
      status: response.status,
      path: new URL(response.url, location.href).pathname,
      text: text.slice(0, 4000000),
    };
  } catch (error) {
    return {error: error?.name === 'AbortError' ? 'timeout' : 'network'};
  } finally {
    clearTimeout(timer);
  }
}"""
