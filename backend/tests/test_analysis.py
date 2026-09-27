from datetime import datetime, timedelta, timezone

from artist_lead_finder.analysis import ProfileAnalyzer
from artist_lead_finder.classification import RuleBasedClassifier
from artist_lead_finder.providers import Candidate


def test_artist_genre_and_activity_signals():
    now = datetime.now(timezone.utc)
    candidate = Candidate(
        platform="mock",
        username="artist",
        bio="Independent rapper. Trap EP out now",
        external_url="https://open.spotify.com/artist/123",
        last_activity_at=now - timedelta(days=3),
    )
    signals = ProfileAnalyzer().analyze(candidate, now)
    result = RuleBasedClassifier().classify(signals)
    assert result.is_artist
    assert result.confidence == 0.95
    assert result.genres == ["Hip-Hop", "Trap"]
    assert signals.activity_level == "strong"
    assert signals.has_spotify


def test_fans_producers_and_deceptive_hosts_are_not_artists():
    for bio in ["Music fan | artist fanpage", "Trap producer | beats", "Photographer"]:
        signals = ProfileAnalyzer().analyze(
            Candidate(
                platform="mock",
                username="other",
                bio=bio,
                external_url="https://spotify.com.evil.example/artist",
            )
        )
        assert not signals.has_spotify
        assert not RuleBasedClassifier().classify(signals).is_artist
