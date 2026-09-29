"""Local music classifier, AI fallback, AI cache and pending AI jobs (no real AI calls)."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from artist_lead_finder.database import open_database
from artist_lead_finder.lead_scout.ai import OpenRouterClassifier, parse_answer
from artist_lead_finder.lead_scout.classification import (
    CLASSIFIER_VERSION,
    AIClassificationError,
    AIClassificationResult,
    AIInvalidOutput,
    AITransientError,
    ClassificationService,
    ClassificationSettings,
    LocalMusicClassifier,
    ProfileText,
    RuleRegistry,
    SqlAIClassificationCache,
    SqlPendingAIJobs,
    build_profile_classification_text,
    calculate_confidence,
    profile_hash,
)
from artist_lead_finder.lead_scout.classification.cache import MAX_ATTEMPTS
from artist_lead_finder.models import utcnow

FIXTURES = Path(__file__).parent / "fixtures" / "classification" / "profiles.json"
LOCAL = LocalMusicClassifier()


def classify(**fields):
    return LOCAL.classify(ProfileText(**fields))


def evidence(result, rule_prefix=""):
    return [r.evidence for r in result.rule_results if r.rule_id.startswith(rule_prefix)]


@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text(encoding="utf-8")), ids=str)
def test_profile_fixtures(case):
    result = LOCAL.classify(ProfileText(**case["profile"]))
    assert result.category == case["expected"], result.reasons
    assert result.uncertain == case["uncertain"], (result.confidence, result.scores)
    assert result.confidence >= case.get("min_confidence", 0)
    assert result.confidence <= case.get("max_confidence", 100)
    assert result.reasons  # always explainable


# ---------- Rules ----------


def test_artist_signals_and_generic_artist_weight():
    rapper = classify(username="x", biography="Independent rapper and songwriter from ATL")
    assert rapper.category == "artist"
    assert "Bio contains strong artist term: independent artist" not in rapper.reasons
    assert "Bio contains strong artist term: rapper" in rapper.reasons
    assert "Bio contains strong artist term: songwriter" in rapper.reasons
    # "artist" alone may be a painter: small weight, not a category by itself.
    painter = classify(username="y", biography="artist")
    assert painter.category == "other" and painter.scores["artist"] == 1 and painter.uncertain
    # Plural "artists" describes clients, not the account.
    assert classify(username="z", biography="I help artists grow").scores["artist"] == 0
    # Next to music emojis or words, "artist" weighs a little more (still not decisive).
    emoji = classify(username="e", biography="#Artist 🎤🎶 Atlanta GA")
    assert emoji.scores["artist"] == 2
    assert "Bio contains generic artist (music context): artist" in emoji.reasons
    assert classify(username="r", biography="артист").scores["artist"] == 1


def test_russian_instagram_categories():
    band = classify(username="x", category_name="Музыкант/группа")
    assert band.category == "artist" and "Instagram category: Музыкант/группа" in band.reasons
    arts = classify(username="y", category_name="Деятель искусств")
    assert "Instagram category: Деятель искусств (ambiguous)" in arts.reasons


def test_release_phrases_first_person_is_stronger():
    own = classify(username="a", biography="my new single out now")
    generic = classify(username="b", biography="new music")
    assert "Release phrase detected: my new single" in own.reasons
    # "new single" inside "my new single" is not counted twice.
    assert "Release phrase: new single" not in own.reasons
    assert own.scores["artist"] > generic.scores["artist"] == 1
    captions = classify(username="c", recent_captions=["New single out now, link in bio"])
    assert captions.scores["artist"] <= 2  # captions alone stay weak


def test_music_links_and_youtube_is_weak():
    spotify = classify(username="a", bio_links=["https://open.spotify.com/artist/x"])
    assert "Spotify link found" in spotify.reasons and spotify.scores["artist"] == 3
    youtube = classify(username="b", external_url="https://youtube.com/@b")
    assert youtube.scores["artist"] == 1
    combo = classify(
        username="c", biography="new album out now", external_url="https://music.apple.com/x"
    )
    assert "Apple Music link + release wording" in combo.reasons


def test_producer_signals_links_and_context():
    beats = classify(
        username="mike",
        biography="beats for sale · drum kits",
        external_url="https://bsta.rs/mike",
    )
    assert beats.category == "producer" and not beats.uncertain
    assert "BeatStars link found" in beats.reasons
    assert "BeatStars link + beats/prod wording" in beats.reasons
    # "Producer" of films: no music context, weak only.
    film = classify(username="f", biography="Film producer | director")
    assert film.category == "other"
    assert "Bio contains producer (no music context): producer" in film.reasons


def test_username_rules_add_score_but_never_decide():
    result = classify(username="prodbyjohn")
    assert "Username contains prodby" in result.reasons
    assert result.category == "other" and result.uncertain
    assert classify(username="johnbeats").scores["producer"] == 2
    assert classify(username="rapnewsdaily").scores["media"] == 1


def test_media_and_services_need_music_context():
    media = classify(username="m", biography="Hip hop news · playlist curator")
    assert media.category == "media"
    radio = classify(username="r", biography="Local radio for the community")
    assert radio.scores["media"] == 0  # generic radio without music context
    director = classify(username="d", biography="Music video director")
    assert director.category == "media"
    wedding = classify(username="w", biography="Wedding videographer")
    assert wedding.category == "other"
    assert "Videographer without music context" in wedding.reasons


def test_negative_signals_score_other_but_strong_music_can_win():
    spam = classify(username="x", biography="rapper | forex signals and casino giveaway")
    assert spam.category == "other" and not spam.uncertain
    assert "Negative signal: forex" in spam.reasons
    strong = classify(
        username="y",
        biography="Rapper & singer. My new album out now. giveaway on my story",
        external_url="https://open.spotify.com/x",
    )
    assert strong.category == "artist"
    assert "Negative signal: giveaway" in strong.reasons
    fan = classify(username="z", biography="fan page of @drake, rapper")
    assert fan.uncertain  # a fan page mentioning a rapper goes to AI


def test_conflicting_artist_and_producer_is_uncertain():
    both = classify(username="x", biography="Rapper / Producer")
    assert both.scores["artist"] > 0 and both.scores["producer"] > 0
    assert both.uncertain
    clear = classify(
        username="y",
        biography="Rapper / producer. My new single out now",
        external_url="https://open.spotify.com/y",
    )
    assert clear.category == "artist" and not clear.uncertain


def test_classification_text_normalises_but_keeps_originals():
    text = build_profile_classification_text(
        ProfileText(username="@Lil.Nova", biography="𝐑𝐚𝐩𝐩𝐞𝐫 | Pre-Save   NOW")
    )
    assert text.bio == "rapper pre save now"
    assert text.username == "lil.nova"
    assert text.original["biography"] == "𝐑𝐚𝐩𝐩𝐞𝐫 | Pre-Save   NOW"


def test_rule_registry_rejects_duplicates_and_accepts_custom_rules():
    registry = RuleRegistry.default()
    with pytest.raises(ValueError):
        registry.register(registry.rules[0])

    class AlwaysMedia:
        id = "custom.media"

        def evaluate(self, context):
            from artist_lead_finder.lead_scout.classification import ClassificationRuleResult

            return [ClassificationRuleResult(self.id, "media", 9, "custom", strong=True)]

    custom = LocalMusicClassifier(RuleRegistry([AlwaysMedia()]))
    assert custom.classify(ProfileText(username="x")).category == "media"


# ---------- Confidence ----------


def test_confidence_calculation_examples():
    high = calculate_confidence({"artist": 22, "producer": 2}, strong_signals=3)
    assert high.category == "artist" and high.confidence >= 90 and not high.uncertain
    close = calculate_confidence({"artist": 11, "producer": 10}, strong_signals=2)
    assert close.uncertain and close.confidence < 50
    low = calculate_confidence({"artist": 3, "other": 2})
    assert low.category == "other" and low.uncertain and low.confidence < 60
    nothing = calculate_confidence({})
    assert nothing.category == "other" and not nothing.uncertain and nothing.confidence >= 70
    negative = calculate_confidence({"artist": 5, "other": 12}, negative_signals=3)
    assert negative.category == "other" and negative.confidence >= 85
    # Negative signals lower the confidence of a music category.
    clean = calculate_confidence({"artist": 10}, strong_signals=1)
    dirty = calculate_confidence({"artist": 10, "other": 2}, strong_signals=1, negative_signals=1)
    assert dirty.confidence < clean.confidence


# ---------- AI parsing and transport ----------


def test_ai_parser_validates_the_schema():
    assert parse_answer('{"category":"producer","confidence":88}').category == "producer"
    assert parse_answer('```json\n{"category":"media","confidence":70}\n```').confidence == 70
    for bad in [
        "artist, 90",
        '{"category":"chef","confidence":90}',
        '{"category":"artist","confidence":140}',
        '{"category":"artist","confidence":"90"}',
        '{"category":"artist","confidence":90,"why":"x"}',
        'Sure! {"category":"artist","confidence":90}',
    ]:
        with pytest.raises(AIInvalidOutput):
            parse_answer(bad)


class Keys:
    def configured(self):
        return True

    def load(self):
        return "sk-test"


def chat(content: str) -> str:
    return json.dumps({"choices": [{"message": {"content": content}}]})


def test_ai_repairs_one_invalid_answer_then_gives_up():
    answers = iter(["I think artist", '{"category":"artist","confidence":77}'])
    sent = []

    def transport(url, headers, body, timeout):
        sent.append((json.loads(body), timeout, headers["Authorization"]))
        return chat(next(answers))

    ai = OpenRouterClassifier(Keys(), transport)
    local = classify(username="x", biography="dj")
    result = ai.classify(ProfileText("x", biography="dj"), local, model="m/1", timeout=12)
    assert (result.category, result.confidence, result.model) == ("artist", 77, "m/1")
    first, second = sent[0][0], sent[1][0]
    assert sent[0][1] == 12 and sent[0][2] == "Bearer sk-test"
    assert "Classify one public Instagram profile" in first["messages"][0]["content"]
    payload = json.loads(first["messages"][1]["content"])
    assert payload["username"] == "x" and payload["localHint"]["category"] == "other"
    assert "not valid" in second["messages"][-1]["content"]

    always_bad = OpenRouterClassifier(Keys(), lambda *args: chat("nope"))
    with pytest.raises(AIInvalidOutput):
        always_bad.classify(ProfileText("x"), model="m/1", timeout=5)


def test_ai_requires_a_key():
    class NoKeys:
        def configured(self):
            return False

    with pytest.raises(AIClassificationError) as error:
        OpenRouterClassifier(NoKeys(), lambda *a: "").classify(ProfileText("x"), model="m")
    assert not error.value.transient


# ---------- Service: modes, cache, pending jobs ----------


class FakeAI:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def classify(self, profile, local=None, *, model, timeout):
        self.calls.append(profile.username)
        answer = self.answers.pop(0) if self.answers else ("artist", 80)
        if isinstance(answer, Exception):
            raise answer
        return AIClassificationResult(answer[0], answer[1], model)


@pytest.fixture
def db(tmp_path):
    engine, sessions = open_database(tmp_path / "classification.db")
    yield sessions
    engine.dispose()


def service(sessions, ai, now=utcnow):
    return ClassificationService(
        ai, SqlAIClassificationCache(sessions), SqlPendingAIJobs(sessions, now=now)
    )


UNCERTAIN = ProfileText("maybe", biography="new music soon")
CLEAR = ProfileText(
    "clear", biography="Rapper. My new single out now", external_url="https://open.spotify.com/c"
)


def test_ai_modes_and_strong_local_override(db):
    ai = FakeAI(("producer", 87))
    classifier = service(db, ai)
    off = classifier.classify(UNCERTAIN, ClassificationSettings(mode="off"))
    assert off.decided_by == "local" and off.ai is None and ai.calls == []
    assert "AI:\noff" in "\n".join(off.log)

    clear = classifier.classify(CLEAR, ClassificationSettings(mode="uncertain"))
    assert clear.decided_by == "local" and ai.calls == []  # strong local override
    assert "AI:\nnot required" in "\n".join(clear.log)

    uncertain = classifier.classify(UNCERTAIN, ClassificationSettings(mode="uncertain"))
    assert ai.calls == ["maybe"]
    assert (uncertain.category, uncertain.confidence, uncertain.decided_by) == (
        "producer",
        87,
        "local+ai",
    )
    assert uncertain.local.category == "other"  # local result is kept

    always = classifier.classify(CLEAR, ClassificationSettings(mode="always"))
    assert ai.calls == ["maybe", "clear"] and always.decided_by == "local+ai"  # agrees


def test_low_confidence_threshold_triggers_ai(db):
    ai = FakeAI(("artist", 70))
    local = LOCAL.classify(ProfileText("r", biography="rapper"))
    assert not local.uncertain
    settings = ClassificationSettings(mode="uncertain", min_confidence=local.confidence + 1)
    assert service(db, ai).decide(ProfileText("r", biography="rapper"), local, settings).ai
    assert ai.calls == ["r"]


def test_ai_cache_is_used_and_versioned(db):
    ai = FakeAI(("artist", 81))
    classifier = service(db, ai)
    settings = ClassificationSettings(mode="always")
    first = classifier.classify(UNCERTAIN, settings)
    second = classifier.classify(UNCERTAIN, settings)
    assert ai.calls == ["maybe"] and not first.ai.cached and second.ai.cached
    assert "(cached)" in "\n".join(second.log)
    # Another classifier version ignores the cached answer.
    newer = SqlAIClassificationCache(db, version=CLASSIFIER_VERSION + "-next")
    assert newer.get(profile_hash(UNCERTAIN)) is None


def test_profile_hash_changes_with_relevant_fields_only():
    base = profile_hash(UNCERTAIN)
    assert base == profile_hash(ProfileText("MAYBE", biography="new   music soon"))
    assert base != profile_hash(ProfileText("maybe", biography="new music out now"))
    assert base != profile_hash(
        ProfileText("maybe", biography="new music soon", bio_links=["https://x.co"])
    )
    assert base != profile_hash(UNCERTAIN, version="other")
    assert len(base) == 64


def test_ai_failures_fall_back_to_local_and_transient_ones_are_retried(db):
    clock = [utcnow()]
    ai = FakeAI(
        AIInvalidOutput("bad"),
        AITransientError("OpenRouter недоступен."),
        AITransientError("OpenRouter ответил 503."),
        ("artist", 90),
    )
    classifier = service(db, ai, now=lambda: clock[0])
    settings = ClassificationSettings(mode="always")
    invalid = classifier.classify(UNCERTAIN, settings)
    assert invalid.decided_by == "local" and not invalid.ai_pending
    assert classifier.pending.count() == 0

    down = classifier.classify(UNCERTAIN, settings, context={"url": "u", "username": "maybe"})
    assert down.decided_by == "local" and down.ai_pending
    assert "retry scheduled" in down.ai_note and classifier.pending.count() == 1
    # Not due yet: nothing is retried, the Scout is never blocked.
    assert classifier.retry_pending(settings) == [] and len(ai.calls) == 2

    clock[0] += timedelta(minutes=2)
    assert classifier.retry_pending(settings) == []  # 503 again, backoff grows
    assert classifier.pending.count() == 1
    clock[0] += timedelta(minutes=5)
    [retried] = classifier.retry_pending(settings)
    assert retried.ai.category == "artist" and retried.context["username"] == "maybe"
    assert classifier.pending.count() == 0
    # The answer is cached: the next classification does not call AI again.
    calls = len(ai.calls)
    assert classifier.classify(UNCERTAIN, settings).ai.cached and len(ai.calls) == calls


def test_pending_jobs_are_dropped_after_max_attempts(db):
    clock = [utcnow()]
    jobs = SqlPendingAIJobs(db, now=lambda: clock[0])
    jobs.add("h" * 64, UNCERTAIN, {}, "down")
    for _ in range(MAX_ATTEMPTS - 2):
        assert jobs.failed("h" * 64, "down")
    assert not jobs.failed("h" * 64, "down") and jobs.count() == 0
