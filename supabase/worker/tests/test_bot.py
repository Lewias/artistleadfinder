"""The Telegram bot on a real Postgres with the server migrations; Telegram is a stand-in
that records what the bot sends, and the app is the account's own database calls."""

import itertools

import psycopg
import pytest
from psycopg.rows import dict_row

from telegram_bot.bot import APP_CLOSED, NO_ANSWER, Bot, TelegramError, pieces

from .conftest import AsUser, add_user

CHAT = 111
OTHER_CHAT = 222


class FakeTelegram:
    def __init__(self):
        self.sent: list[dict] = []
        self.actions: list[tuple[int, str]] = []
        self.updates: list[dict] = []
        self.blocked: set[int] = set()

    def call(self, method, **params):
        if method == "getMe":
            return {"username": "alf_test_bot"}
        if method == "getUpdates":
            updates, self.updates = self.updates, []
            return updates
        if method == "sendMessage":
            if params["chat_id"] in self.blocked:
                raise TelegramError("Forbidden: bot was blocked by the user", 403)
            self.sent.append(params)
            return {"message_id": next(_ids)}
        if method == "sendChatAction":
            self.actions.append((params["chat_id"], params["action"]))
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


# ---------- linking ----------


def test_a_chat_is_linked_with_the_code_from_the_app(bot, tg, owner, dsn, db):
    status = owner.run("select public.tg_status() as r")[0]["r"]
    assert status["linked"] is False and status["bot"] == "alf_test_bot"
    started = owner.run("select public.tg_link_start() as r")[0]["r"]
    assert started["bot"] == "alf_test_bot" and len(started["code"]) == 10

    bot.handle(message(f"/start {started['code'].lower()}"))
    assert "привязан" in tg.texts()[-1]
    # The button menu of the old bot is taken away.
    assert tg.sent[-1]["reply_markup"] == {"remove_keyboard": True}
    status = owner.run("select public.tg_status() as r")[0]["r"]
    assert status["linked"] is True and status["username"] == "artist_hunter"
    # A code works once.
    bot.handle(message(f"/start {started['code']}", chat=OTHER_CHAT))
    assert "не подошёл" in tg.texts(OTHER_CHAT)[-1]
    # The user cannot read codes, links or questions behind the functions.
    for table in ("tg_link_codes", "tg_links", "tg_requests"):
        with pytest.raises(psycopg.Error):
            owner.run(f"select * from public.{table}")


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
    bot.handle(message("кто ждёт ответа?", chat=OTHER_CHAT))
    assert "не привязан" in tg.texts(OTHER_CHAT)[-1]
    linked(bot, owner)
    before = len(tg.sent)
    bot.handle(message("кто ждёт ответа?", kind="group"))
    assert len(tg.sent) == before
    assert not db.execute("select 1 from public.tg_requests").fetchall()


def test_a_blocked_account_is_not_served(bot, tg, owner, db):
    linked(bot, owner)
    db.execute("update public.profiles set blocked = true where id = %s", (owner.user_id,))
    bot.handle(message("кто ждёт ответа?"))
    assert "не привязан" in tg.texts()[-1]
    assert not db.execute("select 1 from public.tg_requests").fetchall()
    with pytest.raises(psycopg.Error, match="заблокирован"):
        owner.take()


def test_unlink_forgets_the_chat_and_its_waiting_questions(bot, tg, owner, db):
    linked(bot, owner)
    bot.handle(message("кто ждёт ответа?"))
    bot.handle(message("/unlink"))
    assert "отвязан" in tg.texts()[-1]
    assert not db.execute("select 1 from public.tg_links").fetchall()
    assert owner.take() == []
    bot.handle(message("кто ждёт ответа?"))
    assert "не привязан" in tg.texts()[-1]


# ---------- questions and answers ----------


def test_a_question_reaches_the_app_and_the_answer_comes_back(bot, tg, owner, db):
    linked(bot, owner)
    assert owner.take() == []  # the app is running: it asks for questions
    bot.handle(message("Кто ждёт ответа в iMessage?"))
    # The app is alive, so the chat only shows «печатает».
    assert tg.actions == [(CHAT, "typing")] and APP_CLOSED not in tg.texts()

    [request] = owner.take()
    assert request["question"] == "Кто ждёт ответа в iMessage?"
    assert owner.take() == []  # handed out once
    bot.tick()
    assert tg.actions[-1] == (CHAT, "typing")  # still working on it

    owner.answer(request["request_id"], "Ждут двое: Jay и Dre.\n<b>не разметка</b>")
    bot.tick()
    assert tg.texts()[-1] == "Ждут двое: Jay и Dre.\n<b>не разметка</b>"
    assert "parse_mode" not in tg.sent[-1]
    # Sent once, whatever happens next.
    before = len(tg.sent)
    bot.tick()
    owner.answer(request["request_id"], "ещё раз")
    bot.tick()
    assert len(tg.sent) == before


def test_a_question_waits_while_the_app_is_closed(bot, tg, owner, db):
    linked(bot, owner)
    bot.handle(message("Кому стоит написать сегодня?"))
    assert tg.texts()[-1] == APP_CLOSED
    # The app opens later and finds it.
    [request] = owner.take()
    owner.answer(request["request_id"], "Напишите Jay.")
    bot.tick()
    assert tg.texts()[-1] == "Напишите Jay."
    # A day is the limit: after it the question is dropped without a word.
    bot.handle(message("а ещё?"))
    db.execute("update public.tg_requests set created_at = now() - interval '25 hours'")
    before = len(tg.sent)
    bot.tick()
    assert owner.take() == [] and len(tg.sent) == before


def test_a_question_the_app_dropped_is_reported(bot, tg, owner, db):
    linked(bot, owner)
    owner.take()
    bot.handle(message("кто ждёт ответа?"))
    [request] = owner.take()
    db.execute("update public.tg_requests set taken_at = now() - interval '16 minutes'")
    bot.tick()
    assert tg.texts()[-1] == NO_ANSWER
    # A late answer to it is not sent.
    owner.answer(request["request_id"], "поздно")
    bot.tick()
    assert tg.texts()[-1] == NO_ANSWER


def test_questions_of_one_account_never_reach_another(bot, tg, owner, dsn, db):
    other = AsUser(dsn, add_user(db, "other@example.com"))
    linked(bot, owner)
    linked(bot, other, chat=OTHER_CHAT)
    bot.handle(message("мой вопрос"))
    assert other.take() == []
    [request] = owner.take()
    # Another account cannot answer it either.
    other.answer(request["request_id"], "чужой ответ")
    bot.tick()
    assert "чужой ответ" not in tg.texts() + tg.texts(OTHER_CHAT)


def test_new_chat_long_texts_and_too_many_questions(bot, tg, owner, db):
    linked(bot, owner)
    owner.take()
    bot.handle(message("/new"))
    assert [row["question"] for row in owner.take()] == ["/new"]
    bot.handle(message("/find 10"))  # a command of the old bot
    assert "передаю ваши сообщения ассистенту" in tg.texts()[-1]
    bot.handle(message("📊 Статус"))  # a button of its menu: the menu goes away
    assert tg.sent[-1]["reply_markup"] == {"remove_keyboard": True}
    assert owner.take() == []
    bot.handle(message("я" * 4001))
    assert "Слишком длинно" in tg.texts()[-1]
    for index in range(4):
        bot.handle(message(f"вопрос {index}"))
    bot.handle(message("ещё один"))
    assert "ещё не ответил" in tg.texts()[-1]
    assert len(db.execute("select 1 from public.tg_requests").fetchall()) == 5


def test_a_long_answer_goes_out_in_pieces(bot, tg, owner):
    linked(bot, owner)
    owner.take()
    bot.handle(message("расскажи всё"))
    [request] = owner.take()
    answer = "\n\n".join(f"Абзац {index}. " + "слово " * 150 for index in range(12))
    owner.answer(request["request_id"], answer)
    before = len(tg.sent)
    bot.tick()
    parts = tg.texts()[before:]
    assert len(parts) > 1 and all(len(part) <= 3900 for part in parts)
    assert "Абзац 0." in parts[0] and "Абзац 11." in parts[-1]
    assert sum(part.count("Абзац") for part in parts) == 12
    assert pieces("a" * 9000, 4000) == ["a" * 4000, "a" * 4000, "a" * 1000]


def test_a_user_who_blocked_the_bot_does_not_stop_it(bot, tg, owner, dsn, db):
    other = AsUser(dsn, add_user(db, "other@example.com"))
    linked(bot, owner)
    linked(bot, other, chat=OTHER_CHAT)
    owner.take(), other.take()
    bot.handle(message("первый"))
    bot.handle(message("второй", chat=OTHER_CHAT))
    for user in (owner, other):
        [request] = user.take()
        user.answer(request["request_id"], "ответ")
    tg.blocked.add(CHAT)
    bot.tick()
    assert tg.texts(OTHER_CHAT)[-1] == "ответ"
    assert not db.execute("select 1 from public.tg_requests where status = 'answered'").fetchall()


def test_the_cloud_parser_is_gone_from_the_database(db):
    left = db.execute(
        "select relname as name from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and relname like 'cloud%' "
        "union all select proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and proname like 'cloud%'"
    ).fetchall()
    assert left == []
    # What the parser saved on CRM contacts stays.
    columns = db.execute(
        "select column_name from information_schema.columns "
        "where table_name = 'crm_contacts' and column_name in ('source', 'cloud', 'instagram_id')"
    ).fetchall()
    assert len(columns) == 3
