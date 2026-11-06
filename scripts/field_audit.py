#!/usr/bin/env python3
"""
field_audit.py -- who we guessed would run, against who did, on the
forecasts frozen before each race (meet_forecast, or a pred_ archive).
READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/field_audit.py --from 2026-09-01 --to 2026-11-01
    XCP_FORECAST_TABLE=pred_meet_forecast $P scripts/field_audit.py --from 2026-10-27 --to 2026-11-01
    XCP_FORECAST_TABLE=pred_meet_forecast $P scripts/field_audit.py --from 2026-11-03 --to 2026-11-10 --made-from 2026-11-03

★ WHY (owner's run, 2026-11-02). score_frozen_forecasts' "listed" said our
  fields held 58% of the runners who finished. Places and team scores are
  only as good as the field, and the field is a guess: the schools at the
  meet's last edition (upcoming_preview._meetPlanUncached), each school's
  current squad fastest first, at most seven or what it brought last time
  (predict.squadCap). This splits the finishers we missed by why:

    school not in our field    a school that did not run the last edition
                               (or a meet with none)
    team ran more than we had  the school was there, and more of its runners
                               finished than we listed
    the wrong runners          the school was there with as many as we
                               listed, but some were other people
    no school                  unattached finishers

  and counts the other way: runners we listed who did not finish (a DNS,
  another race of the meet, or a guess that was wrong).

  By race kind (race_rows.divType on the race's label: varsity, JV and
  the like, other) because a JV or open race takes everyone and a varsity
  race seven.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

CAUSES = ("listed", "school not in our field", "team ran more than we had",
          "the wrong runners", "no school")
KINDS = {2: "varsity", 0: "jv / frosh / open", 1: "other"}


def _key(school):
    return (school or "").strip().lower() or None


def auditRace(fc, finishers):
    """{cause: finishers} and {"listed_n", "listed_ran"} for one race. Pure."""
    listed = [r for r in fc.get("runners") or [] if r.get("person_id") is not None]
    ids = {int(r["person_id"]) for r in listed}
    ours_by_school = {}
    for r in listed:
        ours_by_school[_key(r.get("school"))] = ours_by_school.get(_key(r.get("school")), 0) + 1
    ran = [f for f in finishers if f.get("person_id") is not None]
    ran_ids = {int(f["person_id"]) for f in ran}
    theirs_by_school = {}
    for f in ran:
        k = _key(f.get("school"))
        theirs_by_school[k] = theirs_by_school.get(k, 0) + 1
    out = {c: 0 for c in CAUSES}
    for f in ran:
        k = _key(f.get("school"))
        if int(f["person_id"]) in ids:
            out["listed"] += 1
        elif k is None:
            out["no school"] += 1
        elif k not in ours_by_school:
            out["school not in our field"] += 1
        elif theirs_by_school[k] > ours_by_school[k]:
            out["team ran more than we had"] += 1
        else:
            out["the wrong runners"] += 1
    return out, {"listed_n": len(ids), "listed_ran": len(ids & ran_ids)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--made-from", default=None,
                    help="only forecasts made on or after this day (a field rule's start: "
                         "the JV / open rule went in 2026-11-02)")
    a = ap.parse_args()

    import psycopg2.extras
    import meet_forecast as MF
    import race_rows as RR
    from database import getConn
    from meet_recap import actualByDiv

    tot = {}
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        cur.execute(f"""SELECT source, meet_id, div_id, meet_date, made_on, race_label,
                               runners, teams, state, model_basis, model_version
                        FROM   {MF.TABLE}
                        WHERE  meet_date >= %s AND meet_date <= %s
                          AND  meet_date < CURRENT_DATE
                          AND  (%s::date IS NULL OR made_on::date >= %s::date)""",
                    (a.lo, a.hi, a.made_from, a.made_from))
        stored = [MF._rowOut(r) for r in cur.fetchall()]
        by_meet = {}
        for fc in stored:
            by_meet.setdefault((fc["source"], int(fc["meet_id"])), []).append(fc)
        for (src, mid), fcs in by_meet.items():
            try:
                actual = actualByDiv(cur, mid, src)
            except Exception as exc:                 # noqa: BLE001
                conn.rollback()
                print(f"  {src}:{mid}: {exc}")
                continue
            for fc in fcs:
                fin = actual.get(int(fc["div_id"]))
                if not fin:
                    continue
                got, prec = auditRace(fc, fin)
                kind = KINDS.get(RR.divType(fc.get("race_label")), "other")
                for k in (kind, "ALL"):
                    t = tot.setdefault(k, {"races": 0, "listed_n": 0, "listed_ran": 0,
                                           **{c: 0 for c in CAUSES}})
                    t["races"] += 1
                    for c in CAUSES:
                        t[c] += got[c]
                    t["listed_n"] += prec["listed_n"]
                    t["listed_ran"] += prec["listed_ran"]
        conn.rollback()

    print(f"{MF.TABLE}, races {a.lo}..{a.hi}: the finishers, by whether we listed them "
          f"and why not")
    print(f"  {'':20} {'races':>6} {'finishers':>10} "
          + " ".join(f"{c[:22]:>22}" for c in CAUSES) + f" {'listed, ran':>12}")
    for k in [v for v in KINDS.values() if v in tot] + ["ALL"]:
        t = tot.get(k)
        if not t:
            continue
        n = sum(t[c] for c in CAUSES) or 1
        print(f"  {k:20} {t['races']:>6} {n:>10,} "
              + " ".join(f"{100 * t[c] / n:>21.0f}%" for c in CAUSES)
              + f" {100 * t['listed_ran'] / max(t['listed_n'], 1):>11.0f}%")
    print("  (listed, ran: of the runners we listed, the share who finished this race)")


if __name__ == "__main__":
    main()
