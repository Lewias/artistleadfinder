"""Classification rules and their registry. Each rule is a small object with one job;
it returns ClassificationRuleResult items (category, score, human-readable evidence)."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from . import lexicon as lx
from .config import ClassifierWeights
from .model import ClassificationRuleResult
from .text import ClassificationText, normalize_text


@dataclass
class ClassificationContext:
    text: ClassificationText
    weights: ClassifierWeights
    # Filled once per profile, shared by the rules.
    music_context: bool = False
    service_terms: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, text: ClassificationText, weights: ClassifierWeights):
        context = cls(text, weights)
        haystack = "\n".join([text.all_text, *text.links])
        context.music_context = bool(
            lx.MUSIC_CONTEXT.search(haystack)
            or lx.MUSIC_EMOJI.search(text.original.get("biography") or "")
            or any(host in lx.MUSIC_LINK_HOSTS for host in text.hosts)
            or any(host in lx.BEAT_MARKETPLACE_HOSTS for host in text.hosts)
        )
        # Services describe the account itself: name, bio, category (not captions).
        context.service_terms = unique(SERVICES.findall(text.profile))
        return context


class ClassificationRule(Protocol):
    id: str

    def evaluate(self, context: ClassificationContext) -> list[ClassificationRuleResult]: ...


def unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def field_matches(pattern, context: ClassificationContext) -> list[tuple[str, str]]:
    """(field label, phrase) for the self-description fields, first field wins."""
    seen: dict[str, str] = {}
    for label, value in (
        ("Bio", context.text.bio),
        ("Name", context.text.name),
        ("Category", context.text.category),
    ):
        for phrase in pattern.findall(value):
            seen.setdefault(phrase, label)
    return [(label, phrase) for phrase, label in seen.items()]


def caption_matches(pattern, context: ClassificationContext) -> list[str]:
    return unique(p for caption in context.text.captions for p in pattern.findall(caption))


def host_names(hosts: list[str], table: dict[str, str]) -> list[str]:
    names = []
    for host in hosts:
        name = table.get(host) or next(
            (label for domain, label in table.items() if host.endswith("." + domain)), None
        )
        if name:
            names.append(name)
    return unique(names)


# ---------- Artist ----------

ARTIST_TIERS = {
    **{p: "strong" for p in lx.ARTIST_STRONG},
    **{p: "medium" for p in lx.ARTIST_MEDIUM},
    **{p: "weak" for p in lx.ARTIST_WEAK},
    # Consumed so that "makeup artist" is not read as "artist"; the negative rule scores it.
    **{p: "ignore" for p in lx.NEGATIVE_MEDIUM if "artist" in p},
}
ARTIST_PATTERN = lx.phrase_pattern(list(ARTIST_TIERS))


class ArtistTermsRule:
    id = "artist.terms"

    def evaluate(self, context):
        w, results = context.weights, []
        for label, phrase in field_matches(ARTIST_PATTERN, context):
            tier = ARTIST_TIERS[phrase]
            if tier == "ignore":
                continue
            score = {"strong": w.strong, "medium": w.medium, "weak": w.weak}[tier]
            kind = {"strong": "strong artist term", "medium": "artist term", "weak": "generic"}
            if tier == "weak" and context.music_context:
                # "Artist" next to music words or emojis ("#Artist 🎤") is likely a musician.
                score, kind["weak"] = w.medium, "generic artist (music context)"
            results.append(
                ClassificationRuleResult(
                    self.id,
                    "artist",
                    score,
                    f"{label} contains {kind[tier]}: {phrase}",
                    strong=tier == "strong",
                )
            )
        for phrase in caption_matches(ARTIST_PATTERN, context)[:2]:
            if ARTIST_TIERS[phrase] != "ignore":
                results.append(
                    ClassificationRuleResult(
                        "artist.captions", "artist", w.weak, f"Recent caption mentions {phrase}"
                    )
                )
        return results


# ---------- Releases ----------

RELEASE_TIERS = {
    **{p: "medium" for p in lx.RELEASE_PHRASES},
    **{p: "weak" for p in lx.RELEASE_WEAK},
}
RELEASE_PATTERN = lx.phrase_pattern(list(RELEASE_TIERS))


class FirstPersonReleaseRule:
    """ "my new single" is much stronger than "new music": the profile owns the release."""

    id = "release.first_person"

    def evaluate(self, context):
        w = context.weights
        own = unique(lx.FIRST_PERSON_RELEASE.findall(context.text.profile))
        if own:
            return [
                ClassificationRuleResult(
                    self.id,
                    "artist",
                    w.first_person_release,
                    f"Release phrase detected: {own[0]}",
                    strong=True,
                )
            ]
        captions = unique(
            m for caption in context.text.captions for m in lx.FIRST_PERSON_RELEASE.findall(caption)
        )
        if captions:
            return [
                ClassificationRuleResult(
                    "release.first_person_caption",
                    "artist",
                    w.medium,
                    f"Recent caption announces own release: {captions[0]}",
                )
            ]
        return []


class ReleasePhraseRule:
    id = "release.phrases"

    def evaluate(self, context):
        w, results = context.weights, []
        # Parts of a first-person phrase are already scored ("my new single" > "new single").
        profile = lx.FIRST_PERSON_RELEASE.sub(" ", context.text.profile)
        # Video / audio services talk about "music videos" as their product.
        service = bool(context.service_terms)
        for phrase in unique(RELEASE_PATTERN.findall(profile)):
            score = w.weak if service or RELEASE_TIERS[phrase] == "weak" else w.medium
            results.append(
                ClassificationRuleResult(self.id, "artist", score, f"Release phrase: {phrase}")
            )
        # Captions alone are weak: media accounts post about other people's releases.
        scored = {r.evidence.split(": ", 1)[1] for r in results}
        for phrase in caption_matches(RELEASE_PATTERN, context)[:2]:
            if phrase not in scored:
                results.append(
                    ClassificationRuleResult(
                        "release.captions", "artist", w.weak, f"Recent caption: {phrase}"
                    )
                )
        return results


# ---------- Links ----------


class MusicLinkRule:
    id = "links.music"

    def evaluate(self, context):
        w, results = context.weights, []
        for name in host_names(context.text.hosts, lx.MUSIC_LINK_HOSTS)[: w.music_links_max]:
            results.append(
                ClassificationRuleResult(self.id, "artist", w.music_link, f"{name} link found")
            )
        if not results:
            for name in host_names(context.text.hosts, lx.WEAK_MUSIC_LINK_HOSTS)[:1]:
                results.append(
                    ClassificationRuleResult(
                        "links.youtube", "artist", w.weak, f"{name} link found (weak)"
                    )
                )
        return results


class StreamingReleaseComboRule:
    """Streaming link + release wording in the bio: a strong artist signal."""

    id = "links.streaming_release"

    def evaluate(self, context):
        streaming = host_names(context.text.hosts, lx.MUSIC_LINK_HOSTS)
        text = context.text.profile
        if streaming and (RELEASE_PATTERN.search(text) or lx.FIRST_PERSON_RELEASE.search(text)):
            return [
                ClassificationRuleResult(
                    self.id,
                    "artist",
                    context.weights.streaming_release_combo,
                    f"{streaming[0]} link + release wording",
                    strong=True,
                )
            ]
        return []


class BeatMarketplaceRule:
    id = "links.beat_marketplace"

    def evaluate(self, context):
        w = context.weights
        names = host_names(context.text.hosts, lx.BEAT_MARKETPLACE_HOSTS)
        results = [
            ClassificationRuleResult(
                self.id, "producer", w.beat_marketplace, f"{name} link found", strong=True
            )
            for name in names[:1]
        ]
        if names and lx.BEATS_WORDING.search(f"{context.text.profile}\n{context.text.username}"):
            results.append(
                ClassificationRuleResult(
                    "links.marketplace_beats",
                    "producer",
                    w.marketplace_beats_combo,
                    f"{names[0]} link + beats/prod wording",
                )
            )
        return results


# ---------- Producer ----------

PRODUCER_TIERS = {
    **{p: "strong" for p in lx.PRODUCER_STRONG},
    **{p: "ambiguous" for p in lx.PRODUCER_AMBIGUOUS},
    **{p: "medium" for p in lx.PRODUCER_MEDIUM},
    **{p: "contextual" for p in lx.PRODUCER_CONTEXTUAL},
}
PRODUCER_PATTERN = lx.phrase_pattern(list(PRODUCER_TIERS))


class ProducerTermsRule:
    id = "producer.terms"

    def evaluate(self, context):
        w, results = context.weights, []
        for label, phrase in field_matches(PRODUCER_PATTERN, context):
            tier = PRODUCER_TIERS[phrase]
            if tier == "strong":
                score, kind = w.strong, "strong producer term"
            elif tier == "ambiguous":
                # "Producer" of films or events is not a music producer.
                score = w.medium + w.weak if context.music_context else w.weak
                kind = "producer" if context.music_context else "producer (no music context)"
            elif tier == "contextual" and not context.music_context:
                continue
            else:
                score, kind = w.medium, "producer term"
            results.append(
                ClassificationRuleResult(
                    self.id,
                    "producer",
                    score,
                    f"{label} contains {kind}: {phrase}",
                    strong=tier == "strong",
                )
            )
        return results


# ---------- Username ----------


class UsernameRule:
    """Username hints add a little score; they never decide on their own."""

    id = "username.patterns"

    def evaluate(self, context):
        w, results = context.weights, []
        username = context.text.username.replace(".", "").replace("_", "")
        for category, tokens in lx.USERNAME_PATTERNS.items():
            token = next((t for t in tokens if t in username), None)
            if token:
                score = w.username_producer if category == "producer" else w.weak
                results.append(
                    ClassificationRuleResult(
                        f"username.{category}",
                        category,
                        score,
                        f"Username contains {token}",
                    )
                )
        return results


# ---------- Media and services ----------

MEDIA_TIERS = {
    **{p: "strong" for p in lx.MEDIA_STRONG},
    **{p: "medium" for p in lx.MEDIA_MEDIUM},
}
MEDIA_PATTERN = lx.phrase_pattern(list(MEDIA_TIERS))
SERVICES = lx.phrase_pattern(lx.MUSIC_SERVICES)


class MediaTermsRule:
    id = "media.terms"

    def evaluate(self, context):
        w, results = context.weights, []
        for label, phrase in field_matches(MEDIA_PATTERN, context):
            tier = MEDIA_TIERS[phrase]
            if tier == "medium" and not context.music_context:
                continue
            strong = tier == "strong"
            results.append(
                ClassificationRuleResult(
                    self.id,
                    "media",
                    w.strong if strong else w.medium,
                    f"{label} contains {'music media term' if strong else 'media term'}: {phrase}",
                    strong=strong,
                )
            )
        return results


class MusicServiceRule:
    """Video / photo / audio services count as media only around music."""

    id = "media.services"

    def evaluate(self, context):
        w = context.weights
        terms = context.service_terms
        if not terms:
            return []
        if context.music_context:
            results = [
                ClassificationRuleResult(
                    self.id,
                    "media",
                    w.music_service,
                    f"Music service: {terms[0]} (music context)",
                    strong=True,
                )
            ]
            results += [
                ClassificationRuleResult(self.id, "media", w.weak, f"Music service: {term}")
                for term in terms[1:3]
            ]
            return results
        return [
            ClassificationRuleResult(
                "other.services",
                "other",
                w.non_music_service,
                f"{terms[0].capitalize()} without music context",
            )
        ]


INSTAGRAM_CATEGORIES = {normalize_text(k): v for k, v in lx.INSTAGRAM_CATEGORIES.items()}
INSTAGRAM_WEAK_CATEGORIES = {normalize_text(k): v for k, v in lx.INSTAGRAM_WEAK_CATEGORIES.items()}


class InstagramCategoryRule:
    id = "instagram.category"

    def evaluate(self, context):
        w, name = context.weights, context.text.category.strip()
        original = context.text.original.get("category_name") or name
        if name in INSTAGRAM_CATEGORIES:
            return [
                ClassificationRuleResult(
                    self.id,
                    INSTAGRAM_CATEGORIES[name],
                    w.instagram_category,
                    f"Instagram category: {original}",
                    strong=True,
                )
            ]
        if name in INSTAGRAM_WEAK_CATEGORIES:
            return [
                ClassificationRuleResult(
                    self.id,
                    INSTAGRAM_WEAK_CATEGORIES[name],
                    w.medium,
                    f"Instagram category: {original} (ambiguous)",
                )
            ]
        return []


# ---------- Negative ----------

NEGATIVE_TIERS = {
    **{p: "strong" for p in lx.NEGATIVE_STRONG},
    **{p: "medium" for p in lx.NEGATIVE_MEDIUM},
}
NEGATIVE_PATTERN = lx.phrase_pattern(list(NEGATIVE_TIERS))


class NegativeRule:
    """Scores for "other"; strong music signals can still outweigh them."""

    id = "negative.terms"

    def evaluate(self, context):
        w, results = context.weights, []
        text = f"{context.text.profile}\n{context.text.username.replace('_', ' ')}"
        for phrase in unique(NEGATIVE_PATTERN.findall(text)):
            strong = NEGATIVE_TIERS[phrase] == "strong"
            results.append(
                ClassificationRuleResult(
                    self.id,
                    "other",
                    w.strong_negative if strong else w.medium_negative,
                    f"Negative signal: {phrase}",
                    negative=True,
                )
            )
        return results


# ---------- Registry ----------


class RuleRegistry:
    def __init__(self, rules: list[ClassificationRule] | None = None):
        self.rules: list[ClassificationRule] = list(rules or [])

    def register(self, rule: ClassificationRule) -> "RuleRegistry":
        if any(existing.id == rule.id for existing in self.rules):
            raise ValueError(f"Duplicate classification rule: {rule.id}")
        self.rules.append(rule)
        return self

    def evaluate(self, context: ClassificationContext) -> list[ClassificationRuleResult]:
        return [result for rule in self.rules for result in rule.evaluate(context)]

    @classmethod
    def default(cls) -> "RuleRegistry":
        registry = cls()
        for rule in RULE_GROUPS.values():
            for item in rule:
                registry.register(item)
        return registry


RULE_GROUPS: dict[str, list[ClassificationRule]] = {
    "artist": [ArtistTermsRule(), InstagramCategoryRule()],
    "release": [FirstPersonReleaseRule(), ReleasePhraseRule()],
    "links": [MusicLinkRule(), StreamingReleaseComboRule(), BeatMarketplaceRule()],
    "producer": [ProducerTermsRule()],
    "username": [UsernameRule()],
    "media": [MediaTermsRule(), MusicServiceRule()],
    "negative": [NegativeRule()],
}
