# Project: xc-predictor / tests
# File:    test_pack_pool_memo.py
# Purpose: the pack's pool memo keys on a person-season's FACTS, not its
#          person_id (speed_ratings._poolSeasonFacts, 2026-11-24). Teammates
#          with the same facts share an answer; one whose grade gate, pro
#          season, club season or hand listing differs never borrows a
#          teammate's pool. The pack is the one keyed on person_id, exactly.
#
#   python -m pytest -q tests/test_pack_pool_memo.py
import os
import sys
from datetime import date

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                             # noqa: E402
import pro_ability                                             # noqa: E402
import speed_ratings as sr                                     # noqa: E402

HAND_PRO = 29751385          # pool_resolve._PRO_SEASONS: (2026, 2026, "TF")


def _rows():
    out, rid = [], 0
    pids = [1, 2, 3, 4, 5, HAND_PRO, None, 6]
    for rep in range(3):                          # every key met again
        for meet, day in ((10, "2026-03-07"), (11, "2026-04-18")):
            for p in pids:
                for team, slug, school, src, grade in (
                        (501, None, "Central High", "anet", "11"),
                        (0, None, "Unattached", "anet", None),
                        (None, "MA_college_m_Some_U", "Some U", "tfrrs", "JR"),
                        (777, None, "Fast Track Club", "anet", None)):
                    rid += 1
                    out.append((rid, p, 1000.0 + rid % 7, grade, src, school,
                                day, "TF", 9, "M", 1600.0, 0, None, team, slug,
                                meet, -1))
    return [out[i:i + 50] for i in range(0, len(out), 50)]


def _tables(monkeypatch):
    ay = 2025                                    # spring 2026 = season 2025
    monkeypatch.setattr(sr, "_ANET_LEVELS", {501: "hs", 777: "club"})
    monkeypatch.setattr(sr, "_CLUB_PROS", ({777: 1}, {}))
    monkeypatch.setattr(sr, "_CLUB_MAJORITY", {(4, ay), (HAND_PRO, ay)})
    monkeypatch.setattr(sr, "_PRO_TEAMS", set())
    monkeypatch.setattr(sr, "_UNATTACHED_LEVEL", {("TF", 2): "college"})
    monkeypatch.setattr(sr, "_SEASON_LEVELS", (
        {((5, ay), "TF"): "pro"}, {(1, ay): "hs", (2, ay): "hs", (3, ay): "hs"}))
    monkeypatch.setattr(sr, "_PRO_SEASONS", {(4, ay)})
    monkeypatch.setattr(sr, "_COLLEGE_FIRST", {6: date(2023, 9, 2)})
    monkeypatch.setattr(sr, "_GRADE_UNTRUSTED", {(3, ay): ("8", "ms", "majority")})
    monkeypatch.setattr(pro_ability, "_ABLE", {(4, ay)})


def _pack(monkeypatch, key):
    if key:
        monkeypatch.setenv("XCP_PACK_POOL_KEY", key)
    else:
        monkeypatch.delenv("XCP_PACK_POOL_KEY", raising=False)
    return sr._packLoop(_rows(), date(2026, 11, 20), False)


def test_the_facts_keyed_memo_packs_what_the_person_keyed_one_does(monkeypatch):
    _tables(monkeypatch)
    a, b = _pack(monkeypatch, "person"), _pack(monkeypatch, None)
    assert a["a_uniq"] == b["a_uniq"] and a["t_uniq"] == b["t_uniq"]
    assert list(a["census"].items()) == list(b["census"].items())
    assert len(a["chunks"]) == len(b["chunks"])
    for x, y in zip(a["chunks"], b["chunks"]):
        assert all(np.array_equal(u, v) and u.dtype == v.dtype
                   for u, v in zip(x, y))
    # teammates 1 and 2 share facts; 3 (gate says grade 8) does not borrow
    pools = {}
    for pid, pool in a["a_uniq"]:
        pools.setdefault(pid, set()).add(pool)
    assert pools[1] == pools[2] and pools[3] != pools[1]
