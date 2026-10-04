"""Validated application contracts, independent from SQLAlchemy."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import UserError


class SearchConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    seed_accounts: list[str] = Field(default_factory=list, max_length=100)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    hashtags: list[str] = Field(default_factory=list, max_length=100)
    genres: list[str] = Field(default_factory=list, max_length=50)
    min_followers: int = Field(default=1000, ge=0, le=1_000_000_000)
    max_followers: int = Field(default=50000, ge=0, le=1_000_000_000)
    activity_days: Literal[7, 14, 30, 60, 90] = 30
    minimum_score: int = Field(default=70, ge=0, le=100)
    target_leads: int = Field(default=500, ge=1, le=100000)

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if self.max_followers < self.min_followers:
            raise UserError("Максимум подписчиков должен быть не меньше минимума.")
        for values in (self.seed_accounts, self.keywords, self.hashtags, self.genres):
            if any(not value.strip() or len(value) > 240 for value in values):
                raise UserError("Значения поиска должны содержать от 1 до 240 символов.")
        return self
