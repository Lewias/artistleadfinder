"""Sales skills: the producer's playbook from `producer-sales-skills/` at the repo root.

That folder is also a Claude Code plugin: `skills/<name>/SKILL.md` files with front matter
(`name`, `description`) and shared, user-editable `reference/*.md` files (offers, stages,
voice…). The assistant's system prompt lists only names and descriptions; full texts are
read on demand with the `load_skill` tool, so the prompt stays small and cacheable. The
guardrails reference is the exception: it is short and always applies, so it goes into the
prompt in full. All of this is text placed in the prompt: nothing in it is executed.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# The frozen core bundles the folder next to this module (see scripts/build-core.py).
BUNDLED = Path(__file__).parent / "skills_library"
SKILLS_ROOT = BUNDLED if BUNDLED.is_dir() else Path(__file__).parents[3] / "producer-sales-skills"
GUARDRAILS = "guardrails"
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
TITLE = re.compile(r"^#\s+(.+)$", re.M)
# Skills point at references by plugin path; inside the app they are load_skill names.
REFERENCE_LINK = re.compile(r"`?\.\./\.\./reference/([\w-]+)\.md`?")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    kind: str  # "skill" or "reference"
    text: str


def _split(raw: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    match = FRONT_MATTER.match(raw)
    if not match:
        return meta, raw
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, raw[match.end() :]


def _app_text(text: str) -> str:
    return REFERENCE_LINK.sub(lambda m: f"`{m.group(1)}` (load_skill)", text).strip()


def _skill(path: Path) -> Skill:
    meta, body = _split(path.read_text(encoding="utf-8"))
    return Skill(
        name=meta.get("name") or path.parent.name,
        description=meta.get("description", ""),
        kind="skill",
        text=_app_text(body),
    )


def _reference(path: Path) -> Skill:
    _meta, body = _split(path.read_text(encoding="utf-8"))
    title = TITLE.search(body)
    return Skill(
        name=path.stem,
        description=title.group(1).strip() if title else path.stem,
        kind="reference",
        text=_app_text(body),
    )


@cache
def library(root: Path = SKILLS_ROOT) -> dict[str, Skill]:
    skills = [_skill(path) for path in sorted(root.glob("skills/*/SKILL.md"))]
    skills += [_reference(path) for path in sorted(root.glob("reference/*.md"))]
    return {skill.name: skill for skill in skills}


def catalog_text() -> str:
    """The list placed in the system prompt: skills first, then reference documents."""
    skills = sorted(library().values(), key=lambda skill: (skill.kind != "skill", skill.name))
    return "\n".join(f"- {skill.name} ({skill.kind}): {skill.description}" for skill in skills)


def text_of(name: str) -> str:
    skill = library().get(name)
    return skill.text if skill else ""
