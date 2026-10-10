"""The Telegram bot on a real Postgres with the server migrations; Telegram is a stand-in
that records what the bot sends."""

import itertools

import psycopg
import pytest
from psycopg.rows import dict_row

from cloud_worker.bot import (
    BUTTON_FIND,
    BUTTON_STATUS,
    BUTTON_STOP,
    BUTTON_WRITE,
    Bot,
    TelegramError,
)

from .conftest import PROFILE, AsUser, add_user, params

CHAT = 111
OTHER_CHAT = 222
ADMIN_CHAT = 999


class FakeTelegram:
    def __init__(self):
        self.sent: list[dict] = []
        self.updates: list[dict] = []
        self.blocked: set[int] = set()
        # The chat as the user sees it: message id -> text (sent, edited, deleted).
        self.chat: dict[int, str] = {}
        self.edits = 0

    def call(self, method, **params):
        if method == "getMe":
            return {"username": "alf_test_bot"}
        if method == "getUpdates":
            updates, self.updates = self.updates, []
            return updates
        if method == "sendMessage":
            if params["chat_id"] in self.blocked:
                raise TelegramError("Forbidden: bot was blocked by the user", 403)
            params = {**params, "message_id": next(_ids)}
            self.sent.append(params)
            self.chat[params["message_id"]] = params["text"]
            return {"message_id": params["message_id"]}
        if method == "editMessageText":
            if params["message_id"] not in self.chat:
                raise TelegramError("Bad Request: message to edit not found", 400)
            if self.chat[params["message_id"]] == params["text"]:
                raise TelegramError("Bad Request: message is not modified", 400)
            self.chat[params["message_id"]] = params["text"]
            self.edits += 1
        if method == "deleteMessage":
            self.chat.pop(params["message_id"], None)
        return True

    def texts(self, chat=CHAT) -> list[str]:
        return [item["text"] for item in self.sent if item["chat_id"] == chat]


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


_ids = itertools.count(1)


def message(text, chat=CHAT, kind="private", username="artist_hunter"):
    return {
        "update_id": next(_ids),
        "message": {
            "message_id": next(_ids),
            "chat": {"id": chat, "type": kind},
            "from": {"username": username},
            "text": text,
        },
    }


def button(data, chat=CHAT, under=None):
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": "q",
            "data": data,
            "message": {"message_id": under, "chat": {"id": chat, "type": "private"}},
        },
    }


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
def bot(dsn, tg):
    item = Bot(lambda: psycopg.connect(dsn, autocommit=True, row_factory=dict_row), tg, Clock())
    item.start()
    return item


@pytest.fixture
def owner(dsn, db):
    return AsUser(dsn, add_user(db, "owner@example.com"))


def linked(bot, owner, chat=CHAT):
    code = owner.run("select public.tg_link_start() as r")[0]["r"]["code"]
    bot.handle(message(f"/start {code}", chat=chat))
    return code


def finished_job(owner, db, stage="completed", **counters):
    owner.put_session()
    job = owner.start(params(sources=["https://www.instagram.com/rapgoat.tv/"], target=30))
    db.execute(
        "update public.cloud_jobs set stage = %s, counters = %s::jsonb, finished_at = now(), "
        "error = %s where id = %s",
        (
            stage,
            psycopg.types.json.Jsonb({"found": 12, "added": 9, "updated": 3, **counters}),
            "Instagram требует вход" if stage == "failed" else None,
            job,
        ),
    )
    return job


def test_a_chat_is_linked_with_the_code_from_the_app(bot, tg, owner, dsn, db):
    status = owner.run("select public.tg_status() as r")[0]["r"]
    assert status["linked"] is False and status["bot"] == "alf_test_bot"
    started = owner.run("select public.tg_link_start() as r")[0]["r"]
    assert started["bot"] == "alf_test_bot" and len(started["code"]) == 10

    bot.handle(message(f"/start {started['code'].lower()}"))
    assert "привязан" in tg.texts()[-1]
    status = owner.run("select public.tg_status() as r")[0]["r"]
    assert status["linked"] is True and status["username"] == "artist_hunter"
    # A code works once.
    bot.handle(message(f"/start {started['code']}", chat=OTHER_CHAT))
    assert "не подошёл" in tg.texts(OTHER_CHAT)[-1]
    # The user cannot read codes or links behind the functions.
    with pytest.raises(psycopg.Error):
        owner.run("select * from public.tg_link_codes")
    with pytest.raises(psycopg.Error):
        owner.run("select * from public.tg_links")


def test_guessing_codes_stops_after_a_few_tries(bot, tg, owner, db):
    for _ in range(5):
        bot.handle(message("/start 0000000000", chat=OTHER_CHAT))
    code = owner.run("select public.tg_link_start() as r")[0]["r"]["code"]
    bot.handle(message(f"/start {code}", chat=OTHER_CHAT))
    assert "Слишком много попыток" in tg.texts(OTHER_CHAT)[-1]
    assert not db.execute("select 1 from public.tg_links").fetchall()


def test_an_expired_code_is_refused(bot, tg, owner, db):
    code = owner.run("select public.tg_link_start() as r")[0]["r"]["code"]
    db.execute("update public.tg_link_codes set expires_at = now() - interval '1 minute'")
    bot.handle(message(f"/start {code}"))
    assert "устарел" in tg.texts()[-1]
    assert not db.execute("select 1 from public.tg_links").fetchall()


def test_strangers_and_groups_get_nothing_done(bot, tg, owner, db):
    bot.handle(message("/find 10", chat=OTHER_CHAT))
    assert "не привязан" in tg.texts(OTHER_CHAT)[-1]
    linked(bot, owner)
    sent = len(tg.sent)
    bot.handle(message("/find 10", kind="group"))
    assert len(tg.sent) == sent
    assert not db.execute("select 1 from public.cloud_jobs").fetchall()


def last_job(db):
    return db.execute(
        "select params, profile_id, stage from public.cloud_jobs order by created_at desc limit 1"
    ).fetchone()


def buttons(tg) -> list[dict]:
    markup = tg.sent[-1].get("reply_markup") or {}
    return [item for row in markup.get("inline_keyboard", []) for item in row]


def test_find_asks_how_many_leads_for_the_only_account(bot, tg, owner, db):
    linked(bot, owner)
    bot.handle(message(BUTTON_FIND))
    assert "нет ни одного аккаунта" in tg.texts()[-1]

    first = finished_job(owner, db)
    bot.handle(message(BUTTON_FIND))
    assert "Аккаунт <b>Рабочий</b>. Сколько лидов найти?" in tg.texts()[-1]
    assert [item["text"] for item in buttons(tg)] == [
        "10",
        "25",
        "50",
        "100",
        "200",
        "✏️ Другое число",
    ]

    pick = button(f"cnt:{PROFILE}:25")
    bot.handle(pick)
    bot.handle(pick)  # Telegram delivered it twice: still one job.
    jobs = db.execute("select id from public.cloud_jobs where id <> %s", (first,)).fetchall()
    assert len(jobs) == 1
    job = last_job(db)
    assert job["stage"] == "queued" and job["profile_id"] == PROFILE
    assert job["params"]["target"] == 25
    assert job["params"]["sources"] == ["https://www.instagram.com/rapgoat.tv/"]
    assert "Запустил поиск: 25 лидов, 1 источников, аккаунт <b>Рабочий</b>" in tg.texts()[-1]

    # A number with the command skips the question.
    bot.handle(message("/find 120"))
    assert last_job(db)["params"]["target"] == 120


def test_find_asks_which_account_when_there_are_several(bot, tg, owner, db):
    linked(bot, owner)
    finished_job(owner, db)
    second = "b" * 32
    owner.run(
        "select public.cloud_session_put(%s, %s, %s::jsonb)",
        (
            second,
            "Второй",
            '{"cookies": [{"name": "sessionid", "value": "s2"}], '
            '"proxy": {"host": "1.2.3.4", "port": 8000}}',
        ),
    )
    bot.handle(message(BUTTON_FIND))
    assert tg.texts()[-1].startswith("С какого аккаунта Instagram искать?")
    assert [(item["text"], item["callback_data"]) for item in buttons(tg)] == [
        ("Второй 🛡", f"acc:{second}:0"),
        ("Рабочий", f"acc:{PROFILE}:0"),
    ]
    bot.handle(button(f"acc:{second}:0"))
    assert "Аккаунт <b>Второй</b>. Сколько лидов найти?" in tg.texts()[-1]
    bot.handle(button(f"own:{second}"))
    assert "Напишите числом" in tg.texts()[-1]
    bot.handle(message("70"))
    job = last_job(db)
    # The account chosen; sources and settings from the last search the app sent.
    assert job["profile_id"] == second and job["params"]["target"] == 70
    assert job["params"]["sources"] == ["https://www.instagram.com/rapgoat.tv/"]
    assert "аккаунт <b>Второй</b>" in tg.texts()[-1]

    # /find 30 with several accounts asks the account and keeps the number.
    bot.handle(message("/find 30"))
    assert {item["callback_data"] for item in buttons(tg)} == {
        f"acc:{second}:30",
        f"acc:{PROFILE}:30",
    }
    # A number typed when nobody asked for it starts nothing.
    jobs = db.execute("select count(*) as n from public.cloud_jobs").fetchone()["n"]
    bot.handle(message("15"))
    assert db.execute("select count(*) as n from public.cloud_jobs").fetchone()["n"] == jobs
    # An account that is not this user's is refused.
    bot.handle(button(f"cnt:{'c' * 32}:10"))
    assert "больше нет" in tg.texts()[-1]


def test_the_servers_checks_reach_the_chat(bot, tg, owner, db):
    linked(bot, owner)
    finished_job(owner, db)
    bot.handle(message("/find 10"))
    bot.handle(message("/find 10"))
    bot.handle(message("/find 10"))
    assert tg.texts()[-1].startswith("Не запустил: Уже идёт 2 облачных задачи")
    # A blocked account cannot start anything even with a linked chat.
    db.execute("update public.profiles set blocked = true")
    bot.handle(message("/find 10"))
    assert "не привязан" in tg.texts()[-1]


def test_status_and_stop(bot, tg, owner, db):
    linked(bot, owner)
    bot.handle(message(BUTTON_STATUS))
    assert "ещё не было" in tg.texts()[-1]
    finished_job(owner, db)
    bot.handle(message(BUTTON_STATUS))
    shown = tg.chat[max(tg.chat)]
    assert "Сейчас ничего не ищу" in shown and "найдено 12 из 30" in shown

    bot.handle(message("/find 40"))
    db.execute(
        "update public.cloud_jobs set stage = 'collecting', progress = %s::jsonb "
        "where stage = 'queued'",
        (
            psycopg.types.json.Jsonb(
                {"found": 7, "target": 40, "candidates": 55, "sources_done": 1, "step": "profile"}
            ),
        ),
    )
    bot.handle(button("status"))
    text = tg.chat[tg.sent[-1]["message_id"]]
    assert "найдено <b>7</b> из 40" in text and "Сейчас: профиль" in text

    bot.handle(message(BUTTON_STOP))
    assert "Останавливаю" in tg.texts()[-1]
    assert db.execute(
        "select cancel_requested from public.cloud_jobs where stage = 'collecting'"
    ).fetchone()["cancel_requested"]
    bot.handle(message("/stop"))
    assert tg.texts()[-1] == "Сейчас ничего не ищу."


def progress(db, found):
    db.execute(
        "update public.cloud_jobs set stage = 'collecting', progress = %s::jsonb "
        "where stage in ('queued', 'collecting')",
        (psycopg.types.json.Jsonb({"found": found, "target": 40, "step": "profile"}),),
    )


def test_status_is_one_message_that_follows_the_search(bot, tg, owner, db):
    linked(bot, owner)
    finished_job(owner, db)
    bot.tick()  # its «закончен» is sent before this search starts
    bot.handle(message("/find 40"))
    card = tg.sent[-1]["message_id"]
    assert "обновляется само" in tg.chat[card]

    # The launch message itself follows the search, with no new messages.
    sent = len(tg.sent)
    progress(db, 3)
    bot.tick()
    assert "найдено <b>3</b> из 40" in tg.chat[card]
    edits = tg.edits
    bot.tick()
    assert tg.edits == edits  # nothing new: not touched
    progress(db, 5)
    bot.tick()
    assert "найдено <b>5</b> из 40" in tg.chat[card] and len(tg.sent) == sent

    # «Статус» pressed again and again: the tap is removed, the same message is rewritten.
    for _ in range(3):
        tap = message(BUTTON_STATUS)
        bot.handle(tap)
        assert tap["message"]["message_id"] not in tg.chat
    assert len(tg.sent) == sent and "найдено <b>5</b>" in tg.chat[card]

    # «Остановить» under the status: shown in the status itself.
    bot.handle(button("stop", under=card))
    assert "Останавливаю…" in tg.chat[card] and len(tg.sent) == sent
    db.execute("update public.cloud_jobs set stage = 'cancelled', finished_at = now()")
    bot.tick()
    assert "Сейчас ничего не ищу" in tg.chat[card] and "Поиск остановлен" in tg.chat[card]
    edits = tg.edits
    db.execute("update public.cloud_jobs set progress = '{}'::jsonb")
    bot.tick()
    assert tg.edits == edits  # an ended search is no longer followed

    # Other messages came after it: the old status goes, a new one is at the bottom.
    bot.handle(message("/leads"))
    bot.handle(message(BUTTON_STATUS))
    assert card not in tg.chat
    assert "Сейчас ничего не ищу" in tg.chat[tg.sent[-1]["message_id"]]


def test_leads_show_what_went_to_the_crm(bot, tg, owner, db):
    linked(bot, owner)
    job = finished_job(owner, db)
    db.execute(
        "insert into public.cloud_candidates (job_id, owner_id, username, category, confidence, "
        "outcome, profile) values (%s, %s, 'jay.carter', 'ARTIST', 91, 'added', %s::jsonb), "
        "(%s, %s, 'nobody', 'OTHER', 40, 'filtered', '{}')",
        (
            job,
            owner.user_id,
            psycopg.types.json.Jsonb({"followers": 4200, "emails": ["jay@music.com"]}),
            job,
            owner.user_id,
        ),
    )
    bot.handle(message("/leads"))
    text = tg.texts()[-1]
    assert '<a href="https://www.instagram.com/jay.carter/">@jay.carter</a>' in text
    assert "4 200 подп." in text and "артист 91%" in text and "jay@music.com" in text
    assert "nobody" not in text


def test_ended_jobs_are_announced_once_and_failures_reach_admins(bot, tg, owner, dsn, db):
    linked(bot, owner)
    admin = AsUser(dsn, add_user(db, "admin@example.com"))
    db.execute("update public.profiles set role = 'admin' where id = %s", (admin.user_id,))
    linked(bot, admin, chat=ADMIN_CHAT)
    old = finished_job(owner, db)
    db.execute(
        "update public.cloud_jobs set finished_at = now() - interval '2 days' where id = %s", (old,)
    )
    sent = len(tg.sent)
    bot.tick()
    assert len(tg.sent) == sent  # an old job is not news

    db.execute("update public.cloud_jobs set notified = null")
    db.execute("update public.cloud_jobs set finished_at = now()")
    bot.tick()
    assert tg.texts()[-1].startswith("✅ Поиск закончен: найдено 12 из 30")
    assert buttons(tg)[0]["callback_data"] == f"cnt:{PROFILE}:30"
    bot.tick()
    assert len(tg.sent) == sent + 1

    failed = finished_job(owner, db, stage="failed")
    bot.tick()
    assert "❌ Поиск прервался: Instagram требует вход" in tg.texts()[-1]
    alert = tg.texts(ADMIN_CHAT)[-1]
    assert "owner@example.com" in alert and failed in alert

    # Notifications off: the owner hears nothing, admins still do.
    bot.handle(message("/notify"))
    assert "выключены" in tg.texts()[-1]
    count = len(tg.texts())
    finished_job(owner, db, stage="failed")
    bot.tick()
    assert len(tg.texts()) == count
    assert len(tg.texts(ADMIN_CHAT)) >= 2


def test_a_blocked_bot_does_not_stop_the_rest(bot, tg, owner, db):
    linked(bot, owner)
    tg.blocked.add(CHAT)
    finished_job(owner, db)
    bot.tick()
    tg.blocked.clear()
    bot.tick()
    assert not any("Поиск закончен" in text for text in tg.texts())  # marked, not retried
    assert (
        db.execute("select notified from public.cloud_jobs").fetchone()["notified"] == "completed"
    )


def test_a_stalled_parser_is_reported_to_admins_once(bot, tg, dsn, db, owner):
    admin = AsUser(dsn, add_user(db, "admin@example.com"))
    db.execute("update public.profiles set role = 'admin' where id = %s", (admin.user_id,))
    linked(bot, admin, chat=ADMIN_CHAT)
    owner.put_session()
    job = owner.start(params())
    db.execute(
        "update public.cloud_jobs set stage = 'collecting', "
        "locked_until = now() - interval '10 minutes'"
    )
    bot.tick()
    bot.tick()
    alerts = [text for text in tg.texts(ADMIN_CHAT) if text.startswith("⚠️ Облачный парсер")]
    assert len(alerts) == 1 and job in alerts[0] and "перестал продлевать" in alerts[0]


def test_unlink_from_the_chat_and_from_the_app(bot, tg, owner, db):
    linked(bot, owner)
    bot.handle(message("/unlink"))
    assert "отвязан" in tg.texts()[-1]
    assert not db.execute("select 1 from public.tg_links").fetchall()
    linked(bot, owner)
    owner.run("select public.tg_unlink()")
    assert owner.run("select public.tg_status() as r")[0]["r"]["linked"] is False
    bot.handle(message(BUTTON_STOP))
    assert "не привязан" in tg.texts()[-1]


def test_write_sends_to_the_new_leads_nobody_wrote_to(bot, tg, owner, db):
    linked(bot, owner)
    bot.handle(message(BUTTON_WRITE))
    assert "Сообщения ещё не заданы" in tg.texts()[-1]

    scout = finished_job(owner, db)
    db.execute(
        "insert into public.cloud_candidates (job_id, owner_id, username, outcome) values "
        "(%s, %s, 'jay.carter', 'added'), (%s, %s, 'kid.vibes', 'added'), "
        "(%s, %s, 'old.friend', 'updated')",
        (scout, owner.user_id) * 3,
    )
    proxy = {"scheme": "http", "host": "1.2.3.4", "port": 8000}
    owner.put_session(proxy=proxy)
    # The app started one outreach: its messages and pace are what the bot sends.
    first = owner.start(
        {
            "kind": "outreach",
            "profile_id": PROFILE,
            "usernames": ["someone"],
            "messages": ["Привет, {{username}}!"],
            "settings": {"outreach_daily_limit_per_sender": 15},
        }
    )
    db.execute("update public.cloud_jobs set stage = 'completed' where id = %s", (first,))
    bot.handle(message(BUTTON_WRITE))
    assert "Ждут сообщения: 2. Скольким написать?" in tg.texts()[-1]
    assert [item["callback_data"] for item in buttons(tg)] == [
        f"wcn:{PROFILE}:2",
        f"wow:{PROFILE}",
    ]
    bot.handle(button(f"wcn:{PROFILE}:2"))
    assert "Запустил рассылку: 2 лидам" in tg.texts()[-1]
    job = db.execute(
        "select id, params from public.cloud_jobs where kind = 'outreach' and stage = 'queued'"
    ).fetchone()
    assert sorted(job["params"]["usernames"]) == ["jay.carter", "kid.vibes"]
    assert job["params"]["messages"] == ["Привет, {{username}}!"]
    assert job["params"]["settings"] == {"outreach_daily_limit_per_sender": 15}
    # Nobody is queued twice.
    bot.handle(message("/write 5"))
    assert "нет" in tg.texts()[-1]

    # The status follows the outreach in the same message.
    card = tg.sent[-2]["message_id"]
    db.execute(
        "update public.cloud_jobs set stage = 'collecting', progress = %s::jsonb where id = %s",
        (
            psycopg.types.json.Jsonb({"total": 2, "sent": 1, "queued": 1, "skipped": 0}),
            job["id"],
        ),
    )
    bot.handle(button("status", under=card))
    assert "✉️ Пишу: отправлено <b>1</b> из 2" in tg.chat[card]
    assert "после паузы" in tg.chat[card]

    db.execute(
        "update public.cloud_jobs set stage = 'completed', finished_at = now(), "
        'counters = \'{"total": 2, "sent": 2, "failed": 0, "skipped": 0}\' where id = %s',
        (job["id"],),
    )
    bot.tick()
    assert any(text.startswith("✅ Рассылка закончена: отправлено 2 из 2") for text in tg.texts())
    # «Найти ещё» keeps taking the parser's sources, not the outreach's list.
    bot.handle(message("/find 10"))
    latest = db.execute(
        "select params from public.cloud_jobs where kind = 'scout' order by created_at desc"
    ).fetchone()
    assert latest["params"]["target"] == 10 and latest["params"]["sources"]


def test_only_accounts_with_a_proxy_write(bot, tg, owner, db):
    linked(bot, owner)
    scout = finished_job(owner, db)  # its account has no proxy
    db.execute(
        "insert into public.cloud_candidates (job_id, owner_id, username, outcome) "
        "values (%s, %s, 'jay.carter', 'added')",
        (scout, owner.user_id),
    )
    # An earlier outreach of the app (sent when the account still had its proxy).
    db.execute(
        "insert into public.cloud_jobs (owner_id, request_id, profile_id, params, kind, stage) "
        "values (%s, 'app-click-0001', %s, %s, 'outreach', 'completed')",
        (owner.user_id, PROFILE, psycopg.types.json.Jsonb({"messages": ["Hi"]})),
    )
    bot.handle(message(BUTTON_WRITE))
    assert "только с аккаунта с прокси" in tg.texts()[-1]
    assert not db.execute(
        "select 1 from public.cloud_jobs where kind = 'outreach' and stage = 'queued'"
    ).fetchall()
