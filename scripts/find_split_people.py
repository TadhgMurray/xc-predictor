#!/usr/bin/env python3
"""
find_split_people.py -- one runner on two athlete pages, found in bulk.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/find_split_people.py                      # DRY RUN: the pairs, with links
    $P scripts/find_split_people.py --since 2024 --show 200
    $P scripts/find_split_people.py --write              # merge them (merge_pages' mechanism)
    $P scripts/link_profile_school.py --undo OLD         # undo one

★ WHY (owner, 2026-11-04: "linker needs mucho work ... There are too many of
  these issues"). Mya Curtis, Iowa Western sophomore: /athlete/16152289 holds
  155 races, /athlete/29638265 the one race the ICCAC championship gave a
  person of its own. Every linker joins a NEW row to a person by its feed's
  id or by name + school at the moment it lands; once a second person
  exists, nothing looks back across persons and asks "are these one runner?"

★ THE RULE: SAME NAME, SAME SCHOOL, SAME SEASON, NO CLASH. Two persons are
  one runner when
    - their names are one key (person_name.personKey, a Jr. kept apart),
    - they raced for one school (search_index.schoolFold: "Trinity (Conn.)"
      is "Trinity"), at one level (high school, college) and one gender,
    - in at least one SAME season,
    - with no grade that disagrees in a season they share,
    - and never in the same cross country race on the same day at different
      times (two runners of one name on one team -- siblings, namesakes --
      race together; one runner's two copies of a race share its time).
  The page with more races keeps them; a page minted from a tfrrs id
  (1,000,000,000 + id) is a placeholder and never the keeper over a real one.

! NOTHING THE OWNER RULED ON IS TOUCHED: a page with a "not mine" detach
  (person_pins.result_detach) or a vetoed merge (profile_school_veto) is
  left out, and a merge already on file is not proposed again.
! THE MOVE IS link_profile_school.write: the same decision table, redirect,
  log and re-application every run as an approved "same person" fix, so no
  relink undoes it and --undo puts it back.
"""
import argparse
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MINT_FLOOR = 1_000_000_000      # link_tfrrs_rows.MINT_BASE: a minted person


def gradeKey(grade, level):
    """A grade as one comparable token: 'SO-2', 'So', '14' -> 'so' in college,
    '11', 'Jr', '11th' -> '11' in high school; None when unknown. Pure."""
    import re
    g = str(grade or "").strip().lower()
    if not g:
        return None
    words = {"fr": 0, "so": 1, "jr": 2, "sr": 3}
    m = re.match(r"(fr|so|jr|sr)", g)
    if level == "college":
        if m:
            return m.group(1)
        n = re.match(r"(\d+)", g)
        if n and int(n.group(1)) in (13, 14, 15, 16):
            return ("fr", "so", "jr", "sr")[int(n.group(1)) - 13]
        return None
    if m:
        return str(9 + words[m.group(1)])
    n = re.match(r"(\d+)", g)
    return n.group(1) if n else None


def findPairs(seasons):
    """seasons: dicts (person_id, name, school, level, gender, year, n_races,
    grade) -> [(old, keep, name, school)] by the rule above, before the
    same-day check (clashes() is the database half). Pure."""
    from person_name import personKey
    from search_index import schoolFold
    by = defaultdict(lambda: defaultdict(lambda: {"years": {}, "n": 0}))
    for s in seasons:
        k = personKey(s.get("name"), keep_suffix=True)
        sch = schoolFold(s.get("school"))
        if not k or not sch or not s.get("level") or not s.get("gender"):
            continue
        p = by[(k, sch, s["level"], s["gender"])][s["person_id"]]
        p["years"][s["year"]] = gradeKey(s.get("grade"), s["level"]) or p["years"].get(s["year"])
        p["n"] += int(s.get("n_races") or 0)
        p["name"], p["school"] = s.get("name"), s.get("school")
    out = []
    for (_k, _sch, _lvl, _g), people in by.items():
        if len(people) < 2:
            continue
        # the keeper: a real page over a minted placeholder, then the most races
        order = sorted(people, key=lambda pid: (pid >= MINT_FLOOR, -people[pid]["n"], pid))
        keep = order[0]
        for old in order[1:]:
            a, b = people[keep]["years"], people[old]["years"]
            shared = set(a) & set(b)
            if not shared:
                continue
            if any(a[y] and b[y] and a[y] != b[y] for y in shared):
                continue
            out.append((old, keep, people[old]["name"], people[keep]["school"]))
    return out


_SEASONS = """
    SELECT s.person_id, s.school, split_part(s.pool, '_', 1) AS level,
           right(split_part(s.pool, '|', 1), 1) AS gender, s.year, s.n_races, s.grade
    FROM   athlete_season s
    WHERE  s.year >= %(since)s AND NULLIF(btrim(s.school), '') IS NOT NULL
      AND  split_part(s.pool, '_', 1) IN ('hs', 'college')
"""


def _names(cur, pids):
    """{person_id: name}: the athletes registry, else the person's own rows."""
    out = {}
    pids = sorted(pids)
    for i in range(0, len(pids), 50000):
        chunk = pids[i:i + 50000]
        cur.execute("""SELECT COALESCE(a.person_id, a.athlete_id),
                              NULLIF(TRIM(concat_ws(' ', a.first_name, a.last_name)), '')
                       FROM   athletes a
                       WHERE  a.person_id = ANY(%(p)s) OR a.athlete_id = ANY(%(p)s)""", {"p": chunk})
        for pid, nm in cur.fetchall():
            if nm and nm.lower() != "unknown" and pid not in out:
                out[pid] = nm
    missing = [p for p in pids if p not in out]
    for i in range(0, len(missing), 20000):
        chunk = missing[i:i + 20000]
        cur.execute("""SELECT DISTINCT ON (person_id) person_id, TRIM(athlete_name)
                       FROM   results
                       WHERE  person_id = ANY(%s) AND NULLIF(TRIM(athlete_name), '') IS NOT NULL
                       ORDER  BY person_id""", (chunk,))
        out.update({pid: nm for pid, nm in cur.fetchall()})
    return out


def clashes(cur, pairs):
    """The pairs that raced one cross country race on one day at different
    times (more than a second apart): two runners, not one."""
    pids = sorted({p for a, b, *_x in pairs for p in (a, b)})
    if not pids:
        return set()
    cur.execute("""SELECT person_id, left(date, 10), meet_id, time_seconds FROM results
                   WHERE person_id = ANY(%s) AND time_seconds > 0""", (pids,))
    races = defaultdict(list)
    for pid, day, meet, t in cur.fetchall():
        races[pid].append((day, meet, float(t)))
    bad = set()
    for old, keep, *_x in pairs:
        mine = defaultdict(list)
        for day, _m, t in races.get(keep, ()):
            mine[day].append(t)
        for day, _m, t in races.get(old, ()):
            if mine.get(day) and all(abs(t - u) > 1.0 for u in mine[day]):
                bad.add((old, keep))
                break
    return bad


def _ruled(cur):
    """Person ids the owner already ruled on: detached rows, vetoed or filed merges."""
    out = set()
    for sql in ("SELECT from_person FROM result_detach UNION SELECT to_person FROM result_detach",
                "SELECT old_id FROM profile_school_veto",
                "SELECT old_id FROM profile_school_merge"):
        try:
            cur.execute("SAVEPOINT r")
            cur.execute(sql)
            out.update(r[0] for r in cur.fetchall() if r[0] is not None)
            cur.execute("RELEASE SAVEPOINT r")
        except Exception:                                 # noqa: BLE001 -- absent table
            cur.execute("ROLLBACK TO SAVEPOINT r")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=2025, help="first season read (default 2025)")
    ap.add_argument("--show", type=int, default=60)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SEASONS, {"since": a.since})
            cols = ("person_id", "school", "level", "gender", "year", "n_races", "grade")
            seasons = [dict(zip(cols, r)) for r in cur.fetchall()]
            print(f"[split] {len(seasons):,} athlete-seasons since {a.since}", flush=True)
            names = _names(cur, {s["person_id"] for s in seasons})
            for s in seasons:
                s["name"] = names.get(s["person_id"])
            pairs = findPairs(seasons)
            ruled = _ruled(cur)
            pairs = [p for p in pairs if p[0] not in ruled and p[1] not in ruled]
            bad = clashes(cur, pairs)
            conn.rollback()
        keep = [p for p in pairs if (p[0], p[1]) not in bad]
        print(f"[split] {len(pairs) + len(bad):,} same name + school + season pairs; "
              f"{len(bad):,} raced one race at different times (two runners, left alone); "
              f"{len(keep):,} to merge")
        for old, new, nm, school in keep[:a.show]:
            print(f"  {nm} ({school}): /athlete/{old} -> /athlete/{new}")
        if not a.write:
            print("DRY RUN -- --write merges them (undo one: scripts/link_profile_school.py --undo OLD)")
            return 0
        import link_profile_school as LPS
        out = LPS.write(conn, [(o, k, nm, sch) for o, k, nm, sch in keep])
        print(f"[split] merged {len(keep):,}: {out}")
        print("restart the site; the next pipeline run rebuilds the boards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
