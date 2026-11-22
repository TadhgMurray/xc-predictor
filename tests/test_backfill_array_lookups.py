"""backfill_normalize's array lookups (2026-11-22, _IntMap / _WxStore /
_strKeyed): the per-person maps, the weather grid and the anet geometry in
layouts a forked worker reads without writing -- and the SAME answers the
dicts gave, row by row, through the block views row_fn reads."""
import datetime as dt
import os
import random
import sys
import types

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: (
        {} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import backfill_normalize as B                                    # noqa: E402

rnd = random.Random(11)
PIDS = list(range(1, 400)) + [10 ** 12, 0]
LV = ["hs", "ms", "college", "pro", "open"]


class _Cur:
    """The loaders' statements, answered from lists (repeats included: the
    LAST one must win, as the dict's assignment kept it)."""

    data = None

    def __init__(self):
        self.rows = []
        if _Cur.data is None:
            _Cur.data = self._make()

    @staticmethod
    def _make():
        return {
            "athletes": [(a, rnd.choice("MF")) for a in PIDS + [None, 500, 501]],
            "person_gender": [(p, rnd.choice(["M", "F", "X"])) for p in PIDS[::2]]
            + [(3, "F"), (3, "M")],
            "athlete_season_level": [(p, ay, sp, rnd.choice(LV))
                                     for p in PIDS for ay in range(2018, 2023)
                                     for sp in ("XC", "TF", "ALL") if rnd.random() < 0.5]
            + [(5, 2020, "XC", "hs"), (5, 2020, "XC", "pro"), (None, 2020, "XC", "hs")],
            "grade_fix": [(p, ay, rnd.choice(["9", "12", "FR", None]), rnd.choice(LV + [None]))
                          for p in PIDS for ay in range(2018, 2023) if rnd.random() < 0.4]
            + [(7, 2021, "9", None), (7, 2021, "10", "hs")],
            "college_first_season": [(p, rnd.choice([2019, 2020, 2021])) for p in PIDS[::3]],
        }

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self.rows = [("x",)]
            return
        for t in ("person_gender", "athlete_season_level", "grade_fix",
                  "college_first_season", "athletes"):
            if f"FROM {t}" in sql or f"FROM   {t}" in sql:
                self.rows = list(self.data[t])
                return
        raise AssertionError(sql)

    def fetchone(self):
        return self.rows[0]

    def __iter__(self):
        return iter(self.rows)


def _rows(n):
    out = []
    for i in range(n):
        r = [None] * 14
        r[B._ID], r[B._SRC], r[B._SCHOOL] = i, rnd.choice(["anet", "tfrrs"]), "S"
        r[B._PERSON] = rnd.choice(PIDS + [None, 777])
        r[B._AID] = rnd.choice(PIDS + [None, 500, 9999])
        r[B._GRADE] = rnd.choice([None, 7, 9, 12])
        r[B._DATE] = rnd.choice(["2019-10-01", "2020-08-20", "2021-03-03", "2022-11-30",
                                 None, "bad", "1999-01-01"])
        out.append(tuple(r))
    return out


def test_the_block_views_answer_as_the_dicts_did(monkeypatch):
    monkeypatch.setenv("XCP_BACKFILL_ARRAY_LOOKUPS", "0")
    g0, s0 = B._loadGenders(_Cur()), B._loadSeasonLevels(_Cur())
    monkeypatch.setenv("XCP_BACKFILL_ARRAY_LOOKUPS", "1")
    g1, s1 = B._loadGenders(_Cur()), B._loadSeasonLevels(_Cur())
    assert isinstance(g1, B._GenderMap) and isinstance(s1[0], B._SportMaps)
    assert all(isinstance(m, B._IntMap) for m in s1[1:])
    assert [len(x) for x in (g0,) + s0] == [len(x) for x in (g1,) + s1]
    for k in list(g0) + [("pg", 99999), 123456, ("x", 1)]:      # the maps, key by key
        assert g1.get(k) == g0.get(k)
    for a, b in zip(s0, s1):
        for k in list(a) + [12345, (5 * 10000 + 2020, "TF")]:
            assert b.get(k) == a.get(k)
    rows = _rows(3000)
    for sport in ("XC", "TF"):
        for i in range(0, len(rows), 97):
            block = rows[i:i + 97]
            g, sl, cf = B._lookupViews(block, g1, s1, s1[3], sport)
            assert all(type(x) is dict for x in (g, cf) + sl)
            for r in block:
                gen = B._rowGender(g, r[B._AID], r[B._PERSON])
                assert gen == B._rowGender(g0, r[B._AID], r[B._PERSON])
                for gender in ("M", "F", None):
                    assert B._seasonPool(r, gender, sl, cf, sport) == \
                        B._seasonPool(r, gender, s0, s0[3], sport)


def test_the_weather_and_geometry_stores_answer_as_the_dicts_did():
    wx = {(round(30 + i * 0.25, 2), round(250.0 + (i % 7) * 0.25, 2),
           (dt.date(2020, 1, 1) + dt.timedelta(days=i % 40)).isoformat()):
          (rnd.random(), None if i % 5 == 0 else rnd.random(), 0.0, -0.0, 1e-300)
          for i in range(500)}
    st = B._WxStore.fromDict(wx)
    assert isinstance(st, B._WxStore) and len(st) == len(wx)
    asks = list(wx) + [(30.0, 250.0, "2020-1-01"), (30.0, 250.0, "2020-01-02T00:00"),
                       (99.0, 1.0, "2020-01-01"), (30.0, 250.0, "2030-01-01")]
    for _ in range(2):                                  # the memo answers the same
        for k in asks:
            got, want = st.get(k), wx.get(k)
            assert got == want and repr(got) == repr(want)
    assert B._WxStore.fromDict({(1.0, 2.0, "2020-01-01"): (1, 2.0, 3.0, 4.0, 5.0)}) is not None
    assert type(B._WxStore.fromDict({(1.0, 2.0, "x"): (1.0,) * 5})) is dict

    anet = {(m, d, e): rnd.choice([("flat", 400), ("flat", 400.0), ("banked", 200), (None, 300)])
            for m in range(30) for d in (m * 10, None) for e in (1, 2, None)}
    old, new = B.GeometryIndex(dict(anet), {}), B.GeometryIndex(dict(anet), {})
    new._anet_s, new._anet = B._strKeyed(new._anet), None
    for m in range(32):
        for d in (m * 10, None, 7):
            for e in (1, 2, None, 3):
                a, b = old.lookup("anet", m, d, e), new.lookup("anet", m, d, e)
                assert a == b and repr(a) == repr(b)
