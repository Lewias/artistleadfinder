"""Source adapters return validated data; never touch persistence or scoring."""

import csv
import json
import random
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform: str = Field(min_length=1, max_length=40)
    platform_user_id: str | None = Field(default=None, max_length=160)
    username: str = Field(min_length=1, max_length=160)
    display_name: str = Field(default="", max_length=240)
    profile_url: str = Field(default="", max_length=2048)
    avatar_url: str = Field(default="", max_length=2048)
    bio: str = Field(default="", max_length=10000)
    followers: int = Field(default=0, ge=0, le=1_000_000_000)
    following: int = Field(default=0, ge=0, le=1_000_000_000)
    is_verified: bool = False
    is_private: bool = False
    external_url: str = Field(default="", max_length=2048)
    last_activity_at: datetime | None = None
    recent_content: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("profile_url", "avatar_url", "external_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
                raise ValueError("Only HTTP(S) URLs without credentials are permitted.")
        return value

    @field_validator("recent_content")
    @classmethod
    def bounded_content(cls, values: list[str]) -> list[str]:
        if any(len(value) > 10000 for value in values):
            raise ValueError("Content metadata exceeds allowed length.")
        return values


class ProviderError(Exception):
    def __init__(self, message: str, status: str = "Unavailable") -> None:
        super().__init__(message)
        self.status = status


class SourceProvider(ABC):
    name: str

    @abstractmethod
    def search_by_seed(self, value: str) -> Iterable[Candidate]: ...

    @abstractmethod
    def search_by_keyword(self, value: str) -> Iterable[Candidate]: ...

    @abstractmethod
    def search_by_hashtag(self, value: str) -> Iterable[Candidate]: ...

    @abstractmethod
    def get_profile(self, identity: str) -> Candidate | None: ...

    def get_recent_content(self, identity: str) -> list[str]:
        profile = self.get_profile(identity)
        return profile.recent_content if profile else []


def generate_profiles(
    count: int = 1000, seed: int = 42, now: datetime | None = None
) -> list[Candidate]:
    rng = random.Random(seed)
    now = now or datetime(2026, 9, 18, tzinfo=timezone.utc)
    names = ["northsidejay", "lunar.wav", "kidmercury", "soulframe", "velvetghost", "saintkairo"]
    genres = ["Hip-Hop", "Trap", "R&B", "Pop", "Indie", "Electronic", "Rock", "Soul"]
    unrelated = [
        "Photographer | portraits",
        "Music fan | playlists",
        "Streetwear brand",
        "Travel and coffee",
        "Fitness coach",
    ]
    profiles = []
    for index in range(count):
        bucket = index % 100
        genre = rng.choice(genres)
        artist = bucket < 50
        producer = 50 <= bucket < 65
        bio = (
            f"Independent {genre} artist. New EP out now."
            if artist
            else f"{genre} producer | beats and mixing"
            if producer
            else rng.choice(unrelated)
        )
        profiles.append(
            Candidate(
                platform="mock",
                platform_user_id=str(index + 1),
                username=names[index] if index < len(names) else f"{names[index % 6]}_{index}",
                display_name=f"{genre} Studio {index + 1}",
                bio=bio if rng.random() > 0.08 else "",
                profile_url=f"https://example.com/demo/artist/{index + 1}",
                followers=rng.randint(200, 120000),
                following=rng.randint(50, 2200),
                is_private=rng.random() < 0.08,
                is_verified=rng.random() < 0.03,
                external_url=f"https://open.spotify.com/artist/demo{index}"
                if artist and rng.random() < 0.7
                else "https://soundcloud.com/demo"
                if producer
                else "",
                last_activity_at=now - timedelta(days=rng.randint(0, 150)),
                recent_content=[f"New {genre} single release"] if artist else [],
            )
        )
    return profiles


class DatasetProvider(SourceProvider):
    def __init__(self, profiles: list[Candidate]) -> None:
        self.profiles = profiles

    def search_by_seed(self, value: str) -> Iterable[Candidate]:
        # Imported data has no implied social graph. Seeds match explicit identities only.
        value = value.lstrip("@").casefold()
        return (p for p in self.profiles if p.username.casefold() == value)

    def search_by_keyword(self, value: str) -> Iterable[Candidate]:
        term = value.casefold()
        return (
            p
            for p in self.profiles
            if term
            in f"{p.username} {p.display_name} {p.bio} {' '.join(p.recent_content)}".casefold()
        )

    def search_by_hashtag(self, value: str) -> Iterable[Candidate]:
        return self.search_by_keyword(value.lstrip("#"))

    def get_profile(self, identity: str) -> Candidate | None:
        return next(
            (
                p
                for p in self.profiles
                if p.platform_user_id == identity
                or p.username.casefold() == identity.lstrip("@").casefold()
            ),
            None,
        )


class MockProvider(DatasetProvider):
    name = "mock"

    def __init__(self, count: int = 1000, seed: int = 42) -> None:
        super().__init__(generate_profiles(count, seed, datetime.now(timezone.utc)))

    def search_by_seed(self, value: str) -> Iterable[Candidate]:
        # Synthetic demo graph only; never represents real followers.
        return iter(self.profiles)

    def search_by_hashtag(self, value: str) -> Iterable[Candidate]:
        if value.lstrip("#").casefold() in {"newmusic", "independentartist"}:
            return (p for p in self.profiles if "artist" in p.bio.casefold())
        return super().search_by_hashtag(value)


class ImportedDatasetProvider(DatasetProvider):
    name = "imported"

    def __init__(self, path: Path, max_bytes: int = 25 * 1024 * 1024) -> None:
        if path.stat().st_size > max_bytes:
            raise ValueError("Dataset exceeds 25 MB limit.")
        with path.open(encoding="utf-8-sig", newline="") as file:
            if path.suffix.lower() == ".csv":
                records = list(csv.DictReader(file))
            elif path.suffix.lower() == ".json":
                records = json.load(file)
            else:
                raise ValueError("Only CSV and JSON datasets are supported.")
        if not isinstance(records, list) or len(records) > 100000:
            raise ValueError("Expected an array with at most 100000 profiles.")
        super().__init__([Candidate.model_validate(record) for record in records])


class MetaInstagramProvider(SourceProvider):
    name = "meta_instagram"

    def _unavailable(self):
        raise ProviderError(
            "Instagram: требуется разрешённая официальная интеграция.", "Authentication Required"
        )

    def search_by_seed(self, value: str) -> Iterable[Candidate]:
        return self._unavailable()

    def search_by_keyword(self, value: str) -> Iterable[Candidate]:
        return self._unavailable()

    def search_by_hashtag(self, value: str) -> Iterable[Candidate]:
        return self._unavailable()

    def get_profile(self, identity: str) -> Candidate | None:
        return self._unavailable()
