import json

import pytest
from pydantic import ValidationError

from artist_lead_finder.providers import (
    Candidate,
    ImportedDatasetProvider,
    MetaInstagramProvider,
    ProviderError,
    generate_profiles,
)


def test_mock_is_reproducible_and_has_required_distribution():
    profiles = generate_profiles()
    assert profiles == generate_profiles()
    assert len(profiles) == 1000
    assert sum("Independent" in p.bio for p in profiles) in range(440, 501)
    assert sum("producer" in p.bio for p in profiles) in range(120, 151)
    assert any(p.is_private for p in profiles)


def test_import_validates_and_does_not_execute_urls(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([generate_profiles(1)[0].model_dump(mode="json")]))
    provider = ImportedDatasetProvider(path)
    assert provider.get_profile("1").username == "northsidejay"
    with pytest.raises(ValidationError):
        Candidate(platform="test", username="bad", external_url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        Candidate(platform="test", username="bad", followers=-1)


def test_meta_adapter_fails_explicitly_without_unauthorized_requests():
    with pytest.raises(ProviderError) as error:
        MetaInstagramProvider().search_by_keyword("rapper")
    assert error.value.status == "Authentication Required"
