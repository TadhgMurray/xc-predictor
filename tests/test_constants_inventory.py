"""The constants inventory (scripts/constants_inventory.py ->
docs/REVIEW-ENGINE-2026-10-CONSTANTS.md) must cover the rating path: every
ALL_CAPS module-level name in its files, and every XCP_ switch they or the
two deploy scripts read, appears in the generated document with a category.
So a new constant cannot reach the rating path without a rule saying what it
is for and what the state-space engine does with it. And REVIEW, the "we
do not know yet" category, stays under 15% of the items.

    python -m pytest -q tests/test_constants_inventory.py
"""
import ast
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
SCRIPT = os.path.join(REPO, "scripts", "constants_inventory.py")
sys.path.insert(0, os.path.join(REPO, "scripts"))
import constants_inventory as ci                                # noqa: E402

CAPS = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
ROW = re.compile(r"^\| `([^`]+)` \| [^|]* \|.*\| \*\*([A-Z]+)\*\*")
PY_READ = re.compile(r"(?:environ\.(?:get|setdefault|pop)|getenv)\(\s*[\"'](XCP_[A-Z0-9_]+)[\"']"
                     r"|environ\[\s*[\"'](XCP_[A-Z0-9_]+)[\"']\s*\]"
                     r"|[\"'](XCP_[A-Z0-9_]+)[\"']\s+(?:not\s+)?in\s+\S*environ")
SH_READ = re.compile(r"\$\{?(XCP_[A-Z0-9_]+)")


def _capsNames(path):
    """ALL_CAPS module-level assignment targets, read here independently of
    the generator: module body plus module-level if/try/with, never the
    `if __name__ == "__main__":` block."""
    tree = ast.parse(open(os.path.join(REPO, path), encoding="utf-8").read())
    names, stack = set(), list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.If):
            if "__name__" not in ast.dump(node.test):
                stack += node.body + node.orelse
            continue
        if isinstance(node, ast.Try):
            stack += node.body + [s for h in node.handlers for s in h.body] + node.orelse
            continue
        if isinstance(node, ast.With):
            stack += node.body
            continue
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None
                   else [])
        for t in targets:
            for n in ([t] if isinstance(t, ast.Name) else getattr(t, "elts", [])):
                if isinstance(n, ast.Name) and CAPS.match(n.id):
                    names.add(n.id)
    return names


def _envReads():
    names = set()
    for f in ci.FILES:
        for m in PY_READ.finditer(open(os.path.join(REPO, f), encoding="utf-8").read()):
            names.add(next(g for g in m.groups() if g))
    for f in ci.SHELL_FILES:
        for line in open(os.path.join(REPO, f), encoding="utf-8"):
            if not line.lstrip().startswith("#"):
                names.update(SH_READ.findall(line))
    return names


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    out = tmp_path_factory.mktemp("inv") / "constants.md"
    run = subprocess.run([sys.executable, SCRIPT, "--out", str(out)],
                         capture_output=True, text=True, cwd=REPO)
    assert run.returncode == 0, run.stdout + run.stderr
    sections, current = {}, None
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            current = line[4:].strip().strip("`")
            sections[current] = {}
            continue
        m = ROW.match(line)
        if m and current:
            sections[current][m.group(1)] = m.group(2)
    return sections


def test_every_constant_is_listed_with_a_category(doc):
    valid = set(ci.CATEGORIES)
    missing, bad = [], []
    for f in ci.FILES:
        listed = doc.get(f, {})
        for name in sorted(_capsNames(f)):
            if name not in listed:
                missing.append(f"{f}:{name}")
            elif listed[name] not in valid:
                bad.append(f"{f}:{name} -> {listed[name]}")
    assert not missing, f"{len(missing)} constants missing from the inventory: {missing[:20]}"
    assert not bad, f"{len(bad)} constants without a valid category (add a rule): {bad[:20]}"


def test_every_switch_is_listed_with_a_category(doc):
    listed = doc.get("XCP_ switches", {})
    valid = set(ci.CATEGORIES)
    missing = sorted(n for n in _envReads() if n not in listed)
    bad = sorted(f"{n} -> {c}" for n, c in listed.items() if c not in valid)
    assert not missing, f"switches missing from the inventory: {missing}"
    assert not bad, f"switches without a valid category (add an ENV_RULES entry): {bad}"


def test_review_is_under_fifteen_percent(doc):
    cats = [c for rows in doc.values() for c in rows.values()]
    consts = [c for f, rows in doc.items() if f != "XCP_ switches" for c in rows.values()]
    assert cats and consts
    assert cats.count("REVIEW") < 0.15 * len(cats), (cats.count("REVIEW"), len(cats))
    assert consts.count("REVIEW") < 0.15 * len(consts), (consts.count("REVIEW"), len(consts))
