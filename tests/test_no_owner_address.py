"""The owner's personal address is in no page, mail or outbound request
(owner, 2026-11-03: "please tell me nothing send my personal info")."""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PERSONAL = re.compile(r"gmail\.com|contact:\s*tadhg\b", re.I)


def _files():
    for d in ("racecast", "scripts", "sources", "engine", "deploy", "tfrrs", "backfill"):
        for root, _dirs, files in os.walk(os.path.join(_ROOT, d)):
            for f in files:
                if f.endswith((".py", ".html", ".js", ".sh", ".txt")):
                    yield os.path.join(root, f)


def test_no_personal_address_in_code():
    bad = []
    for p in _files():
        try:
            s = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for m in _PERSONAL.finditer(s):
            bad.append(f"{os.path.relpath(p, _ROOT)}: {s[max(0, m.start() - 30):m.end() + 10]!r}")
    assert not bad, "\n".join(bad[:20])


def test_no_template_names_who_decided():
    for f in os.listdir(os.path.join(_ROOT, "racecast", "templates")):
        s = open(os.path.join(_ROOT, "racecast", "templates", f), encoding="utf-8").read()
        assert "decided_by" not in s, f
