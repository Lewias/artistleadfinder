"""Checks the skill system and grades saved outputs against the test scenarios.

    python3 tests/check.py                 # structure + golden examples in tests/examples/
    python3 tests/check.py tests/outputs   # also grade your own runs saved as <scenario-id>.md

Standard library only. An output file is the skill's answer for one scenario (see
tests/README.md). Grading is deliberately mechanical: it catches wrong labels and stages,
missing or forbidden drafts, corporate or pushy phrases, prices not in offers.md, made-up
percentages, and unfilled placeholders. It does not replace reading the drafts yourself.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
REFERENCE = ROOT / "reference"
TESTS = ROOT / "tests"
LABELS = ("ready", "hot", "warm", "cold", "stop")
MAX_CHARS = {"instagram": 300, "imessage": 300}
MAX_EMAIL_WORDS = 140


def front_matter(text: str) -> dict[str, str]:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    meta: dict[str, str] = {}
    for line in (match.group(1) if match else "").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta


def check_structure() -> list[str]:
    problems = []
    names = set()
    for skill_file in sorted(SKILLS.glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        meta = front_matter(text)
        name = skill_file.parent.name
        names.add(name)
        if meta.get("name") != name:
            problems.append(f"{skill_file}: front matter name must be '{name}'")
        if len(meta.get("description", "")) < 40:
            problems.append(f"{skill_file}: description is missing or too short")
        for ref in re.findall(r"\.\./\.\./reference/([\w.-]+\.md)", text):
            if not (REFERENCE / ref).exists():
                problems.append(f"{skill_file}: missing reference {ref}")
        if "guardrails.md" not in text:
            problems.append(f"{skill_file}: must point to reference/guardrails.md")
    scenarios = load_scenarios()
    ids = [s["id"] for s in scenarios]
    if len(ids) != len(set(ids)):
        problems.append("scenarios.json: duplicate ids")
    for scenario in scenarios:
        if scenario["skill"] not in names:
            problems.append(f"scenario {scenario['id']}: unknown skill {scenario['skill']}")
    return problems


def load_scenarios() -> list[dict]:
    return json.loads((TESTS / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]


def banned_phrases() -> list[str]:
    """Quoted phrases from the 'Не писать' sections of voice.md."""
    voice = (REFERENCE / "voice.md").read_text(encoding="utf-8")
    phrases = []
    for section in re.findall(r"## Не писать \((?:корпоратив|давление)\)\n(.*?)(?=\n## |\Z)", voice, re.S):
        phrases += [p.lower() for p in re.findall(r'"([^"]+)"', section)]
    return phrases


def allowed_prices() -> set[int]:
    offers = (REFERENCE / "offers.md").read_text(encoding="utf-8")
    return {int(p.replace(",", "")) for p in re.findall(r"\$(\d[\d,]*)", offers)}


def drafts_of(output: str) -> list[str]:
    """Blockquoted lines are drafts; consecutive '>' lines form one message."""
    messages, current = [], []
    for line in output.splitlines():
        if line.startswith(">"):
            current.append(line[1:].strip())
        elif current:
            messages.append(" ".join(part for part in current if part))
            current = []
    if current:
        messages.append(" ".join(part for part in current if part))
    return [m for m in messages if m]


def lint_draft(text: str, channel: str, banned: list[str], prices: set[int]) -> list[str]:
    issues = []
    low = text.lower()
    issues += [f"banned phrase '{p}'" for p in banned if p in low]
    for amount in re.findall(r"\$(\d[\d,]*)", text):
        if int(amount.replace(",", "")) not in prices:
            issues.append(f"price ${amount} is not in offers.md")
    if re.search(r"\d+\s?%", text):
        issues.append("percentage in a draft (unverified stats?)")
    if re.search(r"\[[^\]]+\]", text):
        issues.append("unfilled [placeholder]")
    if channel in MAX_CHARS and len(text) > MAX_CHARS[channel]:
        issues.append(f"too long for {channel} ({len(text)} chars)")
    if channel == "email" and len(text.split()) > MAX_EMAIL_WORDS:
        issues.append(f"email too long ({len(text.split())} words)")
    # voice.md: no punctuation at all, except inside links, {{placeholders}} and an email's "subject:" label.
    bare = re.sub(r"https?://\S+|\{\{[^}]+\}\}", " ", text)
    bare = re.sub(r"^\s*subject:", " ", bare, flags=re.I)
    marks = sorted(set(re.findall(r"[.,!?;:—–…\"“”«»]", bare)))
    if marks:
        issues.append(f"punctuation in a draft: {' '.join(marks)}")
    return issues


def grade(scenario: dict, output: str, banned: list[str], prices: set[int]) -> list[str]:
    expect = scenario["expect"]
    fails = []
    low = output.lower()
    header = re.search(r"\b(" + "|".join(LABELS) + r")\b\s+(\d{1,3})\s*/\s*100", output)
    if "label" in expect:
        if not header:
            fails.append("no '<label> <score>/100' header")
        elif header.group(1) not in expect["label"]:
            fails.append(f"label {header.group(1)}, expected {expect['label']}")
    if "score_range" in expect and header:
        low_bound, high_bound = expect["score_range"]
        if not low_bound <= int(header.group(2)) <= high_bound:
            fails.append(f"score {header.group(2)} outside {expect['score_range']}")
    if "stage" in expect:
        stage = re.search(r"Этап:\s*`?([a-z_]+)", output)
        if not stage:
            fails.append("no 'Этап: <stage>' line")
        elif stage.group(1) not in expect["stage"]:
            fails.append(f"stage {stage.group(1)}, expected {expect['stage']}")
    if "next_action_any" in expect:
        line = next((ln for ln in output.splitlines() if "Следующее действие" in ln), "").lower()
        if not any(word in line for word in expect["next_action_any"]):
            fails.append(f"next action should mention one of {expect['next_action_any']}")
    if expect.get("output_must_include_any") and not any(
        word.lower() in low for word in expect["output_must_include_any"]
    ):
        fails.append(f"analysis should mention one of {expect['output_must_include_any']}")
    drafts = drafts_of(output)
    if expect.get("draft") == "required" and not drafts:
        fails.append("a draft is required")
    if expect.get("draft") == "none" and drafts:
        fails.append("no draft must be written here")
    joined = " ".join(drafts).lower()
    if drafts and expect.get("draft_must_include_any") and not any(
        word.lower() in joined for word in expect["draft_must_include_any"]
    ):
        fails.append(f"draft should include one of {expect['draft_must_include_any']}")
    for word in expect.get("draft_must_not_include", []):
        if word.lower() in joined:
            fails.append(f"draft must not include '{word}'")
    for draft in drafts:
        fails += [f"draft: {issue}" for issue in lint_draft(draft, scenario["channel"], banned, prices)]
    return fails


def main() -> int:
    problems = check_structure()
    for problem in problems:
        print(f"STRUCTURE  {problem}")
    banned, prices = banned_phrases(), allowed_prices()
    dirs = [TESTS / "examples"] + [Path(arg) for arg in sys.argv[1:]]
    failed = bool(problems)
    for directory in dirs:
        print(f"\n== {directory}")
        for scenario in load_scenarios():
            path = directory / f"{scenario['id']}.md"
            if not path.exists():
                print(f"  -     {scenario['id']}  (no output)")
                continue
            fails = grade(scenario, path.read_text(encoding="utf-8"), banned, prices)
            failed |= bool(fails)
            print(f"  {'FAIL' if fails else 'PASS'}  {scenario['id']}")
            for fail in fails:
                print(f"        - {fail}")
    print("\nstructure OK" if not problems else "\nstructure problems found")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
