"""Provider-only discovery: no database, classification or scoring dependencies."""

from dataclasses import dataclass
from typing import Callable, Iterator

from .providers import Candidate, ProviderError, SourceProvider
from .schemas import SearchConfiguration


@dataclass(frozen=True)
class DiscoveryRecord:
    candidate: Candidate
    provider: str
    source_type: str
    source_value: str


class DiscoveryEngine:
    def __init__(self, providers: list[SourceProvider]) -> None:
        self.providers = providers

    def discover(
        self,
        config: SearchConfiguration,
        checkpoint: Callable[[], bool],
        on_health: Callable[[str, str, str | None], None],
    ) -> Iterator[DiscoveryRecord]:
        queries = [("seed", value) for value in config.seed_accounts]
        queries += [("keyword", value) for value in config.keywords]
        queries += [("hashtag", value) for value in config.hashtags]
        for provider in self.providers:
            try:
                for kind, value in queries:
                    if not checkpoint():
                        return
                    on_health(provider.name, "request", None)
                    method = {
                        "seed": provider.search_by_seed,
                        "keyword": provider.search_by_keyword,
                        "hashtag": provider.search_by_hashtag,
                    }[kind]
                    for candidate in method(value):
                        if not checkpoint():
                            return
                        yield DiscoveryRecord(
                            Candidate.model_validate(candidate), provider.name, kind, value
                        )
                    on_health(provider.name, "Healthy", None)
            except ProviderError as error:
                # No retry, proxy fallback or restriction bypass.
                safe_errors = {
                    "Limited": "Источник ограничен квотой или rate limit.",
                    "Authentication Required": "Источник требует авторизации.",
                }
                on_health(
                    provider.name,
                    error.status,
                    safe_errors.get(error.status, "Источник недоступен."),
                )
            except Exception:
                on_health(provider.name, "Unavailable", "Источник временно недоступен.")
