"""upcoming_preview.hostCourse is read once an hour for the box (2026-11-12):
the same pattern reads the shared copy, another pattern its own, and a
pattern that matches nothing is read again."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import upcoming_preview as U  # noqa: E402


class _Cur:
    description = [("name",), ("n",), ("has_cell",)]

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(params[0])
        self.rows = [] if params[0] == "nowhere" else [("LaVern Gibson XC Course", 40, True)]

    def fetchall(self):
        return self.rows


def test_one_read_per_pattern(tmp_path, monkeypatch):
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    cur = _Cur()
    assert U.hostCourse(cur, r"la\s*vern\s*gibson") == "LaVern Gibson XC Course"
    assert U.hostCourse(cur, r"la\s*vern\s*gibson") == "LaVern Gibson XC Course"
    U.hostCourse(cur, r"rim\s*rock")
    assert U.hostCourse(cur, "nowhere") is None
    assert U.hostCourse(cur, "nowhere") is None
    assert cur.calls == [r"la\s*vern\s*gibson", r"rim\s*rock", "nowhere", "nowhere"]
