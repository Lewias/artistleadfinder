"""Sales skills: the bundled playbook and each account's local Markdown copies.

The source folder is also a Claude Code plugin: `skills/<name>/SKILL.md` files with front matter
(`name`, `description`) and shared, user-editable `reference/*.md` files (offers, stages,
voice…). The assistant's system prompt lists only names and descriptions; full texts are
read on demand with the `load_skill` tool, so the prompt stays small and cacheable.
Untouched defaults from older builds can be updated without replacing the user's edits.
"""

import re
from dataclasses import dataclass, replace
from functools import cache
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from ..errors import UserError

# The frozen core bundles the folder next to this module (see scripts/build-core.py).
BUNDLED = Path(__file__).parent / "skills_library"
SKILLS_ROOT = BUNDLED if BUNDLED.is_dir() else Path(__file__).parents[3] / "producer-sales-skills"
PREVIOUS_DEFAULTS = {
    "closing": "87d6cc1724fb5537b42113b6e43191c1c7a7a6cd6eefc213a005db43e72895ad",
    "conversation-analysis": "b2675445a1b2674c9b2f744e3d7fae54cfc2555ccfc28ad2a0df5eeafd736ebc",
    "deal-stages": "852f85eb01441810f7897c63fb5e39f4769b1200350d87738168f13da5a8a32a",
    "follow-up": "ddd8bea9ad1870a8210e9014183c417cc3b2405313bc76aa815193c9b4a16364",
    "lead-qualification": "a41dc96c96ef0c89d760b216a454e80fa76914b247b612fd8cfb36f6feaac266",
    "negotiation": "0dcddc086bc48dcc35873213c2f945a07e5fdd9811e628d4c44591097b28f768",
    "objection-handling": "d2c50dafed702750fb308710ded8aea62f8b59ef03982e839cbd754145063546",
    "offers": "27f46cf649f2741fbde4a107bb92a2bb968c49fce3ac03978586441ef7b5381d",
    "voice": "1a7987672204dfba3ffcb42d40b247371d063944ed8da819b94a367c5817ad9a",
}
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
                previous = PREVIOUS_DEFAULTS.get(name)
                if previous and sha256(text.encode()).hexdigest() == previous:
                    self._write(path, default.text)
                    text = default.text
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
