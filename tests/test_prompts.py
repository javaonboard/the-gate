"""Prompts are code, and nothing was checking them.

A pass over the repo to tidy up comments cut the body off every prompt in the
system. The code still imported, still ran, still returned answers. The editor
lost the list of what counts as a cut and started calling the clapperboard its
own shot; the orchestrator lost the rule that says lead with GO or NO-GO; the
merge prompt lost the placeholders it was being handed, so the labels it was
asked to judge never reached it.

None of that raises. It just gets quietly worse, on footage nobody re-checks.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for d in ("agents", "core") for p in (ROOT / d).rglob("*.py"))

# A prompt is a module-level string constant in SCREAMING_CASE holding
# instructions, so it is long and has spaces. MODEL = "gemini-3.7-flash" is not.
MIN_PROMPT = 60


def prompts() -> list[tuple[Path, str, str]]:
    found = []
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Name) and target.id.isupper()
                        and len(value.value) >= MIN_PROMPT and " " in value.value):
                    found.append((path, target.id, value.value))
    return found


def ids(items):
    return [f"{p.relative_to(ROOT).as_posix()}::{n}" for p, n, _ in items]


ALL = prompts()


def test_there_are_prompts_to_check():
    assert len(ALL) >= 10, "the finder stopped finding prompts, not that they went"


@pytest.mark.parametrize("path,name,body", ALL, ids=ids(ALL))
def test_a_prompt_finishes_its_sentence(path: Path, name: str, body: str):
    """A prompt that ends on a colon was cut off mid-thought.

    This is the exact shape the damage took: the sentence introducing a list
    survived, the list did not.
    """
    tail = body.rstrip()
    assert tail, f"{name} is empty"
    assert tail[-1] not in ":,-–—", (
        f"{name} ends on {tail[-1]!r}, so whatever it was introducing is gone:\n"
        f"  ...{tail[-70:]!r}"
    )


@pytest.mark.parametrize("path,name,body", ALL, ids=ids(ALL))
def test_a_prompt_says_more_than_it_promises(path: Path, name: str, body: str):
    """Announcing a list and then giving one line is the same failure, softer."""
    if not re.search(r":\s*$", body.split("\n\n")[0]):
        return
    rest = "\n\n".join(body.split("\n\n")[1:]).strip()
    assert len(rest) > 40, f"{name} opens a list and then stops"


def test_every_placeholder_a_prompt_is_handed_is_one_it_has():
    """`.format(labels=...)` on a string with no {labels} silently drops it.

    Python does not complain about a keyword the template never uses, so the
    call kept working and the model simply stopped being told what to judge.
    """
    missing = []
    for path in SOURCES:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        constants = {
            t.id: n.value.value
            for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "format"
                    and isinstance(node.func.value, ast.Name)):
                continue
            template = constants.get(node.func.value.id)
            if template is None:
                continue
            has = set(re.findall(r"\{(\w+)", template))
            for kw in node.keywords:
                if kw.arg and kw.arg not in has:
                    missing.append(
                        f"{path.relative_to(ROOT).as_posix()}:{node.lineno} "
                        f"{node.func.value.id}.format({kw.arg}=...) but the "
                        f"prompt has no {{{kw.arg}}}"
                    )
    assert not missing, "\n".join(missing)
