#!/usr/bin/env python3
"""
crest_check.py -- why a school shows no crest. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/crest_check.py "Washington St."
    $P scripts/crest_check.py "Washington St." --state WA --pool college_m

★ WHY (owner, 2026-11-04: "Wazzou still not there" -- the link went to the
  right school page, the predictions page drew no badge). The badge comes
  from school_logo.crestUrl: the start-up cache keyed by the EXACT school
  name, then a state (school_identity.teamState with a pool) and a level.
  This prints each step: the school_logo rows under that name and names
  like it, whether each file is on disk, and what crestState answers for
  the state and pools a page would pass.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("school")
    ap.add_argument("--state")
    ap.add_argument("--pool", action="append",
                    help="pools to try (default: none, college_m, college_f, hs_m, hs_f)")
    a = ap.parse_args()

    import school_logo as SL
    from database import getConn

    with getConn() as conn:
        cur = conn.cursor()
        lv = "COALESCE(level, '')" if SL.hasLevelColumn(cur, True) else "''"
        stem = a.school.rstrip(".").split()[0]
        cur.execute(f"""SELECT school, state, {lv}, path, shared, override
                        FROM school_logo WHERE school = %s OR school ILIKE %s
                        ORDER BY school = %s DESC, school LIMIT 40""",
                    (a.school, stem + "%", a.school))
        rows = cur.fetchall()
        print(f"school_logo rows for {a.school!r} and names starting {stem!r}:")
        for school, st, level, path, shared, override in rows:
            full = SL.pathFor(path)
            on_disk = bool(full and os.path.exists(full))
            serves = (path is not None and on_disk and (override or "").lower() != "none"
                      and (not shared or override is not None))
            print(f"  {school!r:40} state={st or '-':3} level={level or '-':8} "
                  f"file={'yes' if on_disk else 'NO'} shared={bool(shared)} "
                  f"override={override or '-'}  {'serves' if serves else 'DOES NOT SERVE'}")
        if not any(r[0] == a.school for r in rows):
            print(f"  ! no row under exactly {a.school!r}: the badge looks the name up exactly")
        conn.rollback()

    SL.loadCrests(getConn, force=True)
    print(f"\ncache rows under {a.school!r}: {SL._CRESTS['map'].get(a.school)}")
    try:
        from school_identity import contextState, teamState
        print(f"contextState: {contextState(a.school, a.state)!r}")
    except Exception as exc:                             # noqa: BLE001
        print(f"school_identity: {type(exc).__name__}: {exc}")
        teamState = None
    for pool in a.pool or [None, "college_m", "college_f", "hs_m", "hs_f"]:
        ts = teamState(a.school, pool, a.state) if (teamState and pool) else None
        print(f"  pool={pool or '-':10} teamState={ts!r:8} "
              f"crestState={SL.crestState(a.school, a.state, pool)!r} "
              f"url={SL.crestUrl(a.school, a.state, 64, pool)!r}")


if __name__ == "__main__":
    main()
