#!/usr/bin/env python3
"""
link_swapped.py -- one runner we hold as two people, with first and last
name the other way round ("Ethan Avila" and "Avila Ethan" at one school),
joined.

    /srv/venv/bin/python scripts/link_swapped.py                # DRY RUN: verdicts + examples
    /srv/venv/bin/python scripts/link_swapped.py --show 80
    /srv/venv/bin/python scripts/link_swapped.py --apply        # merge
    /srv/venv/bin/python scripts/link_swapped.py --undo 27242759   # one back, and never again
    /srv/venv/bin/python scripts/link_swapped.py --undo all

★ WHY (owner, 2026-10-26: "agree" to building it, after
  scripts/diag_swapped_names.py found 20,618 such pairs, 18,332 of them
  with nothing against them). A form filled in last-name-first makes a
  second profile and no name key joins them.

★ WHO IS JOINED (decide(), pure, tests/test_link_swapped.py). A pair
  diag_swapped_names calls safe -- the only two people at the school
  answering to either order, never in one race (XC or track), never two
  grades in one season -- that also passes:
    - a REAL SCHOOL: "44-unattached", Unknown, Independent, a hometown are
      pools of strangers, not evidence (link_teamless.isTeamlessRow);
    - NO DIGITS in either name ("Artsiuk1 Ryhor" is a typing slip, and its
      partner may be anybody);
    - NOT TWO FIRST NAMES: "Thomas Jackson" and "Jackson Thomas" can be two
      kids. A pair is refused when BOTH of its names are mostly first names
      in our own athletes (FIRST_SHARE of their uses, MIN_USES uses at
      least) -- the owner's rule, measured on our data;
    - ONE PAIR PER PERSON: a person in two pairs (two schools) is a chain,
      and a chain is for a human.
  The person with more results keeps the id (the lower id on a tie); the
  other's rows, athletes rows and page move to it.

★ WHAT --apply WRITES, in one transaction, the way link_teamless does:
    swapped_merge (old_id, new_id, names, school): the decision, kept, so a
    re-scrape that seeds a new row under the old id is moved next run;
    person_link_log rule 'swapped': every moved result; results /
    results_tf: person_id old -> new, every source; athletes: person_id old
    -> new; person_redirect old -> new. A row the target already holds stays
    behind (person_move.dropCollisions). --undo puts it all back; --undo of
    one id also vetoes it (swapped_veto), so it is never decided again.
"""
import argparse
import os
import re
import sys
import time
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

RULE = "swapped"
TABLES = (("XC", "results"), ("TF", "results_tf"))
FIRST_SHARE = 0.5      # a name used as a first name at least this often ...
MIN_USES = 20          # ... over at least this many uses is "a first name"

MERGE_DDL = """
CREATE TABLE IF NOT EXISTS swapped_merge (
    old_id     bigint PRIMARY KEY,
    new_id     bigint NOT NULL,
    old_name   text,
    new_name   text,
    school     text,
    merged_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS swapped_veto (
    old_id     bigint PRIMARY KEY,
    vetoed_at  timestamptz NOT NULL DEFAULT now()
);
-- the athletes rows a merge repointed, so --undo puts back exactly those
-- (a person can own several athletic.net ids)
CREATE TABLE IF NOT EXISTS swapped_athlete_log (
    athlete_id   bigint NOT NULL,
    school       text,
    from_person  bigint NOT NULL,
    to_person    bigint NOT NULL,
    PRIMARY KEY (athlete_id, school, from_person)
)"""


def _norm(part):
    return " ".join(str(part or "").lower().split())


def firstShare(part, stats):
    """How often a name part is used as a first name in our athletes, or
    None with fewer than MIN_USES uses."""
    f, l = stats.get(_norm(part), (0, 0))
    return f / (f + l) if f + l >= MIN_USES else None


def orientation(first, last, stats):
    """+1 when first-then-last reads the right way round (the first name is
    mostly a first name, or the last mostly a last name), -1 the wrong way,
    0 when our athletes cannot say."""
    a, b = firstShare(first, stats), firstShare(last, stats)
    score = (0 if a is None else a - 0.5) + (0 if b is None else 0.5 - b)
    return (score > 0.1) - (score < -0.1)


def isFirstName(part, stats):
    """stats: {name part: (uses as a first name, uses as a last name)}."""
    f, l = stats.get(_norm(part), (0, 0))
    return f + l >= MIN_USES and f / (f + l) >= FIRST_SHARE


def decide(pair, kind, stats, rows_of, pairs_of, real_school):
    """One pair -> (verdict, keep_id, move_id). Pure.
    pair: (p1, p2, school, gender, first1, last1, first2, last2)."""
    p1, p2, sch, _g, f1, l1, f2, l2 = pair
    if kind != "safe":
        return kind, None, None
    if not real_school(sch):
        return "no real school", None, None
    if any(re.search(r"\d", str(x or "")) for x in (f1, l1, f2, l2)):
        return "digits in a name", None, None
    if isFirstName(f1, stats) and isFirstName(l1, stats):
        return "both names are first names", None, None
    if pairs_of.get(p1, 1) > 1 or pairs_of.get(p2, 1) > 1:
        return "in two pairs", None, None
    # the side whose FIRST name reads as a first name keeps the id (its page
    # shows the name the right way round); unclear, the bigger career; then
    # the lower id (owner's dry run, 2026-10-27: 'Smith Jeff' 2 rows kept over
    # 'Jeff Smith' 1, 'Thomas Jessica' over 'Jessica Thomas' on a 7-7 tie)
    o1, o2 = orientation(f1, l1, stats), orientation(f2, l2, stats)
    n1, n2 = rows_of.get(p1, 0), rows_of.get(p2, 0)
    keep, move = (p1, p2) if (o1, n1, -p1) >= (o2, n2, -p2) else (p2, p1)
    return "link", keep, move


_PAIR_NAMES = """
SELECT DISTINCT ON (p.p1, p.p2) p.p1, p.p2, p.sch, p.g,
       a1.first_name, a1.last_name, a2.first_name, a2.last_name
FROM   nk_pair p
JOIN   athletes a1 ON a1.person_id = p.p1 AND lower(btrim(a1.school)) = p.sch
JOIN   athletes a2 ON a2.person_id = p.p2 AND lower(btrim(a2.school)) = p.sch
ORDER  BY p.p1, p.p2
"""

# one pass over athletes per side, not one per name
_STATS = """
SELECT x, count(*) FILTER (WHERE side = 'f'), count(*) FILTER (WHERE side = 'l')
FROM (SELECT lower(btrim(first_name)) AS x, 'f' AS side FROM athletes
      WHERE  lower(btrim(first_name)) = ANY(%(parts)s)
      UNION ALL
      SELECT lower(btrim(last_name)), 'l' FROM athletes
      WHERE  lower(btrim(last_name)) = ANY(%(parts)s)) t
GROUP  BY x
"""

_ROWS = """
SELECT person_id, sum(n) FROM (
    SELECT person_id, count(*) AS n FROM results
    WHERE  person_id = ANY(%(ids)s) GROUP BY 1
    UNION ALL
    SELECT person_id, count(*) FROM results_tf
    WHERE  person_id = ANY(%(ids)s) GROUP BY 1) t
GROUP  BY 1
"""


def candidates(cur):
    """-> (pairs with names, {pair: kind}, name stats, rows per person)."""
    import diag_swapped_names as D
    from person_name import personKeySql
    cur.execute("SET LOCAL work_mem = '1GB'")
    cur.execute(D._KEYS.format(fwd=personKeySql("a.first_name", "a.last_name", keep_suffix=True),
                               rev=personKeySql("a.last_name", "a.first_name", keep_suffix=True)))
    cur.execute("CREATE INDEX ON sw (k, sch, g)")
    cur.execute("CREATE INDEX ON sw (kr, sch, g)")
    cur.execute("ANALYZE sw")
    cur.execute(D._PAIRS)
    cur.execute("ANALYZE nk_pair")
    cur.execute(D._CROWD)
    crowd = {(a, b): n for a, b, n in cur.fetchall()}
    cur.execute(D._SAME_RACE)
    same = set(cur.fetchall())
    cur.execute(D._OVERLAP)
    overlap = set(cur.fetchall())
    cur.execute(_PAIR_NAMES)
    pairs = cur.fetchall()
    kinds = {}
    for kind, rows in D.classify([(p[0], p[1]) + tuple(p[2:]) for p in pairs],
                                 crowd, same, overlap).items():
        for r in rows:
            kinds[(r[0], r[1])] = kind
    parts = sorted({_norm(x) for p in pairs for x in p[4:8] if x})
    cur.execute(_STATS, {"parts": parts})
    stats = {x: (f, l) for x, f, l in cur.fetchall()}
    ids = sorted({p[0] for p in pairs} | {p[1] for p in pairs})
    cur.execute(_ROWS, {"ids": ids})
    rows_of = {pid: n for pid, n in cur.fetchall()}
    return pairs, kinds, stats, rows_of


def judge(pairs, kinds, stats, rows_of):
    from link_teamless import isTeamlessRow
    safe = [p for p in pairs if kinds.get((p[0], p[1])) == "safe"]
    pairs_of = Counter()
    for p in safe:
        pairs_of[p[0]] += 1
        pairs_of[p[1]] += 1
    out = {}
    for p in pairs:
        out[(p[0], p[1])] = decide(p, kinds.get((p[0], p[1]), "safe"), stats, rows_of,
                                   pairs_of, lambda s: not isTeamlessRow(s, team_id=1, source="x"))
    return out


def write(conn, decisions):
    """Record new decisions, then (re)apply every decision on file."""
    from psycopg2.extras import execute_values
    from person_redirects import DDL as REDIRECT_DDL
    from person_move import MOVE_DDL, dropCollisions
    from link_freshmen import LOG_DDL
    out = {}
    with conn.cursor() as cur:
        cur.execute(LOG_DDL)
        cur.execute(MERGE_DDL)
        cur.execute(REDIRECT_DDL)
        if decisions:
            execute_values(cur, """
                INSERT INTO swapped_merge (old_id, new_id, old_name, new_name, school)
                VALUES %s ON CONFLICT (old_id) DO NOTHING""", decisions)
            out["decisions new"] = cur.rowcount
        cur.execute("DROP TABLE IF EXISTS sm_map")
        cur.execute("""CREATE TEMP TABLE sm_map AS
                       SELECT old_id, new_id FROM swapped_merge
                       WHERE  old_id NOT IN (SELECT old_id FROM swapped_veto)""")
        for sport, table in TABLES:
            cur.execute(MOVE_DDL)
            cur.execute(f"""
                INSERT INTO pm_move (result_id, new_id)
                SELECT r.result_id, m.new_id
                FROM   {table} r JOIN sm_map m ON r.person_id = m.old_id
                ON CONFLICT DO NOTHING""")
            kept = dropCollisions(cur, table)
            if kept:
                out[f"{sport} rows kept back (target holds a copy)"] = sum(kept.values())
            cur.execute(f"""
                INSERT INTO person_link_log (sport, result_id, from_person, to_person, rule)
                SELECT %s, r.result_id, r.person_id, mv.new_id, %s
                FROM   {table} r JOIN pm_move mv ON mv.result_id = r.result_id
                ON CONFLICT DO NOTHING""", (sport, RULE))
            cur.execute(f"""UPDATE {table} r SET person_id = mv.new_id
                            FROM pm_move mv WHERE r.result_id = mv.result_id""")
            out[f"{sport} rows moved"] = cur.rowcount
        cur.execute("""INSERT INTO swapped_athlete_log (athlete_id, school, from_person, to_person)
                       SELECT a.athlete_id, COALESCE(a.school, ''), m.old_id, m.new_id
                       FROM   athletes a JOIN sm_map m ON a.person_id = m.old_id
                       ON CONFLICT DO NOTHING""")
        cur.execute("""UPDATE athletes a SET person_id = m.new_id
                       FROM sm_map m WHERE a.person_id = m.old_id""")
        out["athletes rows repointed"] = cur.rowcount
        cur.execute("""
            INSERT INTO person_redirect (old_id, new_id, n_probes)
            SELECT old_id, new_id, 0 FROM sm_map
            ON CONFLICT (old_id) DO UPDATE SET new_id = EXCLUDED.new_id
            WHERE person_redirect.new_id <> EXCLUDED.new_id""")
        out["redirects written"] = cur.rowcount
    conn.commit()
    return out


def undo(conn, which):
    """which: 'all' or one old id (that one is also vetoed)."""
    with conn.cursor() as cur:
        cur.execute(MERGE_DDL)
        one = None if which == "all" else int(which)
        cond = "" if one is None else "AND l.from_person = %(one)s"
        back = {}
        for sport, table in TABLES:
            cur.execute(f"""
                UPDATE {table} r SET person_id = l.from_person
                FROM   person_link_log l
                WHERE  l.sport = %(sport)s AND l.rule = %(rule)s {cond}
                  AND  r.result_id = l.result_id AND r.person_id = l.to_person""",
                        {"sport": sport, "rule": RULE, "one": one})
            back[sport] = cur.rowcount
        mcond = "" if one is None else "AND m.old_id = %(one)s"
        acond = "" if one is None else "AND l.from_person = %(one)s"
        cur.execute(f"""
            UPDATE athletes a SET person_id = l.from_person
            FROM   swapped_athlete_log l
            WHERE  a.athlete_id = l.athlete_id AND COALESCE(a.school, '') = l.school
              AND  a.person_id = l.to_person {acond}""", {"one": one})
        back["athletes"] = cur.rowcount
        cur.execute(f"DELETE FROM swapped_athlete_log l WHERE true {acond}", {"one": one})
        cur.execute(f"""DELETE FROM person_redirect p USING swapped_merge m
                        WHERE p.old_id = m.old_id AND p.new_id = m.new_id {mcond}""",
                    {"one": one})
        cur.execute(f"DELETE FROM person_link_log l WHERE l.rule = %(rule)s {cond}",
                    {"rule": RULE, "one": one})
        cur.execute(f"DELETE FROM swapped_merge m WHERE true {mcond}", {"one": one})
        if one is not None:
            cur.execute("INSERT INTO swapped_veto (old_id) VALUES (%s) ON CONFLICT DO NOTHING",
                        (one,))
    conn.commit()
    print(f"[swapped] undone {which}: rows put back {back}"
          + ("; vetoed, the next run leaves it alone" if which != "all" else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=30)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo")
    args = ap.parse_args()
    from database import getConn
    t0 = time.time()
    with getConn() as conn:
        if args.undo:
            undo(conn, args.undo)
            return
        with conn.cursor() as cur:
            pairs, kinds, stats, rows_of = candidates(cur)
        verdicts = judge(pairs, kinds, stats, rows_of)
        conn.rollback()                      # the temp tables go; nothing else was written
        names = {(p[0], p[1]): p for p in pairs}
        by = Counter(v[0] for v in verdicts.values())
        print(f"[swapped] {len(pairs):,} pairs ({time.time() - t0:.0f}s):")
        for why, n in by.most_common():
            print(f"    {why:<28} {n:>8,}")
        for why in ("link", "both names are first names", "no real school",
                    "digits in a name", "in two pairs"):
            rows = [k for k, v in verdicts.items() if v[0] == why][:args.show]
            if rows:
                print(f"\n  {why} (up to {args.show}):")
            for k in rows:
                p = names[k]
                keep = verdicts[k][1]
                print(f"    {str(p[4]) + ' ' + str(p[5]):<28} {str(p[6]) + ' ' + str(p[7]):<28} "
                      f"{p[3]} {p[2][:30]:<30} {p[0]}/{p[1]}"
                      + (f" keep {keep} ({rows_of.get(p[0], 0)}/{rows_of.get(p[1], 0)} rows)"
                         if keep else ""))
        if not args.apply:
            print("\n[swapped] dry run: nothing written (--apply merges the 'link' pairs)")
            return
        decisions = []
        for k, (why, keep, move) in verdicts.items():
            if why == "link":
                p = names[k]
                name_of = {p[0]: f"{p[4]} {p[5]}", p[1]: f"{p[6]} {p[7]}"}
                decisions.append((move, keep, name_of[move], name_of[keep], p[2]))
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('swapped_veto')")
        if cur.fetchone()[0]:
            cur.execute("SELECT old_id FROM swapped_veto")
            vetoed = {r[0] for r in cur.fetchall()}
            decisions = [d for d in decisions if d[0] not in vetoed and d[1] not in vetoed]
        out = write(conn, decisions)
        print(f"[swapped] {out}; logged in person_link_log rule '{RULE}' "
              f"(--undo <id>|all reverses)")


if __name__ == "__main__":
    main()
