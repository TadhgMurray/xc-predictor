"""season_level.audit prints a NULL level (2026-10-29: it died on one after
4h 19m, before the commit), and the write commits before the audit runs.

    python -m pytest -q tests/test_season_level_audit.py
"""
import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(ROOT, d))

import season_level as S                                         # noqa: E402


class Cur:
    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return [("hs", "numeric", 10), (None, "word", 61602), ("pro", None, None)]


def test_audit_prints_a_null_level():
    out = io.StringIO()
    with redirect_stdout(out):
        S.audit(Cur())
    assert "(none)" in out.getvalue() and "61,602" in out.getvalue()


def test_the_write_commits_before_the_audit():
    src = io.open(os.path.join(ROOT, "engine", "season_level.py"), encoding="utf-8").read()
    body = src[src.index("def main("):]
    assert body.index("conn.commit()") < body.index("audit(cur)")
