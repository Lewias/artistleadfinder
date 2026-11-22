"""Sales skills: the bundled playbook and each account's local Markdown copies.

The source folder is also a Claude Code plugin: `skills/<name>/SKILL.md` files with front matter
(`name`, `description`) and shared, user-editable `reference/*.md` files (offers, stages,
voice…). The assistant's system prompt lists only names and descriptions; full texts are
read on demand with the `load_skill` tool, so the prompt stays small and cacheable. The
guardrails reference is the exception: it is short and always applies, so it goes into the
prompt in full. All of this is text placed in the prompt: nothing in it is executed.
"""

import re
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from ..errors import UserError

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


class LocalRules:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "sales-rules"
        self.lock = RLock()

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.md"

    def _write(self, path: Path, text: str) -> None:
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", delete=False, dir=self.root, suffix=".tmp"
            ) as file:
                temporary = Path(file.name)
                file.write(text + "\n")
            temporary.replace(path)
        except (OSError, UnicodeError):
            raise UserError(f"Не удалось сохранить локальный документ {path.name}.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def initialize(self) -> None:
        with self.lock:
            for name, skill in library().items():
                path = self._path(name)
                if not path.exists():
                    self._write(path, skill.text)

    def read(self, name: str) -> Skill:
        default = library().get(name)
        if default is None:
            raise UserError(f"Нет скилла {name}. Есть: {', '.join(library())}.")
        with self.lock:
            path = self._path(name)
            try:
                text = path.read_text(encoding="utf-8-sig").rstrip("\n")
            except FileNotFoundError:
                self._write(path, default.text)
                text = default.text
            except (OSError, UnicodeError):
                raise UserError(f"Не удалось прочитать локальный документ {path.name}.") from None
        return replace(default, text=text)

    def edit(self, name: str, find: str, replacement: str) -> bool:
        with self.lock:
            current = self.read(name).text
            if find:
                if current.count(find) != 1:
                    raise UserError("Нужен один точный фрагмент из текущего текста документа.")
                updated = current.replace(find, replacement, 1)
            else:
                addition = replacement.strip()
                if not addition:
                    raise UserError("Укажите текст, который нужно добавить.")
                if f"\n{addition}\n" in f"\n{current}\n":
                    return False
                updated = f"{current}\n\n{addition}" if current else addition
            if current == updated:
                return False
            self._write(self._path(name), updated)
            return True


def text_of(name: str, local: LocalRules | None = None) -> str:
    if name not in library():
        return ""
    skill = local.read(name) if local is not None else library()[name]
    return skill.text
