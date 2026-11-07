#!/usr/bin/env python3
"""
gender_split_people.py -- one page holding a boy's races and a girl's.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/gender_split_people.py                 # DRY RUN: every page it would split
    $P scripts/gender_split_people.py --person 26140046
    $P scripts/gender_split_people.py --write         # split them (result_detach)

★ WHY (owner, 2026-11-06, /athlete/26140046). Katrina Coogan, Shrewsbury
  and Georgetown: four of the top nine all-time high school girls' ratings
  were hers -- 15:05 at the Brown Invitational 2010, 2nd behind Cheserek in
  the BOYS' varsity race. A male runner's races were on her page, rated as a
  girl's; in 2013 the page ran a men's 10K and a women's 6K on one day.

★ THE RULE (cross country). Every division's field gender comes from its
  runners (person_gender, FIELD_SHARE of FIELD_MIN+ known). A person with
  MIN_SIDE+ rows in men's fields AND MIN_SIDE+ in women's fields is two
  people when either:
    - the two sides rate GAP or more apart (median rating, log) -- one
      runner in the other gender's race runs their own level, not 5% off it;
    - the two sides share a day (nobody runs the men's and the women's race).
  The side that is not the person's gender (person_gender; without one, the
  smaller side, and a tie is left alone) is moved onto ONE fresh person (person_pins.detach, which 04a3 re-applies
  every night). A girl who races in boys' fields at her own level is left.
! DRY RUN by default; --write moves the rows. Undo one with
  scripts/person_pins.py --undo-detach RESULT_ID (or 'all').
"""
import argparse
import math
import os
import statistics
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FIELD_MIN = 5
FIELD_SHARE = 0.8
MIN_SIDE = 3
GAP = 0.05
# ★ ONE DAY IN BOTH IS TWO PEOPLE, HOWEVER FEW ROWS (owner, 2026-11-07:
#   Coogan's page -- a boy's 15:05 at Brown 2010, 3% from her own level, and
#   a men's 10K on the day of her women's 6K in 2013). Nobody runs the men's
#   and the women's race of one day, so a shared day needs no MIN_SIDE.
#   ! NOT PLACING: a woman near the front of a men's race was the first
#   idea, and the owner's answer was "a strong girl lowk could even with
#   more, it happens" -- placing proves nothing.


def verdict(rows, gender=None):
    """rows: [(result_id, side 'M'/'F', day, rating[, place, field size])]
    of one person. -> (move side, [result ids], why) or None. Pure."""
    by = defaultdict(list)
    for row in rows:
        rid, side, day, rating = row[:4]
        if side in ("M", "F"):
            by[side].append((rid, day, rating))
    shared = {d for _i, d, _r in by["M"]} & {d for _i, d, _r in by["F"]}
    if not shared and (len(by["M"]) < MIN_SIDE or len(by["F"]) < MIN_SIDE):
        return None
    if not by["M"] or not by["F"]:
        return None
    med = {}
    for g in ("M", "F"):
        rs = [float(r) for _i, _d, r in by[g] if r]
        med[g] = statistics.median(rs) if rs else None
    days = {d for _i, d, _r in by["M"]} & {d for _i, d, _r in by["F"]}
    gap = (abs(math.log(med["M"] / med["F"])) if med["M"] and med["F"] else 0.0)
    if gap < GAP and not days:
        return None
    g = (gender or "").upper()
    if g in ("M", "F"):
        move = "F" if g == "M" else "M"       # the person's own gender stays
    elif len(by["M"]) != len(by["F"]):
        move = "M" if len(by["M"]) < len(by["F"]) else "F"
    else:
        return None                           # nothing says which side is theirs
    why = (f"{len(by['M'])} rows in men's fields, {len(by['F'])} in women's; medians "
           f"{med['M'] or 0:.1f} / {med['F'] or 0:.1f} ({100 * gap:.0f}% apart)"
           + (f"; both on {len(days)} day(s)" if days else ""))
    return move, sorted(i for i, _d, _r in by[move]), why


def explainOne(pid, rows, gender):
    """--person: why the page is or is not two people."""
    if not rows:
        print(f"  /athlete/{pid}: no rows in both men's and women's fields (a field "
              f"counts with {FIELD_MIN}+ rated runners, {FIELD_SHARE:.0%} one way)")
        return
    side = defaultdict(list)
    for row in rows:
        rid, g, d, r = row[:4]
        side[g].append((d, r, rid))
    print(f"  /athlete/{pid}: the person's gender {gender or 'unknown'}")
    for g in ("M", "F"):
        rs = sorted(side[g])
        med = statistics.median([float(r) for _d, r, _i in rs if r]) if any(r for _d, r, _i in rs) else None
        print(f"    {len(rs)} rows in {'men' if g == 'M' else 'women'}'s fields, median "
              f"rating {med and round(med, 1)}: "
              + ", ".join(f"{d} {r and round(float(r), 1)}" for d, r, _i in rs[:12]))
    v = verdict(rows, gender)
    print(f"    verdict: {v[2] if v else 'one person (no gap of ' + format(GAP, '.0%') + ', no shared day)'}")


_SQL = """
    -- ! A FIELD'S GENDER FROM ITS ROWS' POOLS, not person_gender: most
    --   runners of 2010 have no person_gender row, so Coogan's boys' races
    --   were in no field at all (owner's --person run, 2026-11-07)
    WITH fld AS (
        SELECT r.source, r.meet_id, r.div_id,
               count(*) FILTER (WHERE right(split_part(r.rating_pool, '|', 1), 2) = '_m') AS m,
               count(*) FILTER (WHERE right(split_part(r.rating_pool, '|', 1), 2) = '_f') AS f
        FROM   results r
        WHERE  r.meet_id IS NOT NULL AND r.div_id IS NOT NULL AND r.rating_pool IS NOT NULL
        GROUP  BY 1, 2, 3
        HAVING count(*) >= %(fmin)s),
    side AS (
        SELECT r.result_id, r.person_id, left(r.date, 10) AS d, r.speed_rating AS s,
               r.place, fld.m + fld.f AS n,
               CASE WHEN fld.m >= %(share)s * (fld.m + fld.f) THEN 'M'
                    WHEN fld.f >= %(share)s * (fld.m + fld.f) THEN 'F' END AS g
        FROM   results r
        JOIN   fld ON fld.source = r.source AND fld.meet_id = r.meet_id AND fld.div_id = r.div_id
        WHERE  r.person_id IS NOT NULL {one}),
    both_sides AS (
        SELECT person_id FROM side GROUP BY 1
        HAVING count(*) FILTER (WHERE g = 'M') >= 1
           AND count(*) FILTER (WHERE g = 'F') >= 1)
    SELECT s.person_id, s.result_id, s.g, s.d, s.s, pg.gender, s.place, s.n
    FROM   side s JOIN both_sides b USING (person_id)
    LEFT   JOIN person_gender pg ON pg.person_id = s.person_id
    WHERE  s.g IS NOT NULL
    ORDER  BY s.person_id, s.d
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", type=int, help="one person only")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=60)
    a = ap.parse_args(argv)
    from database import getConn
    import person_pins as PP
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = 0")
            cur.execute(_SQL.format(one="AND r.person_id = %(pid)s" if a.person else ""),
                        {"fmin": FIELD_MIN, "share": FIELD_SHARE, "side": MIN_SIDE,
                         "pid": a.person})
            raw = cur.fetchall()
            by = defaultdict(list)
            gender = {}
            for pid, rid, g, d, s, pg, place, n in raw:
                by[pid].append((rid, g, d, s, place, n))
                gender[pid] = pg
            found = []
            for pid, rows in by.items():
                v = verdict(rows, gender.get(pid))
                if v:
                    found.append((pid,) + v)
            print(f"[gender split] {len(by):,} pages with races in both men's and women's "
                  f"fields; {len(found):,} are two people")
            if a.person:
                explainOne(a.person, by.get(a.person, []), gender.get(a.person))
            for pid, move, rids, why in found[:a.show]:
                print(f"  /athlete/{pid}  move the {len(rids)} {'men' if move == 'M' else 'women'}'s-"
                      f"field rows to a new page -- {why}")
            if not a.write:
                print("DRY RUN -- --write moves them")
                conn.rollback()
                return 0
            moved, cleared = 0, []
            for pid, move, rids, why in found:
                to = None
                for rid in rids:
                    got = PP.detach(cur, "XC", rid, pid, actor="gender_split", to_person=to)
                    to = to or got
                    if got:
                        moved += 1
                        cleared.append(rid)
            # ! PRICED AS THE OTHER GENDER: the moved rows were normalised and
            #   rated in this person's pool; cleared, the nightly's 05 (new
            #   rows: no normalized_time) and 09b price them on the new page
            if cleared:
                cur.execute("UPDATE results SET normalized_time = NULL, speed_rating = NULL "
                            "WHERE result_id = ANY(%s)", (cleared,))
            conn.commit()
            print(f"[gender split] written: {moved:,} rows moved off {len(found):,} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
