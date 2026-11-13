"""/meets browse views are computed once for the box (meets_filter,
2026-11-09): the same filters read the shared copy, other filters their own,
an empty answer is not kept, and a caller's edit stays its own."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import meets_filter as M  # noqa: E402


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, params=None):
        if "WITH picked" in sql:              # the view's own statement
            self.sql.append(sql)

    def fetchall(self):
        return [dict(r) for r in self.rows]

    def fetchone(self):
        return None


def _f(**kw):
    f = {"sport": "TF", "state": "", "school": "", "q": "", "year": None, "year_label": None,
         "champ": False, "unit": "", "kind": "", "units": {}, "level": "", "from": "", "to": ""}
    f.update(kw)
    return f


def test_browse_views_are_shared(tmp_path, monkeypatch):
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "tableOid", lambda cur, t, now=None: 7)
    monkeypatch.setattr(M, "hasMeetUnits", lambda cur: False)
    # the meet-date probe (backfill_meet_dates) is its own statement
    monkeypatch.setattr(M, "meetDatesIndexed", lambda cur, sport: False)
    rows = [{"meet_id": 1, "meet_name": "Big Ten", "date": "2026-02-27", "n_results": 900}]
    cur = _Cur(rows)
    a = M.filteredMeets(cur, _f(state="OH"))
    a[0]["meet_name"] = "edited"
    b = M.filteredMeets(cur, _f(state="OH"))
    assert b == rows and len(cur.sql) == 1
    M.filteredMeets(cur, _f(state="MI"))                 # other filters: own copy
    assert len(cur.sql) == 2
    empty = _Cur([])
    M.filteredMeets(empty, _f(q="nothing"))
    M.filteredMeets(empty, _f(q="nothing"))
    assert len(empty.sql) == 2                            # an empty answer is not kept
