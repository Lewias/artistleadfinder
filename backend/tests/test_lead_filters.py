"""Unit tests of the final Scout decision: ordered filters, skip reasons and details."""

from datetime import datetime, timezone

import pytest

from artist_lead_finder.lead_scout.classification import (
    FinalClassificationResult,
    LocalClassificationResult,
)
from artist_lead_finder.lead_scout.decision import (
    CandidateRef,
    FilterInput,
    decide,
    normalized_username,
)
from artist_lead_finder.lead_scout.profiles import NormalizedInstagramProfile
from artist_lead_finder.lead_scout.settings import ScoutSettings

REF = CandidateRef("youngjay", "rapdaily", "post", origin_url="https://www.instagram.com/p/A1/")


def profile(followers=1834, emails=("jay@mail.com",), phones=(), private=False):
    return NormalizedInstagramProfile(
        username="youngjay",
        source="api",
        resolved_at=datetime.now(timezone.utc),
        id="4242",
        followers_count=followers,
        emails=list(emails),
        phones=list(phones),
        is_private=private,
    )


def classified(category="artist", confidence=94):
    local = LocalClassificationResult(category, 10, confidence, [], False, [], {}, 2)
    return FinalClassificationResult(category, confidence, local, "local")


def run(settings=None, **data):
    values = {"profile": profile(), "classification": classified(), **data}
    return decide(FilterInput(REF, **values), settings or ScoutSettings())


@pytest.mark.parametrize(
    ("mode", "category", "accepted"),
    [
        ("artists", "artist", True),
        ("artists", "producer", False),
        ("artists", "media", False),
        ("artists", "other", False),
        ("artists_producers", "producer", True),
        ("artists_producers", "media", False),
        ("everyone", "other", True),
        ("everyone", "media", True),
    ],
)
def test_profile_type_modes(mode, category, accepted):
    decision = run(ScoutSettings(scout_profile_type=mode), classification=classified(category))
    assert decision.accepted is accepted
    if not accepted:
        assert decision.reason == "WRONG_PROFILE_TYPE"
        assert decision.details == f"{category} not allowed in {mode}"


def test_followers_limits_carry_details():
    settings = ScoutSettings(scout_min_followers=500, scout_max_followers=5000)
    low = run(settings, profile=profile(followers=245))
    assert (low.reason, low.details) == ("FOLLOWERS_TOO_LOW", "245 < min 500")
    high = run(settings, profile=profile(followers=9000))
    assert (high.reason, high.details) == ("FOLLOWERS_TOO_HIGH", "9000 > max 5000")
    assert run(settings, profile=profile(followers=500)).accepted


def test_unknown_followers_pass_unless_required():
    assert run(profile=profile(followers=None)).accepted
    strict = run(
        ScoutSettings(scout_allow_unknown_followers=False), profile=profile(followers=None)
    )
    assert (strict.reason, strict.details) == ("MISSING_REQUIRED_DATA", "followers count unknown")


def test_contacts_required():
    settings = ScoutSettings(scout_only_contacts=True)
    assert run(settings, profile=profile(emails=())).reason == "NO_CONTACT"
    assert run(settings, profile=profile(emails=(), phones=("+14045550199",))).accepted
    assert run(settings).accepted


def test_low_confidence():
    settings = ScoutSettings(scout_min_confidence=70)
    decision = run(settings, classification=classified(confidence=64))
    assert (decision.reason, decision.details) == ("LOW_CONFIDENCE", "64% < min 70%")
    assert run(settings, classification=classified(confidence=70)).accepted
    assert run(classification=classified(confidence=5)).accepted  # 0 turns it off


def test_source_account_and_ignored_usernames():
    source = CandidateRef("RapDaily", "rapdaily", "post")
    decision = decide(FilterInput(source), ScoutSettings())
    assert decision.reason == "SOURCE_ACCOUNT"
    ignored = ScoutSettings(scout_ignore_usernames=["@YoungJay"])
    assert decide(FilterInput(REF), ignored).reason == "IGNORED_USERNAME"
    assert normalized_username("https://www.instagram.com/Spotify/") == "spotify"


def test_already_processed_depends_on_the_setting():
    assert run(already_processed=True).reason == "ALREADY_PROCESSED"
    again = run(ScoutSettings(scout_skip_processed=False), already_processed=True)
    assert again.accepted


def test_duplicate_lead_and_private_profile():
    duplicate = run(existing_lead_id=7)
    assert (duplicate.reason, duplicate.details) == ("DUPLICATE_LEAD", "lead #7 is updated instead")
    assert run(profile=profile(private=True)).reason == "PROFILE_PRIVATE"


def test_filters_apply_strictly_in_order():
    settings = ScoutSettings(
        scout_min_followers=500, scout_only_contacts=True, scout_ignore_usernames=["youngjay"]
    )
    everything_wrong = dict(
        profile=profile(followers=10, emails=()),
        classification=classified("media"),
        already_processed=True,
        existing_lead_id=3,
    )
    assert run(settings, **everything_wrong).reason == "IGNORED_USERNAME"
    settings = settings.model_copy(update={"scout_ignore_usernames": []})
    assert run(settings, **everything_wrong).reason == "ALREADY_PROCESSED"
    everything_wrong["already_processed"] = False
    assert run(settings, **everything_wrong).reason == "DUPLICATE_LEAD"
    everything_wrong["existing_lead_id"] = None
    assert run(settings, **everything_wrong).reason == "FOLLOWERS_TOO_LOW"
    everything_wrong["profile"] = profile(emails=())
    assert run(settings, **everything_wrong).reason == "WRONG_PROFILE_TYPE"
    everything_wrong["classification"] = classified()
    assert run(settings, **everything_wrong).reason == "NO_CONTACT"


def test_stages_skip_before_expensive_steps():
    settings = ScoutSettings(scout_only_contacts=True)
    # Before the profile is opened only candidate filters run; nothing is decided yet.
    early = decide(FilterInput(REF), settings)
    assert not early.accepted and early.reason is None and not early.complete
    # Resolved but not classified: a missing contact already skips (no AI needed).
    before_ai = decide(FilterInput(REF, profile(emails=())), settings)
    assert before_ai.reason == "NO_CONTACT"
    assert [c.outcome for c in before_ai.checks if c.name == "profile type"] == ["not checked"]
    waiting = decide(FilterInput(REF, profile()), settings)
    assert not waiting.accepted and waiting.reason is None


def test_accepted_decision_explains_itself():
    decision = run(ScoutSettings(scout_only_contacts=True))
    assert decision.accepted and decision.complete
    assert decision.reasons[0] == "artist 94%"
    assert decision.log() == [
        "source account: pass",
        "ignored username: pass",
        "already processed: pass",
        "duplicate lead: pass",
        "required data: pass",
        "followers: pass (1834)",
        "profile type: pass (artist)",
        "confidence: pass (94%)",
        "contacts: pass (email)",
    ]
