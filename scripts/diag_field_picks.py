#!/usr/bin/env python3
"""
diag_field_picks.py -- rebuild a championship's projected field and say how
each team got in (owner, 2026-11-16: North Central (Ill.) projected 2nd in
its D3 regional, yet not in the nationals field).

READ-ONLY, and slow: like the nightly (build_qualified_fields), it predicts
every round that has not run (upcoming_preview.qualifiedFieldInfo, live).

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_field_picks.py --meet 27292 --day 2026-11-21 --div 1
    ... --school "North Central (Ill.)"      # where one team stood

Prints, per round: its automatic team(s), then the at-large picks in the
order they were made, with the round each came from.
"""
import argparse
import collections
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--meet", type=int, required=True, help="last season's championship meet id")
    ap.add_argument("--source", default="tfrrs")
    ap.add_argument("--day", required=True, help="this year's date of the championship (ISO)")
    ap.add_argument("--div", type=int, required=True)
    ap.add_argument("--school", action="append", default=[])
    ap.add_argument("--compare", action="store_true",
                    help="also list the at-large picks by ratings and by each wins mode")
    a = ap.parse_args(argv)
    if a.compare:
        os.environ.setdefault("XCP_ATLARGE_BY", "wins")

    import psycopg2.extras
    from database import getConn
    import upcoming_preview as U

    with getConn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET statement_timeout = '900s'")
        plan = U.pastPlan(cur, a.meet, a.source, a.day)
        if not plan:
            print("no plan for that meet and day")
            return 1
        names = {}
        cur.execute("SELECT meet_id, meet_name FROM meets_tfrrs WHERE sport = 'XC'")
        for r in cur.fetchall():
            names[int(r["meet_id"])] = r["meet_name"]
        # ★ EACH ROUND AS THE SELECTION SAW IT: its rows (pid, school,
        #   seconds, rating) and the team order made from them
        import qualifying as Q
        seen_rounds = {}
        _orig = Q.selectField

        call = {}

        def _spy(rule, rounds, *a, **k):
            seen_rounds.update(rounds)
            args = list(a)
            if len(args) >= 4 and args[3] is not None:
                # the season's record, read once, for the variants below
                real, memo = args[3], {}

                def once(squads):
                    if "r" not in memo:
                        memo["r"] = real(squads)
                    return memo["r"]
                args[3] = once
            call.update(rule=rule, rounds=rounds, args=args, kw=k)
            return _orig(rule, rounds, *args, **k)
        Q.selectField = _spy
        why = {}
        try:
            field, info = U.qualifiedFieldInfo(cur, plan, a.div, live=True, why=why)
        finally:
            Q.selectField = _orig
        conn.rollback()

    def rname(k):
        mid = k[1] if isinstance(k, tuple) else k
        return names.get(int(mid), str(mid)) if str(mid).lstrip("-").isdigit() else str(k)

    import qualifying as Q

    def clock(sec):
        return f"{int(sec // 60)}:{sec % 60:04.1f}" if sec else "?"
    if seen_rounds:
        print("\neach round's team order, as the selection made it (top 6):")
        for k, rows in seen_rounds.items():
            order = Q.teamOrder(rows)
            print(f"  {rname(k)}  ({len(rows)} runners)")
            ranked = sorted(rows, key=lambda r: r[2])
            for s in order[:6]:
                mine = [(i + 1, r) for i, r in enumerate(ranked) if r[1] == s][:5]
                print(f"     {s:<28} " + "  ".join(f"#{p} {clock(r[2])}" for p, r in mine))
    picks = why.get("picks") or {}
    print(f"{plan.get('name')} on {a.day}, race {a.div}: "
          f"{len(field or {})} runners, info {info}")
    print("\nautomatic:")
    for k, schools in (picks.get("auto") or {}).items():
        print(f"  {rname(k):<70} {', '.join(schools)}")
    pts = picks.get("points") or []
    print(f"\nat-large, in pick order (by {picks.get('by') or 'ratings'}; "
          f"wins-losses against the board and the teams already in):")
    for i, (k, school) in enumerate(picks.get("at_large") or [], 1):
        wl = f"{pts[i - 1][0]}-{pts[i - 1][1]}" if i <= len(pts) else ""
        print(f"  {i:>2}. {school:<34} {wl:<6} from {rname(k)}")
    if a.compare and call.get("args") and len(call["args"]) >= 4:
        print("\nat-large picks by each method:")
        for mode in ("rating",) + Q.AT_LARGE_MODES:
            ex = {}
            args = list(call["args"])
            args[2] = ex
            rule = dict(call["rule"], at_large_mode=mode)
            if mode == "rating":
                args[3] = None
            Q.selectField(rule, call["rounds"], *args, **call["kw"])
            print(f"  {mode:<7} " + ", ".join(s for _k, s in ex.get("at_large") or []))
    if a.school and field:
        by = collections.Counter(field.values())
        print()
        for s in a.school:
            hit = {k: v for k, v in by.items() if s.lower() in (k or "").lower()}
            print(f"{s!r} in the field: {hit or 'NO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
