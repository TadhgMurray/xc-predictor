#!/usr/bin/env python3
"""
person_collision.py -- a person id that a NEW anet athlete also owns.

    /srv/venv/bin/python scripts/person_collision.py              # DRY RUN
    /srv/venv/bin/python scripts/person_collision.py --show 80
    /srv/venv/bin/python scripts/person_collision.py --person 32773608
    /srv/venv/bin/python scripts/person_collision.py --write
    /srv/venv/bin/python scripts/person_collision.py --undo

★ THE CASE (owner, 2026-09-29, NCAA DI 2024 men's 10k): "April Gienger" and
  "Regan Holmes" in the men's race. Person 32773608 held Cole Sprout's whole
  Stanford career (anet athlete 12267305 and his tfrrs rows) AND the two
  2026 club rows of anet athlete 32773608, April Gienger. The page takes
  its name from the athlete whose id IS the person id, so Cole's career ran
  under her name; 32791413 was Ronan McMahon-Staggs's Washington career
  under a sixth grader's.

★ HOW. Before the tfrrs mint existed, unlink.py numbered a split-off person
  max(person_id) + 1 -- the top of the anet id range, about 32.7 million.
  anet kept counting and later issued those same numbers to new athletes,
  whose rows are seeded person_id = athlete_id at insert, so each landed
  on the stranger already living there. unlink now numbers from
  SPLIT_BASE (2e9); this repairs the ones already made.

★ THE TEST: the athlete whose anet id IS the person id shares NO name token
  with anyone else under that id (every other anet athlete's name, every
  tfrrs row's name) -- not even one within a typo (namesNear) -- and is the
  NEWCOMER, first racing after the rest of the id had begun. Cole Sprout / April Gienger, Ronan McMahon-Staggs /
  Regan Holmes. One shared token -- a nickname, a married surname, a
  hyphenation -- is no collision. And the native athlete must hold FEWER
  rows than the rest: the newcomer moves nothing, it keeps its own number
  (future scrapes of it seed that number) and everyone else leaves.

★ WHERE THEY GO: the smallest other anet athlete id in the group, when no
  row anywhere carries it as a person (Cole goes home to 12267305);
  otherwise a fresh id from unlink.SPLIT_BASE up.

! LOGGED AND REVERSIBLE: person_link_log, rule 'collision'; --undo puts
  every logged row back.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RULE = "collision"
ANET_TOP = 1_000_000_000          # below the tfrrs mint: an anet-seeded person
TABLES = (("XC", "results"), ("TF", "results_tf"))


def tokensSql(x):
    """A name's lower-case letter tokens of two letters or more."""
    return (f"ARRAY(SELECT t FROM unnest(regexp_split_to_array(btrim("
            f"regexp_replace(lower(COALESCE({x}, '')), '[^a-z]+', ' ', 'g')), ' ')) t "
            f"WHERE length(t) >= 2)")


def gatherSql(person=None):
    """Temp tables pc_other (p, ident, aid, name, n) and pc_suspect."""
    only = "AND r.person_id = %(person)s" if person is not None else ""
    parts = [f"""
        SELECT r.person_id AS p,
               CASE WHEN r.source = 'anet' THEN r.athlete_id END AS aid,
               CASE WHEN r.source = 'anet' THEN NULL ELSE r.athlete_name END AS name,
               count(*) AS n, min(r.date) AS first
        FROM   {t} r
        WHERE  r.person_id IS NOT NULL AND r.person_id < {ANET_TOP}
          AND  NOT (r.source = 'anet' AND r.athlete_id = r.person_id) {only}
        GROUP  BY 1, 2, 3""" for _s, t in TABLES]
    native = [f"""
        SELECT r.person_id AS p, count(*) AS n, min(r.date) AS first FROM {t} r
        WHERE  r.source = 'anet' AND r.athlete_id = r.person_id
          AND  r.person_id IN (SELECT p FROM pc_other)
        GROUP  BY 1""" for _s, t in TABLES]
    return f"""
        DROP TABLE IF EXISTS pc_other, pc_native, pc_suspect;
        CREATE TEMP TABLE pc_other AS
            SELECT p, aid, name, sum(n)::bigint AS n, min(first) AS first
            FROM ({' UNION ALL '.join(parts)}) x GROUP BY 1, 2, 3;
        CREATE INDEX ON pc_other (p);
        ANALYZE pc_other;
        CREATE TEMP TABLE pc_native AS
            SELECT p, sum(n)::bigint AS n, min(first) AS first
            FROM ({' UNION ALL '.join(native)}) x GROUP BY 1;
        CREATE TEMP TABLE pc_suspect AS
        WITH other_tok AS (
            SELECT o.p, sum(o.n) AS n_other, min(o.first) AS first_other,
                   array_agg(DISTINCT tk) FILTER (WHERE tk IS NOT NULL) AS toks,
                   min(o.aid) AS min_aid
            FROM   pc_other o
            LEFT   JOIN LATERAL (
                   SELECT a.first_name || ' ' || a.last_name AS nm
                   FROM   athletes a WHERE a.athlete_id = o.aid
                     AND  btrim(COALESCE(a.first_name, '') || COALESCE(a.last_name, '')) <> ''
                   LIMIT  1) an ON true
            LEFT   JOIN LATERAL unnest({tokensSql('COALESCE(o.name, an.nm)')}) tk ON true
            GROUP  BY o.p),
        native_tok AS (
            SELECT DISTINCT ON (a.athlete_id) a.athlete_id AS p,
                   a.first_name || ' ' || a.last_name AS nm,
                   {tokensSql("a.first_name || ' ' || a.last_name")} AS toks
            FROM   athletes a JOIN pc_native n ON n.p = a.athlete_id
            WHERE  btrim(COALESCE(a.first_name, '') || COALESCE(a.last_name, '')) <> ''
            ORDER  BY a.athlete_id)
        SELECT o.p, nt.nm AS native_name, n.n AS n_native, o.n_other,
               o.toks AS other_tokens, o.min_aid, nt.toks AS native_tokens
        FROM   other_tok o
        JOIN   native_tok nt ON nt.p = o.p
        JOIN   pc_native n ON n.p = o.p
        WHERE  cardinality(nt.toks) > 0 AND cardinality(o.toks) > 0
          AND  NOT (nt.toks && o.toks)
          AND  n.n < o.n_other
          -- the id's own athlete is the NEWCOMER: first raced after the
          -- rest of the id had begun (anet issued the number later)
          AND  n.first > o.first_other;
    """


def gather(cur, person=None):
    cur.execute("SET LOCAL work_mem = '512MB'")
    cur.execute(gatherSql(person), {"person": person})
    cur.execute("""SELECT p, native_name, n_native, n_other, other_tokens, min_aid,
                          native_tokens
                   FROM pc_suspect ORDER BY n_other DESC""")
    return [r[:6] for r in cur.fetchall() if not namesNear(r[6], r[4])]


def osa(a, b):
    """Edit distance counting an adjacent swap as one edit."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def namesNear(native, others):
    """★ ONE TYPO IS THE SAME NAME (owner's dry run, 2026-09-29: "Hanna
    Mosley" against her own college rows as "Hannah Mosely" -- no token in
    common, one person). Any native token within one edit (a swap counts
    as one) of any other token, both three letters or more, is a match."""
    for a in native or ():
        for b in others or ():
            if len(a) >= 3 and len(b) >= 3 and osa(a, b) <= 1:
                return True
    return False


def targets(cur, suspects):
    """{p: new person id}: the smallest other anet athlete id when no row
    carries it as a person, else the next id from unlink.SPLIT_BASE."""
    from unlink import SPLIT_BASE
    cur.execute("""
        SELECT GREATEST(
            COALESCE((SELECT max(person_id) FROM results WHERE person_id >= %(b)s), 0),
            COALESCE((SELECT max(person_id) FROM results_tf WHERE person_id >= %(b)s), 0))
    """, {"b": SPLIT_BASE})
    next_id = max(int(cur.fetchone()[0] or 0) + 1, SPLIT_BASE)
    out, taken = {}, set()
    for p, _nm, _nn, _no, _toks, min_aid in suspects:
        to = None
        if min_aid is not None and int(min_aid) not in taken:
            cur.execute("""SELECT EXISTS (SELECT 1 FROM results WHERE person_id = %(a)s)
                               OR EXISTS (SELECT 1 FROM results_tf WHERE person_id = %(a)s)""",
                        {"a": int(min_aid)})
            if not cur.fetchone()[0]:
                to = int(min_aid)
        if to is None:
            to, next_id = next_id, next_id + 1
        taken.add(to)
        out[int(p)] = to
    return out


LOG_DDL = """
CREATE TABLE IF NOT EXISTS person_link_log (
    sport        text        NOT NULL,
    result_id    bigint      NOT NULL,
    from_person  bigint      NOT NULL,
    to_person    bigint      NOT NULL,
    rule         text        NOT NULL,
    linked_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sport, result_id, rule)
)"""


def write(cur, moves):
    """Move every row under p that is NOT the native athlete's to moves[p]."""
    from psycopg2.extras import execute_values
    cur.execute(LOG_DDL)
    cur.execute("DROP TABLE IF EXISTS pc_map")
    cur.execute("CREATE TEMP TABLE pc_map (p bigint PRIMARY KEY, to_person bigint NOT NULL)")
    execute_values(cur, "INSERT INTO pc_map VALUES %s", list(moves.items()))
    moved = {}
    for sport, t in TABLES:
        cur.execute(f"""
            INSERT INTO person_link_log (sport, result_id, from_person, to_person, rule)
            SELECT %s, r.result_id, m.p, m.to_person, %s
            FROM   {t} r JOIN pc_map m ON r.person_id = m.p
            WHERE  NOT (r.source = 'anet' AND r.athlete_id = m.p)
            ON CONFLICT DO NOTHING""", (sport, RULE))
        cur.execute(f"""
            UPDATE {t} r SET person_id = m.to_person
            FROM   pc_map m
            WHERE  r.person_id = m.p AND NOT (r.source = 'anet' AND r.athlete_id = m.p)""")
        moved[sport] = cur.rowcount
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'athletes' AND column_name = 'person_id'""")
    if cur.fetchone():
        cur.execute("""UPDATE athletes a SET person_id = m.to_person
                       FROM pc_map m
                       WHERE a.person_id = m.p AND a.athlete_id <> m.p""")
        moved["athletes"] = cur.rowcount
    return moved


def undo(cur):
    cur.execute("SELECT to_regclass('person_link_log')")
    if cur.fetchone()[0] is None:
        return {}
    back = {}
    for sport, t in TABLES:
        cur.execute(f"""
            UPDATE {t} r SET person_id = l.from_person
            FROM   person_link_log l
            WHERE  l.sport = %s AND l.rule = %s
              AND  r.result_id = l.result_id AND r.person_id = l.to_person""",
                    (sport, RULE))
        back[sport] = cur.rowcount
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'athletes' AND column_name = 'person_id'""")
    if cur.fetchone():
        cur.execute("""UPDATE athletes a SET person_id = l.from_person
                       FROM (SELECT DISTINCT from_person, to_person FROM person_link_log
                             WHERE rule = %s) l
                       WHERE a.person_id = l.to_person AND a.athlete_id <> l.from_person""",
                    (RULE,))
    cur.execute("DELETE FROM person_link_log WHERE rule = %s", (RULE,))
    return back


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", type=int)
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", action="store_true")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        if a.undo:
            print(f"[collision] undone: {undo(cur)}")
            conn.commit()
            return
        sus = gather(cur, a.person)
        moves = targets(cur, sus)
        print(f"[collision] {len(sus):,} person ids hold a newer anet athlete who "
              f"shares no name with the rest")
        for p, nm, nn, no, toks, _aid in sus[:a.show]:
            print(f"    {p:>11}  native {nm!r} ({nn} rows)  vs  "
                  f"{' '.join(sorted(toks or []))[:50]!r} ({no} rows)  -> {moves[int(p)]}")
        if a.write and moves:
            print(f"[collision] moved: {write(cur, moves)}")
            conn.commit()
        elif not a.write:
            print("[collision] DRY RUN -- pass --write to move them")
            conn.rollback()


if __name__ == "__main__":
    main()
