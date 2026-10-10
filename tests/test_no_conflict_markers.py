"""No merge-conflict markers left in source, templates, CSS or JS.

A stray `=======` line in race.css (2026-10-10) made browsers drop the next
rule, and no other test noticed: CSS and templates parse around it.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ("racecast", "engine", "scripts", "model", "deploy", "tests", "backfill")
EXTS = (".py", ".html", ".css", ".js", ".sh", ".sql")
MARK = re.compile(r"^(<<<<<<< |=======$|>>>>>>> )", re.M)


def test_no_conflict_markers():
    bad = []
    for d in DIRS:
        for base, dirs, files in os.walk(os.path.join(ROOT, d)):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", "node_modules")]
            for f in files:
                if f.endswith(EXTS) and f != "test_no_conflict_markers.py":
                    p = os.path.join(base, f)
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        for m in MARK.finditer(fh.read()):
                            bad.append(f"{os.path.relpath(p, ROOT)}: {m.group(0)!r}")
    assert not bad, "\n".join(bad)
