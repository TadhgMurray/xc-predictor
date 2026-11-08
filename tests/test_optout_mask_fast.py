"""person_optout.maskTree skips leaf values by exact type before its
isinstance test (2026-11-08: ~2x faster on a big page's context). It must
do exactly what the walk before it did: the reference below is that walk,
verbatim, and both run over the same random contexts -- rows with and
without hidden people, search hits, dict / list / tuple subclasses, str and
int subclasses, shared rows, cycles, the skipped Flask keys and nesting past
MAX_DEPTH -- and must leave them identical.

    python -m pytest -q tests/test_optout_mask_fast.py
"""
import collections
import copy
import datetime
import decimal
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

import person_optout as O                                    # noqa: E402

HIDDEN = frozenset([27605295, 4242])


def _refMaskTree(obj, ids=None, depth=0, _seen=None):
    """maskTree as it was before 2026-11-08."""
    ids = O._STATE["ids"] if ids is None else ids
    if not ids or depth > O.MAX_DEPTH:
        return
    _seen = set() if _seen is None else _seen
    if id(obj) in _seen:
        return
    if isinstance(obj, dict):
        _seen.add(id(obj))
        O._maskDict(obj, ids)
        for k, v in obj.items():
            if k not in O._SKIP_KEYS and isinstance(v, (dict, list, tuple)):
                _refMaskTree(v, ids, depth + 1, _seen)
    elif isinstance(obj, (list, tuple)):
        _seen.add(id(obj))
        if isinstance(obj, list):
            obj[:] = [v for v in obj
                      if not (isinstance(v, dict) and O._maskDict(v, ids))]
        for v in obj:
            if isinstance(v, (dict, list, tuple)):
                _refMaskTree(v, ids, depth + 1, _seen)


class _Row(collections.OrderedDict):
    pass


class _List(list):
    pass


class _Str(str):
    pass


class _Int(int):
    pass


def _pid(r):
    return r.choice([27605295, 4242, 11, 12, "27605295", None, _Int(4242), 4242.0, "x"])


def _leaf(r):
    return r.choice(["Ann Lee", _Str("Bo Ray"), 7, 1.5, None, True, decimal.Decimal("1.2"),
                     datetime.date(2025, 9, 1), datetime.datetime(2025, 9, 1, 8), b"x",
                     "/athlete/27605295", "/athlete/4242?x=1", "/athlete/99"])


def _tree(r, depth, pool):
    kind = r.random()
    if depth > 9 or kind < 0.25:
        return _leaf(r)
    if pool and kind < 0.32:
        return r.choice(pool)                 # a shared node (and some cycles)
    if kind < 0.7:
        d = r.choice([dict, _Row])()
        if r.random() < 0.7:
            d[r.choice(["person_id", "best_person_id"])] = _pid(r)
        for k in r.sample(["name", "athlete_name", "label", "best_name", "first_name",
                           "last_name", "link", "href", "url", "school", "rows",
                           "results", "config", "request", "g", "team"], r.randint(0, 7)):
            d[k] = _tree(r, depth + 1, pool) if k in ("rows", "results", "team", "config",
                                                      "request", "g") else _leaf(r)
        pool.append(d)
        return d
    seq = [_tree(r, depth + 1, pool) for _ in range(r.randint(0, 6))]
    out = r.choice([list, _List, tuple])(seq)
    if not isinstance(out, tuple):
        pool.append(out)
        if pool and r.random() < 0.1:
            out.append(r.choice(pool))        # a cycle through a list
    return out


def test_the_walk_is_the_one_it_replaced():
    r = random.Random(20261108)
    for _ in range(3000):
        ctx = {"rows": _tree(r, 0, []), "athlete": _tree(r, 0, []), "request": _tree(r, 0, [])}
        a = copy.deepcopy(ctx)
        b = copy.deepcopy(ctx)
        _refMaskTree(a, HIDDEN)
        O.maskTree(b, HIDDEN)
        assert repr(a) == repr(b)


def test_a_big_board_is_masked_the_same():
    r = random.Random(3)
    rows = [{"person_id": r.choice([27605295, 1, 2, 3]), "name": "N%d" % i,
             "school": "S", "rating": r.uniform(400, 1300), "date": datetime.date(2025, 9, 1),
             "href": "/athlete/%d" % r.choice([4242, 5]), "splits": [1.0, 2.0]}
            for i in range(3000)]
    hits = [{"kind": "athlete", "label": "x", "link": "/athlete/%d" % r.choice([4242, 7])}
            for _ in range(200)]
    ctx = {"rows": rows, "hits": hits}
    a = copy.deepcopy(ctx)
    b = copy.deepcopy(ctx)
    _refMaskTree(a, HIDDEN)
    O.maskTree(b, HIDDEN)
    assert a == b and repr(a) == repr(b)
    assert any(x["name"] == O.HIDDEN_NAME for x in b["rows"])
    assert len(b["hits"]) < 200
