"""Every stylesheet's braces balance (2026-10-02). A stray `}` at the top
level is not an error a browser reports: it becomes part of the NEXT rule's
selector, the selector is invalid, and that whole rule is dropped. One
stray brace in style.css silently removed the topbar's Athletes/Coaches
switch styling on every desktop page."""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _strays(text):
    t = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    t = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', t)
    depth, line, bad = 0, 1, []
    for ch in t:
        if ch == "\n":
            line += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                bad.append(line)
                depth = 0
    return bad, depth


def test_stylesheets_balance():
    for path in glob.glob(os.path.join(ROOT, "racecast", "static", "*.css")):
        bad, depth = _strays(open(path, encoding="utf-8").read())
        assert not bad, f"{os.path.basename(path)}: stray '}}' at line(s) {bad}"
        assert depth == 0, f"{os.path.basename(path)}: {depth} unclosed '{{'"
