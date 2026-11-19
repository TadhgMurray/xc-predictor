"""
person_gender.py -- a person's gender from the divisions they raced under,
by majority; a person who raced both ways enough is SPLIT, and every reader
pools their rows by the row's own label. Issue 164. Pipeline step 04d.

    python engine/person_gender.py            # report
    python engine/person_gender.py --write    # (re)build person_gender

★ THE EVIDENCE IS THE ROW, NOT THE PROFILE. `athletes.gender` is one
  letter per scraped profile, and a person with several profiles got the
  majority of PROFILES. The division a race was run under is the fact:
  "Boys Varsity", "Girls JV", "Men 8k" (tfrrs XC), "Women's 1500 Meters"
  (tfrrs track). athletic.net track rows carry no gendered label, so a
  track-only anet career falls back to the profile. Ties go to 'M', the
  same tie-break as before.

★ A SPLIT PERSON IS TWO ATHLETES TO THE ENGINE AND THE SITE, ONE ROW IN
  THE DATABASE. split = both genders have 5+ rows and the minority is at
  least a third. The pack keys an athlete by (person, pool) and the pool
  carries the gender, so pooling a split person's rows by the row's own
  label makes two athlete keys with no other change; the boards and the
  athlete page do the same, and the page labels the season blocks.

  person_gender (person_id, gender, n_m, n_f, split)

Readers: speed_ratings_db (the pack, both sports), build_ranking_results
(the boards' gender temp), backfill_normalize._loadGenders (person level
only: the normalisation pool), racecast/app.py (the season labels). A
database without the table behaves exactly as before.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

PROFILE_VOTES = 3            # a scraped profile's weight against labelled rows
# ★ A DECISIVE FIELD OUTVOTES A PROFILE (owner, 2026-10-25: Liam Whitling,
#   one race, a boys' "Varsity" at Rocky Grove whose field is all boys, his
#   athletic.net profile marked F: his single row voted M once, the profile
#   F three times, and a 16:55 boy sat #1 on the girls' board). A cross
#   country division is one gender; FIELD_MIN+ known runners saying one
#   gender FIELD_SHARE to 1 is the strongest single fact we have, stronger
#   than one letter on a profile somebody typed. Labelled rows still count
#   one each, so the Cam Kuss and stray-label cases are unchanged.
FIELD_VOTES = 4
FIELD_MIN = 3                # known-gender runners a division needs to vote
FIELD_SHARE = 9              # ...and one gender must outnumber the other 9 to 1
M_RX = r"\m(boys?|men|mens|male|males)\M"
F_RX = r"\m(girls?|women|womens|female|females)\M"


def labelExpr(text_sql):
    """SQL: 'M' / 'F' / NULL from one free-text label. Both words present
    (a mixed race) is NULL. Postgres word boundaries are \\m \\M, so "men"
    never matches inside "women"."""
    return (f"CASE WHEN ({text_sql}) ~* '{F_RX}' AND NOT (({text_sql}) ~* '{M_RX}') "
            f"THEN 'F' WHEN ({text_sql}) ~* '{M_RX}' AND NOT (({text_sql}) ~* '{F_RX}') "
            f"THEN 'M' END")


# the row's own label, per reader (what each query has joined)
ROW_LABEL_PACK = {
    "XC": "COALESCE(m.division, mt.division_distances -> r.div_id::text ->> 'div_name')",
    "TF": "r.event_short",
}
ROW_LABEL_BOARD = {"XC": "m.division", "TF": "r.event_short"}


def packGenderExpr(sport, available=True):
    """The pack's gender column: a split person's row by its own label,
    everyone else by the majority, the profile majority (`a.gender`, the
    lateral) when the table has nothing."""
    if not available:
        return "a.gender"
    lab = labelExpr(ROW_LABEL_PACK[sport])
    return (f"CASE WHEN pg.split THEN COALESCE({lab}, pg.gender, a.gender) "
            f"ELSE COALESCE(pg.gender, a.gender) END")


def boardGenderExpr(sport):
    """The boards' gender column over tmp_person_gender `a` (which already
    holds COALESCE(person_gender, profile majority) and `split`)."""
    lab = labelExpr(ROW_LABEL_BOARD[sport])
    return f"CASE WHEN a.split THEN COALESCE({lab}, a.gender) ELSE a.gender END"


# ★ THE BUILD IN PIECES (2026-11-19; see SIDE BY SIDE below). _BUILD is
#   these fragments joined, the one statement it always was.
_HEAD = """
DROP TABLE IF EXISTS person_gender_new;
CREATE TABLE person_gender_new AS
"""
_PA_FLD = f"""-- ★ THE FIELD, BY PERSON (2026-10-07): each runner's scraped profile gender,
--   one per person (a person whose profiles disagree has none here). By
--   person, not by the row's athlete_id: a tfrrs row carries no athletic.net
--   profile id, so the field of a tfrrs race counted almost nobody.
WITH pa AS (
    SELECT person_id, min(gender) AS g
    FROM   athletes
    WHERE  person_id IS NOT NULL AND gender IN ('M', 'F')
    GROUP  BY person_id HAVING count(DISTINCT gender) = 1
),
fld AS (
    SELECT fr.source, fr.meet_id, fr.div_id,
           count(DISTINCT fr.person_id) FILTER (WHERE pa.g = 'M') AS fm,
           count(DISTINCT fr.person_id) FILTER (WHERE pa.g = 'F') AS ff
    FROM   results fr JOIN pa ON pa.person_id = fr.person_id
    GROUP  BY fr.source, fr.meet_id, fr.div_id
),
"""
_EV_XC = f"""    -- ★ THE FIELD OUTVOTES THE LABEL WHEN IT IS DECISIVE (2026-10-07,
    --   owner: Fairborn Community Park 2026, a college MEN's 5k whose tfrrs
    --   division reads "Women"). The label decided, so every runner first
    --   seen there became a woman: rated on the women's scale (a 15:17 at
    --   206) and predicted into the women's NCAA championship. A cross
    --   country division is one gender, and when {FIELD_MIN}+ of its known
    --   runners say one gender {FIELD_SHARE} to 1 that is the division's,
    --   whatever its label says. A field that is not decisive leaves the
    --   label (and, with no label, no vote) exactly as before.
    -- ★ AND WHERE THE DIVISION SAYS NOTHING, THE FIELD (2026-09-29): a
    --   college division on athletic.net is often just "Collegiate", and
    --   45 first-years at the Bates Preview 2026 were normalised on the
    --   women's 6 km anchor for want of a word.
    -- (the field is the OTHER runners: a person's own profile never votes
    --  in their own field, or a lone wrong profile in a small field makes
    --  it look mixed)
    -- ! PLAIN HASH JOINS, NO LATERAL (2026-10-26: the LATERAL form ran 18
    --   hours on the server, one probe of fld per result row, and its lock
    --   on results queued the nightly and then the site behind it). The
    --   runner's own vote comes off in the expressions instead.
    SELECT r.person_id,
           COALESCE(CASE WHEN f.fm - sm >= {FIELD_MIN} AND f.fm - sm >= {FIELD_SHARE} * (f.ff - sf) THEN 'M'
                         WHEN f.ff - sf >= {FIELD_MIN} AND f.ff - sf >= {FIELD_SHARE} * (f.fm - sm) THEN 'F' END,
                    {labelExpr(ROW_LABEL_PACK['XC'])}) AS g,
           CASE WHEN (f.fm - sm >= {FIELD_MIN} AND f.fm - sm >= {FIELD_SHARE} * (f.ff - sf))
                  OR (f.ff - sf >= {FIELD_MIN} AND f.ff - sf >= {FIELD_SHARE} * (f.fm - sm))
                THEN {FIELD_VOTES} ELSE 1 END AS w
    FROM   results r
    LEFT   JOIN pa ps ON ps.person_id = r.person_id
    CROSS  JOIN LATERAL (SELECT (ps.g IS NOT DISTINCT FROM 'M')::int AS sm,
                                (ps.g IS NOT DISTINCT FROM 'F')::int AS sf) own
    LEFT   JOIN fld f ON f.source = r.source AND f.meet_id = r.meet_id
                     AND f.div_id IS NOT DISTINCT FROM r.div_id
    -- ★ AND AN UPLOADED RACE'S DIVISION (2026-10-10): racecast/uploads.py
    --   writes an anet-shaped meets row per race, source 'upload', the
    --   gender in its division ("Girls Varsity"); an uploaded runner has no
    --   athletes profile, so the label is the only vote they bring. A timer
    --   meet (sources/promote.py, source 'timer', 2026-10-25) is the same shape
    LEFT   JOIN meets m        ON m.div_id = r.div_id
                              AND (r.source = 'anet'
                                   OR (r.source IN ('upload', 'timer') AND m.source = r.source))
    LEFT   JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id AND r.source = 'tfrrs'
    WHERE  r.person_id IS NOT NULL
"""
_EV_TF = f"""    SELECT r.person_id, {labelExpr('r.event_short')}, 1
    FROM   results_tf r
    WHERE  r.person_id IS NOT NULL
      AND  r.event_short ~* '{M_RX}|{F_RX}'
"""
_PROF = f"""-- ★ THE PROFILES VOTE TOO, AT {PROFILE_VOTES} EACH (2026-09-06). A boy
--   whose every division read "Varsity" had ONE row labelled the other
--   way and was pooled as a girl (a 17:54 5k at 144.8). One profile
--   outvotes a stray label; a handful of labelled rows still outvote a
--   wrong profile (the Cam Kuss case this table exists for). One vote
--   per profile id, however many school rows it has.
prof AS (
    SELECT DISTINCT person_id, athlete_id, gender FROM (
        SELECT r.person_id, a.athlete_id, a.gender
        FROM   results r JOIN athletes a ON a.athlete_id = r.athlete_id
        WHERE  r.person_id IS NOT NULL AND a.gender IN ('M', 'F')
        UNION ALL
        SELECT r.person_id, a.athlete_id, a.gender
        FROM   results_tf r JOIN athletes a ON a.athlete_id = r.athlete_id
        WHERE  r.person_id IS NOT NULL AND a.gender IN ('M', 'F')
    ) p
)"""
_ROWS = """    SELECT person_id,
           count(*) FILTER (WHERE g = 'M') AS n_m,
           count(*) FILTER (WHERE g = 'F') AS n_f,
           coalesce(sum(w) FILTER (WHERE g = 'M'), 0) AS w_m,
           coalesce(sum(w) FILTER (WHERE g = 'F'), 0) AS w_f
    FROM   ev WHERE g IS NOT NULL GROUP BY person_id
"""
_PROFS = """    SELECT person_id,
           count(*) FILTER (WHERE gender = 'M') AS p_m,
           count(*) FILTER (WHERE gender = 'F') AS p_f
    FROM   prof GROUP BY person_id
"""
_FINAL = f"""SELECT person_id,
       CASE WHEN w_f + {PROFILE_VOTES} * p_f > w_m + {PROFILE_VOTES} * p_m
            THEN 'F' ELSE 'M' END                        AS gender,
       n_m, n_f,
       -- split is decided by the ROWS alone: a profile is one letter, not
       -- evidence of a second person
       (least(n_m, n_f) >= 5
        AND least(n_m, n_f) * 3 >= n_m + n_f)               AS split
FROM (
    SELECT COALESCE(r.person_id, p.person_id) AS person_id,
           COALESCE(r.n_m, 0) AS n_m, COALESCE(r.n_f, 0) AS n_f,
           COALESCE(r.w_m, 0) AS w_m, COALESCE(r.w_f, 0) AS w_f,
           COALESCE(p.p_m, 0) AS p_m, COALESCE(p.p_f, 0) AS p_f
    FROM   rows_ r FULL OUTER JOIN profs p USING (person_id)
) s
WHERE n_m + n_f + p_m + p_f > 0;
"""
_TAIL = """ALTER TABLE person_gender_new ADD PRIMARY KEY (person_id);
-- who moved: the people whose verdict (gender or split) differs from the
-- table this replaces, so a backfill can re-normalise just them
-- (2026-09-06); an absent previous table means everyone with a row
DROP TABLE IF EXISTS person_gender_changed;
CREATE TABLE person_gender_changed AS
SELECT n.person_id, o.gender AS was, n.gender AS now
FROM   person_gender_new n
LEFT   JOIN person_gender o USING (person_id)
WHERE  o.person_id IS NULL OR o.gender IS DISTINCT FROM n.gender
   OR  o.split IS DISTINCT FROM n.split;
ALTER TABLE person_gender_changed ADD PRIMARY KEY (person_id);
"""
_BUILD = (_HEAD + _PA_FLD + "ev AS (\n" + _EV_XC + "    UNION ALL\n" + _EV_TF
          + "),\n" + _PROF + ",\nrows_ AS (\n" + _ROWS + "),\nprofs AS (\n"
          + _PROFS + ")\n" + _FINAL + _TAIL)

# ★ SIDE BY SIDE (2026-11-19; run 20261116_204106: 04d_gender 542 s in
#   line, one statement, one log line at the end). _BUILD's three readers
#   of results / results_tf are independent until the final join: the
#   cross country votes (pa, fld and ev's results branch), the track votes
#   (ev's results_tf branch) and the profiles (prof). Each now runs on its
#   own connection into a plain table, per person, and the main connection
#   finishes from those three.
# ! THE SAME ROWS. rows_ groups ev by person; ev is the UNION ALL of the
#   two branches, so each person's counts and vote weights are the sums of
#   the two branches' own -- count and sum add, and a person absent from a
#   branch adds nothing. The sums are cast back to bigint, the type count
#   and sum(int) gave, and the stack is a UNION ALL of the two pieces, so
#   person_id resolves to the type ev's union gave it. profs is the profiles
#   piece as it was. From there on the statement is _BUILD's own text
#   (_FINAL, _TAIL). Each piece's plan is one branch of the old plan.
# ! MEMORY: three statements at once, each with the main connection's
#   session settings (work_mem 1GB, 4 workers per gather) -- the hash tables
#   the one plan held across its branches until it ended, now held by three
#   plans at once. Workers past max_parallel_workers are simply not started.
# ! Never under an exported snapshot (XCP_PG_SNAPSHOT_FILE: the main
#   transaction could not see the pieces' rows). A failed piece costs only
#   time: the pieces are dropped and _BUILD runs as before.
#   XCP_GENDER_PARALLEL=0 is the one statement.
_PIECE_XC, _PIECE_TF, _PIECE_PROF = ("person_gender_p_xc", "person_gender_p_tf",
                                     "person_gender_p_prof")
_PIECES = {
    _PIECE_XC: _PA_FLD + "ev AS (\n" + _EV_XC + ")\n" + _ROWS,
    _PIECE_TF: "WITH ev AS (\n" + _EV_TF + ")\n" + _ROWS,
    _PIECE_PROF: "WITH " + _PROF + "\n" + _PROFS,
}
_STACK = (_HEAD + f"""WITH rows_ AS (
    SELECT person_id,
           sum(n_m)::bigint AS n_m, sum(n_f)::bigint AS n_f,
           sum(w_m)::bigint AS w_m, sum(w_f)::bigint AS w_f
    FROM  (SELECT * FROM {_PIECE_XC} UNION ALL SELECT * FROM {_PIECE_TF}) u
    GROUP  BY person_id
),
profs AS (SELECT * FROM {_PIECE_PROF})
""" + _FINAL + _TAIL + f"DROP TABLE {', '.join(_PIECES)};\n")
_SIDE_SKIP = ("transaction_isolation", "transaction_read_only",
              "transaction_deferrable", "session_authorization", "role")


def sideOn():
    if os.environ.get("XCP_GENDER_PARALLEL", "1").strip() in ("0", "false"):
        return False
    return not os.environ.get("XCP_PG_SNAPSHOT_FILE", "").strip()


def _dropPieces(cur):
    cur.execute(f"DROP TABLE IF EXISTS {', '.join(_PIECES)}")


def buildPieces(cur, connect=None):
    """The three pieces at once, each on its own connection with `cur`'s
    session settings, committed. Raises on a failure."""
    from concurrent.futures import ThreadPoolExecutor
    connect = connect or getConn
    cur.execute("SELECT name, current_setting(name) FROM pg_settings "
                "WHERE source = 'session' ORDER BY name")
    settings = [(n, v) for n, v in cur.fetchall() if n not in _SIDE_SKIP]

    def work(name, sql):
        t0 = time.time()
        with connect() as conn:
            try:
                with conn.cursor() as wc:
                    for k, v in settings:
                        wc.execute("SAVEPOINT side_set")
                        try:
                            wc.execute("SELECT set_config(%s, %s, true)", (k, v))
                            wc.execute("RELEASE SAVEPOINT side_set")
                        except Exception:                   # noqa: BLE001
                            wc.execute("ROLLBACK TO SAVEPOINT side_set")
                    wc.execute(f"DROP TABLE IF EXISTS {name}")
                    wc.execute(f"CREATE TABLE {name} AS\n{sql}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        print(f"  {name}: {time.time() - t0:.0f}s on its own connection", flush=True)

    with ThreadPoolExecutor(max_workers=len(_PIECES)) as ex:
        for f in [ex.submit(work, n, q) for n, q in _PIECES.items()]:
            f.result()


def build(cur, connect=None):
    """person_gender_new and person_gender_changed, uncommitted on `cur`."""
    if sideOn():
        t0 = time.time()
        try:
            buildPieces(cur, connect)
        except Exception as exc:                             # noqa: BLE001
            print(f"  side by side failed ({type(exc).__name__}: {exc}); "
                  f"one statement instead", flush=True)
            _dropPieces(cur)
        else:
            cur.execute(_STACK)
            print(f"  person_gender_new: {time.time() - t0:.0f}s, three pieces side by "
                  f"side (XCP_GENDER_PARALLEL=0 for one statement)", flush=True)
            return
    cur.execute(_BUILD)


# the swap itself is database.swapTable (short lock, retries), run after
# _BUILD commits -- a bare DROP here queued every page behind any reader

_REPORT = """
    SELECT count(*), count(*) FILTER (WHERE split),
           count(*) FILTER (WHERE gender = 'F'),
           count(*) FILTER (WHERE gender = 'M')
    FROM person_gender
"""


def available(cur):
    cur.execute("SELECT to_regclass('public.person_gender')")
    return cur.fetchone()[0] is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    with getConn() as conn, conn.cursor() as cur:
        if args.write:
            cur.execute("SET max_parallel_workers_per_gather = 4")
            cur.execute("SET work_mem = '1GB'")
            build(cur)
            conn.commit()
            from database import swapTable
            swapTable(conn, "person_gender", "person_gender_new")
            cur.execute("ANALYZE person_gender")
            conn.commit()
        if not available(cur):
            print("  person_gender: not built (run with --write)")
            return
        cur.execute("SELECT to_regclass('public.person_gender_changed')")
        if cur.fetchone()[0] is not None:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE was IS NOT NULL) "
                        "FROM person_gender_changed")
            n_ch, n_flip = cur.fetchone()
            print(f"  person_gender_changed: {n_ch:,} people differ from the "
                  f"previous table ({n_flip:,} flipped, the rest new)")
        cur.execute(_REPORT)
        n, n_split, n_f, n_m = cur.fetchone()
        print(f"  person_gender: {n:,} people with a labelled race "
              f"({n_m:,} M, {n_f:,} F), {n_split:,} split")
        cur.execute("""SELECT person_id, gender, n_m, n_f FROM person_gender
                       WHERE split ORDER BY n_m + n_f DESC LIMIT 8""")
        for pid, g, nm, nf in cur.fetchall():
            print(f"    split: person {pid}  majority {g}  M {nm:,}  F {nf:,}")


if __name__ == "__main__":
    main()
