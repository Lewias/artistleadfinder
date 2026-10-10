"""«Spotify»: small artists from playlists, search and «Fans also like», kept only when their
Spotify profile links an Instagram; they are then written to through our Direct outreach.

The search runs on its own thread with its own hidden Chromium (client.SpotifyWeb). An
artist is asked once; the answer stays in spotify_artists, so later runs go further.
"""

import logging
import random
import threading
from collections import deque
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from ..errors import UserError
from ..models import CrmContact, Lead, Setting, SpotifyArtist, SpotifyPlaylist, SpotifyRun, utcnow
from . import parse
from .client import SpotifyError, SpotifyRateLimited, SpotifyWeb

log = logging.getLogger(__name__)

SETTINGS_KEY = "spotify"
DEFAULTS = {
    "min_listeners": 0,
    "max_listeners": 10000,
    # Playlist searches that lead to new and unsigned artists rather than to the charts.
    "queries": [
        "underground rap",
        "new rap artists",
        "unsigned rappers",
        "upcoming artists",
        "small artists",
        "independent hip hop",
        "new drill",
        "rage rap",
    ],
    "seeds": [],
    "use_playlists": True,
    "use_search": True,
    "use_related": True,
    "release_days": 0,
    "target": 50,
}
MAX_QUERIES = 40
MAX_SEEDS = 200
MAX_TARGET = 1000
PLAYLISTS_PER_QUERY = 5
SEARCH_PAGE = 30
SEARCH_PAGES = 5
PLAYLIST_PAGE = 100
PLAYLIST_TRACKS = 500
# A playlist is taken again after this long: new tracks get added.
PLAYLIST_AGAIN = timedelta(days=14)
# Artists without an Instagram or out of the range are asked again after this long.
ARTIST_AGAIN = timedelta(days=30)
# «Fans also like» of artists up to this many times the upper limit are small artists too.
RELATED_REACH = 3
MAX_NEAR = 3000
# Small artists of earlier runs whose «Fans also like» a new run goes through first.
RESUME_ARTISTS = 300
VIEWS = {
    "new": ("found",),
    "found": ("found",),
    "skipped": ("out_of_range", "no_instagram", "old_release", "known"),
}
STATUS_TEXT = {
    "found": "Подходит",
    "out_of_range": "Слушатели вне диапазона",
    "no_instagram": "Нет Instagram",
    "old_release": "Давно не выпускал релизы",
    "known": "Уже есть в CRM или ему писали",
}


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        return _aware(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


def _number(params: dict, key: str, low: int, high: int, label: str) -> int:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise UserError(f"{label}: целое число от {low} до {high}.")
    return value


def clean_settings(params: dict, current: dict) -> dict:
    merged = {**current, **{key: params[key] for key in DEFAULTS if key in params}}
    result = dict(merged)
    result["min_listeners"] = _number(merged, "min_listeners", 0, 1_000_000_000, "Слушателей от")
    result["max_listeners"] = _number(merged, "max_listeners", 0, 1_000_000_000, "Слушателей до")
    if result["min_listeners"] > result["max_listeners"]:
        raise UserError("Нижняя граница слушателей больше верхней.")
    result["release_days"] = _number(merged, "release_days", 0, 3650, "Свежесть релиза")
    result["target"] = _number(merged, "target", 1, MAX_TARGET, "Сколько найти")
    queries = merged.get("queries")
    if not isinstance(queries, list):
        raise UserError("Фразы поиска — список строк.")
    result["queries"] = list(
        dict.fromkeys(str(item).strip()[:100] for item in queries if str(item).strip())
    )
    if len(result["queries"]) > MAX_QUERIES:
        raise UserError(f"Не больше {MAX_QUERIES} фраз поиска.")
    seeds = merged.get("seeds")
    if not isinstance(seeds, list):
        raise UserError("Ссылки — список строк.")
    result["seeds"] = []
    for item in seeds:
        text = str(item).strip()
        if not text:
            continue
        if parse.parse_link(text) is None:
            raise UserError(f"Не похоже на ссылку на артиста или плейлист Spotify: {text[:60]}")
        if text not in result["seeds"]:
            result["seeds"].append(text)
    if len(result["seeds"]) > MAX_SEEDS:
        raise UserError(f"Не больше {MAX_SEEDS} ссылок.")
    for key in ("use_playlists", "use_search", "use_related"):
        result[key] = bool(merged.get(key))
    return result


def row_dict(row: SpotifyArtist) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "url": f"https://open.spotify.com/artist/{row.id}",
        "listeners": row.listeners,
        "followers": row.followers,
        "instagram": row.instagram,
        "release_name": row.release_name,
        "release_date": _iso(row.release_date),
        "city": row.city,
        "country": row.country,
        "status": row.status,
        "status_text": STATUS_TEXT.get(row.status, row.status),
        "source": row.source,
        "checked_at": _iso(row.checked_at),
        "outreach_at": _iso(row.outreach_at),
        "crm_at": _iso(row.crm_at),
    }


def run_dict(run: SpotifyRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "target": run.target,
        "checked": run.checked,
        "found": run.found,
        "step": run.step,
        "message": run.message,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }


class SpotifyService:
    def __init__(
        self,
        sessions,
        add_usernames: Callable[[list[str]], int],
        crm_merge: Callable[[str, list[dict]], dict],
        web_factory: Callable[[], SpotifyWeb] = SpotifyWeb,
        pause: Callable[[threading.Event], None] | None = None,
        now: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.add_usernames = add_usernames
        self.crm_merge = crm_merge
        self.web_factory = web_factory
        # Between two requests to Spotify, like a person clicking through the player.
        self.pause = pause or (lambda stop: stop.wait(random.uniform(0.8, 2.0)))
        self.now = now
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.recover()

    # ---------- RPC ----------

    def call(self, method: str, params: dict) -> dict:
        handlers = {
            "spotify.state": lambda p: self.state(),
            "spotify.settings": self.update_settings,
            "spotify.start": self.start,
            "spotify.stop": lambda p: self.stop(),
            "spotify.results": self.results,
            "spotify.to_outreach": self.to_outreach,
            "spotify.to_crm": self.to_crm,
        }
        handler = handlers.get(method)
        if handler is None:
            raise UserError("Неизвестная команда Spotify.")
        return handler(params or {})

    def recover(self) -> None:
        """A run cut off by closing the app is not running any more."""
        with self.sessions.begin() as session:
            for run in session.scalars(select(SpotifyRun).where(SpotifyRun.status == "running")):
                run.status = "interrupted"
                run.message = (
                    "Поиск прервался: приложение закрылось. Запустите снова — проверенные "
                    "артисты не проверяются заново."
                )
                run.finished_at = self.now()

    def shutdown(self) -> None:
        self.stopping.set()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    # ---------- settings and state ----------

    def settings(self) -> dict:
        with self.sessions() as session:
            row = session.get(Setting, SETTINGS_KEY)
            stored = row.value if row is not None and isinstance(row.value, dict) else {}
        return {**DEFAULTS, **{key: stored[key] for key in DEFAULTS if key in stored}}

    def update_settings(self, params: dict) -> dict:
        cleaned = clean_settings(params, self.settings())
        with self.sessions.begin() as session:
            row = session.get(Setting, SETTINGS_KEY)
            if row is None:
                session.add(Setting(key=SETTINGS_KEY, value=cleaned))
            else:
                row.value = cleaned
        return self.state()

    def state(self) -> dict:
        with self.sessions() as session:
            run = session.scalar(select(SpotifyRun).order_by(SpotifyRun.id.desc()).limit(1))
            counts = dict(
                session.execute(
                    select(SpotifyArtist.status, func.count()).group_by(SpotifyArtist.status)
                ).all()
            )
            fresh = session.scalar(
                select(func.count())
                .select_from(SpotifyArtist)
                .where(SpotifyArtist.status == "found", SpotifyArtist.outreach_at.is_(None))
            )
            return {
                "running": self.running,
                "settings": self.settings(),
                "run": run_dict(run),
                "totals": {
                    "found": counts.get("found", 0),
                    "new": fresh or 0,
                    "checked": sum(counts.values()),
                },
            }

    # ---------- the run ----------

    def start(self, params: dict) -> dict:
        settings = self.settings()
        if "target" in params:
            settings = clean_settings({"target": params["target"]}, settings)
            self.update_settings({"target": settings["target"]})
        has_terms = settings["queries"] and (settings["use_playlists"] or settings["use_search"])
        if not (has_terms or settings["seeds"]):
            raise UserError("Добавьте фразы поиска или ссылки на артистов и плейлисты.")
        with self.lock:
            if self.running:
                raise UserError("Поиск в Spotify уже идёт.")
            with self.sessions.begin() as session:
                run = SpotifyRun(
                    target=settings["target"], step="Запускаю Chromium", started_at=self.now()
                )
                session.add(run)
                session.flush()
                run_id = run.id
            self.stopping.clear()
            self.thread = threading.Thread(
                target=self._work, args=(run_id, settings), name="spotify-search", daemon=True
            )
            self.thread.start()
        return self.state()

    def stop(self) -> dict:
        self.stopping.set()
        return self.state()

    def _update(self, run_id: int, **fields) -> None:
        with self.sessions.begin() as session:
            run = session.get(SpotifyRun, run_id)
            for key, value in fields.items():
                setattr(run, key, value)

    def _work(self, run_id: int, settings: dict) -> None:
        web = self.web_factory()
        status, message = "done", ""
        try:
            web.start()
            status, message = Search(self, web, run_id, settings).run()
        except SpotifyRateLimited:
            status = "failed"
            message = (
                "Spotify временно ограничил запросы. Продолжите через 15–30 минут — "
                "уже проверенные артисты не проверяются заново."
            )
        except SpotifyError as error:
            status, message = "failed", str(error)
        except Exception:  # noqa: BLE001 - the run ends with a message instead of a dead thread
            log.exception("spotify_search_failed")
            status = "failed"
            message = "Поиск в Spotify остановился из-за ошибки. Подробности в логе."
        finally:
            web.close()
            self._update(run_id, status=status, message=message, step="", finished_at=self.now())

    # ---------- results ----------

    def results(self, params: dict) -> dict:
        view = str(params.get("view") or "new")
        if view not in VIEWS:
            raise UserError("Неизвестный список.")
        limit = min(max(int(params.get("limit") or 100), 1), 500)
        offset = max(int(params.get("offset") or 0), 0)
        query = select(SpotifyArtist).where(SpotifyArtist.status.in_(VIEWS[view]))
        if view == "new":
            query = query.where(SpotifyArtist.outreach_at.is_(None))
        with self.sessions() as session:
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(
                query.order_by(SpotifyArtist.checked_at.desc(), SpotifyArtist.id)
                .limit(limit)
                .offset(offset)
            )
            return {"rows": [row_dict(row) for row in rows], "total": total or 0}

    def _chosen(self, session, params: dict) -> list[SpotifyArtist]:
        query = select(SpotifyArtist).where(
            SpotifyArtist.status == "found", SpotifyArtist.instagram.is_not(None)
        )
        if params.get("all_new"):
            query = query.where(SpotifyArtist.outreach_at.is_(None))
        else:
            ids = params.get("ids")
            if not isinstance(ids, list) or not ids:
                raise UserError("Выберите артистов.")
            query = query.where(SpotifyArtist.id.in_([str(item) for item in ids][:5000]))
        return list(session.scalars(query.order_by(SpotifyArtist.checked_at)))

    def to_outreach(self, params: dict) -> dict:
        """Their Instagrams go to the «Рассылка» list; nothing is sent from here."""
        with self.sessions() as session:
            names = [row.instagram for row in self._chosen(session, params)]
        if not names:
            raise UserError("Нет новых артистов для рассылки.")
        added = self.add_usernames(names)
        with self.sessions.begin() as session:
            for row in session.scalars(
                select(SpotifyArtist).where(SpotifyArtist.instagram.in_(names))
            ):
                if row.status == "found" and row.outreach_at is None:
                    row.outreach_at = self.now()
        return {"added": added, "count": len(names)}

    def to_crm(self, params: dict) -> dict:
        with self.sessions() as session:
            rows = [row_dict(row) for row in self._chosen(session, params)]
        if not rows:
            raise UserError("Нет артистов для CRM.")
        items = []
        for row in rows:
            facts = [f"Spotify: {row['listeners']:,} слушателей в месяц".replace(",", " ")]
            if row["release_name"]:
                when = row["release_date"] or ""
                facts.append(f"последний релиз «{row['release_name']}» {when}".strip())
            if row["city"]:
                facts.append(f"слушают в {row['city']}")
            items.append(
                {
                    "name": row["name"],
                    "channels": [{"kind": "instagram", "value": row["instagram"]}],
                    "notes": "; ".join(facts) + f"\n{row['url']}",
                }
            )
        result = self.crm_merge("instagram", items)
        with self.sessions.begin() as session:
            for row in session.scalars(
                select(SpotifyArtist).where(SpotifyArtist.id.in_([row["id"] for row in rows]))
            ):
                row.crm_at = self.now()
        return result


class Search:
    """One run: sources → artists, each asked once, until the target is found."""

    def __init__(self, service: SpotifyService, web: SpotifyWeb, run_id: int, settings: dict):
        self.service = service
        self.web = web
        self.run_id = run_id
        self.settings = settings
        self.checked = 0
        self.found = 0
        self.seen: set[str] = set()
        # «Fans also like» of small artists: asked before anything from the sources.
        self.near: deque[tuple[str, str]] = deque()
        self.known = self._known_instagrams()
        if settings["use_related"]:
            self._resume_near()

    def _resume_near(self) -> None:
        """«Fans also like» of small artists met before: the last runs stopped somewhere
        inside them, and that is where most small artists are."""
        reach = self.settings["max_listeners"] * RELATED_REACH
        with self.service.sessions() as session:
            rows = session.execute(
                select(SpotifyArtist.name, SpotifyArtist.related)
                .where(SpotifyArtist.listeners <= reach)
                .order_by(SpotifyArtist.checked_at.desc())
                .limit(RESUME_ARTISTS)
            ).all()
        queued: set[str] = set()
        for name, related in rows:
            for artist_id in related or []:
                if artist_id not in queued and len(self.near) < MAX_NEAR:
                    queued.add(artist_id)
                    self.near.append((artist_id, f"похож на {name}"))

    def _known_instagrams(self) -> set[str]:
        """Instagrams already written to, in the CRM or found before: not new leads."""
        with self.service.sessions() as session:
            known = set(
                session.scalars(
                    select(Lead.username).where(
                        Lead.platform == "instagram",
                        (Lead.last_contacted_at.is_not(None)) | (Lead.do_not_contact.is_(True)),
                    )
                )
            )
            known |= set(
                session.scalars(
                    select(SpotifyArtist.instagram).where(
                        SpotifyArtist.status == "found", SpotifyArtist.instagram.is_not(None)
                    )
                )
            )
            for channels in session.scalars(
                select(CrmContact.channels).where(
                    CrmContact.crm == "instagram", CrmContact.deleted_at.is_(None)
                )
            ):
                known |= {
                    str(item.get("value")).lower()
                    for item in channels or []
                    if isinstance(item, dict) and item.get("kind") == "instagram"
                }
            return known

    def _step(self, text: str) -> None:
        self.service._update(self.run_id, step=text[:300], checked=self.checked, found=self.found)

    def _wait(self) -> bool:
        """Pauses between requests; False once the user pressed «Остановить»."""
        self.service.pause(self.service.stopping)
        return not self.service.stopping.is_set()

    def run(self) -> tuple[str, str]:
        target = self.settings["target"]
        sources = self._sources()
        while self.found < target:
            if self.service.stopping.is_set():
                return "stopped", f"Остановлено. Найдено {self.found}."
            if self.near:
                artist_id, source = self.near.popleft()
            else:
                item = next(sources, None)
                if item is None:
                    return "done", (
                        f"Источники закончились, найдено {self.found} из {target}. Добавьте фразы "
                        "поиска или ссылки на плейлисты."
                    )
                artist_id, source = item
            if artist_id in self.seen or self._asked_before(artist_id):
                self.seen.add(artist_id)
                continue
            self.seen.add(artist_id)
            if not self._wait():
                continue
            self._check(artist_id, source)
        return "done", f"Найдено {self.found} артистов с Instagram."

    def _asked_before(self, artist_id: str) -> bool:
        with self.service.sessions() as session:
            row = session.get(SpotifyArtist, artist_id)
            if row is None:
                return False
            if row.status in ("found", "known"):
                return True
            return _aware(row.checked_at) > self.service.now() - ARTIST_AGAIN

    def _status(self, facts: parse.ArtistFacts) -> str:
        settings = self.settings
        if not settings["min_listeners"] <= facts.listeners <= settings["max_listeners"]:
            return "out_of_range"
        days = settings["release_days"]
        if days and (
            facts.release_date is None
            or facts.release_date < self.service.now().date() - timedelta(days=days)
        ):
            return "old_release"
        if not facts.instagram:
            return "no_instagram"
        if facts.instagram in self.known:
            return "known"
        return "found"

    def _check(self, artist_id: str, source: str) -> None:
        facts = parse.artist_facts(self.web.artist(artist_id))
        self.checked += 1
        if facts is None:
            self._step(f"Проверено {self.checked}")
            return
        status = self._status(facts)
        with self.service.sessions.begin() as session:
            row = session.get(SpotifyArtist, artist_id) or SpotifyArtist(id=artist_id)
            row.name, row.listeners, row.followers = facts.name, facts.listeners, facts.followers
            row.instagram, row.bio = facts.instagram, facts.bio
            row.release_name, row.release_date = facts.release_name, facts.release_date
            row.city, row.country = facts.city, facts.country
            row.status, row.source, row.run_id = status, source[:240], self.run_id
            row.related = facts.related[:40]
            row.checked_at = self.service.now()
            session.add(row)
        if status == "found":
            self.found += 1
            self.known.add(facts.instagram)
        if (
            self.settings["use_related"]
            and facts.listeners <= self.settings["max_listeners"] * RELATED_REACH
            and len(self.near) < MAX_NEAR
        ):
            for related in facts.related:
                if related not in self.seen:
                    self.near.append((related, f"похож на {facts.name}"))
        self._step(f"Проверяю артистов: {facts.name}")

    # ---------- sources ----------

    def _sources(self) -> Iterator[tuple[str, str]]:
        settings = self.settings
        for seed in settings["seeds"]:
            link = parse.parse_link(seed)
            if link and link[0] == "artist":
                yield link[1], "ваша ссылка"
            elif link:
                yield from self._playlist(link[1], "", again=True)
        for term in settings["queries"]:
            if settings["use_playlists"]:
                yield from self._playlists_for(term)
            if settings["use_search"] and self._wait():
                self._step(f"Ищу артистов: «{term}»")
                for artist_id in parse.search_artists(self.web.search("artists", term)):
                    yield artist_id, f"поиск «{term}»"

    def _playlists_for(self, term: str) -> Iterator[tuple[str, str]]:
        taken = 0
        for page in range(SEARCH_PAGES):
            if taken >= PLAYLISTS_PER_QUERY or not self._wait():
                return
            self._step(f"Ищу плейлисты: «{term}»")
            found = parse.search_playlists(
                self.web.search("playlists", term, offset=page * SEARCH_PAGE, limit=SEARCH_PAGE)
            )
            if not found:
                return
            for playlist in found:
                if taken >= PLAYLISTS_PER_QUERY:
                    return
                if self._scanned_recently(playlist.id):
                    continue
                taken += 1
                yield from self._playlist(playlist.id, playlist.name)

    def _scanned_recently(self, playlist_id: str) -> bool:
        with self.service.sessions() as session:
            row = session.get(SpotifyPlaylist, playlist_id)
            return row is not None and _aware(row.scanned_at) > self.service.now() - PLAYLIST_AGAIN

    def _playlist(
        self, playlist_id: str, name: str, again: bool = False
    ) -> Iterator[tuple[str, str]]:
        """Artists of a playlist page by page; a link the user gave is taken every run."""
        if not again and self._scanned_recently(playlist_id):
            return
        artists = 0
        for offset in range(0, PLAYLIST_TRACKS, PLAYLIST_PAGE):
            if not self._wait():
                return
            page = parse.playlist_page(
                self.web.playlist(playlist_id, offset=offset, limit=PLAYLIST_PAGE)
            )
            if page is None:
                break
            name = name or page.name
            self._step(f"Плейлист «{name}»")
            artists += len(page.artists)
            for artist_id in page.artists:
                yield artist_id, f"плейлист «{name}»"
            if offset + PLAYLIST_PAGE >= page.total:
                break
        with self.service.sessions.begin() as session:
            row = session.get(SpotifyPlaylist, playlist_id) or SpotifyPlaylist(id=playlist_id)
            row.name, row.artists, row.scanned_at = name[:240], artists, self.service.now()
            session.add(row)
