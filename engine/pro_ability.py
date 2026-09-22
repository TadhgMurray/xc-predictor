#!/usr/bin/env python3
"""
pro_ability.py -- is this athlete-season fast enough to be pooled
professional? The ONE reader of pro_ability_season.

★ OWNER, 2026-09-22: "if they're sub 14:00? for men, or sub 15:30? for women
  put in pro, otherwise trust grade."

★★ WHY A MODULE OF ITS OWN, forty lines long. The engine and the site both
   have to ask this question, and they must get the same answer: a pool they
   disagree about is the exact failure pool_resolve's header records ("There
   were three implementations ... they disagreed on about a million
   athlete-seasons"). The engine's other facts live in speed_ratings.py,
   which the site cannot import -- it carries the pack, numpy and the solve.
   So the reader lives here, where four call sites can share it.

★ A SET, NOT A JOIN. The alternative was a LEFT JOIN in three query
  templates, which is three places for the academic-vs-calendar season key
  to be typed wrong (it already has been, twice, in those very joins). The
  table holds ONLY the able -- tens of thousands of rows, not the ~29M an
  every-season table would be -- so it fits in memory and the lookup is a
  tuple test.

⚠⚠⚠ ABSENT IS "NOT FAST ENOUGH"; EMPTY IS "NO VERDICT". Those are different
    and getting them the wrong way round demotes every professional in the
    corpus on the first run after a schema change. An athlete-season missing
    from a POPULATED table did not clear the bar. A table that is missing,
    empty, or unreadable yields None for everybody, and pool_resolve's gate
    treats None as no verdict and leaves pooling exactly as it was.

! ABSENCE ALSO COVERS "NO RATED MARK AT ALL", which the shortened table
  cannot tell from a slow one. That is harmless: such a season puts no rated
  row into any pool's anchor, and the gate can only ever demote.

Built by engine/build_pro_ability.py. The standard itself -- the two
thresholds and the curve they are measured on -- lives in pool_resolve
beside the gate that applies it.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ABLE = None


def loadProAbility(quiet=False):
    """The (person_id, academic_year) pairs that cleared the standard.
    Cached; an empty set means no verdict is available for anybody."""
    global _ABLE
    if _ABLE is not None:
        return _ABLE
    _ABLE = set()
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('pro_ability_season')")
            got = cur.fetchone()
            name = got[0] if not isinstance(got, dict) else list(got.values())[0]
            if name is None:
                if not quiet:
                    print("[pool] pro_ability_season not found -- "
                          "no ability gate")
                return _ABLE
            cur.execute("SELECT person_id, season FROM pro_ability_season")
            _ABLE = {(int(r[0]), int(r[1])) for r in cur.fetchall()}
        if not quiet:
            print(f"[pool] ability gate: {len(_ABLE):,} athlete-seasons "
                  f"clear the professional standard")
    except Exception as exc:                                 # noqa: BLE001
        if not quiet:
            print(f"[pool] pro_ability_season unavailable ({exc}) -- "
                  f"no ability gate")
    return _ABLE


def proAbilityFor(person_id, season, quiet=False):
    """True / False / None for one athlete-season.

    None is NO VERDICT -- no table, or nothing to look up with. Callers pass
    it straight to resolvePool(pro_ability=...), which leaves pooling
    untouched for None."""
    if person_id is None or season is None:
        return None
    able = loadProAbility(quiet=quiet)
    if not able:
        return None
    try:
        return (int(person_id), int(season)) in able
    except (TypeError, ValueError):
        return None


def reset():
    """Drop the cache. For tests and for a process that rebuilt the table."""
    global _ABLE
    _ABLE = None
