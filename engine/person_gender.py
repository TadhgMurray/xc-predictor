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
import sys

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

PROFILE_VOTES = 3            # a scraped profile's weight against labelled rows
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


_BUILD = f"""
DROP TABLE IF EXISTS person_gender_new;
CREATE TABLE person_gender_new AS
WITH ev AS (
    SELECT r.person_id,
           {labelExpr(ROW_LABEL_PACK['XC'])} AS g
    FROM   results r
    LEFT   JOIN meets m        ON m.div_id = r.div_id AND r.source = 'anet'
    LEFT   JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id AND r.source = 'tfrrs'
    WHERE  r.person_id IS NOT NULL
    UNION ALL
    SELECT r.person_id, {labelExpr('r.event_short')}
    FROM   results_tf r
    WHERE  r.person_id IS NOT NULL
      AND  r.event_short ~* '{M_RX}|{F_RX}'
    UNION ALL
    -- ★ THE FIELD, WHERE THE DIVISION SAYS NOTHING (2026-09-29). A college
    --   division on athletic.net is often just "Collegiate": no gender
    --   word, so a first-year with no scraped profile had no vote at all,
    --   was normalised as 'college_unknown_gender' on the women's 6 km
    --   anchor, and rated like a woman -- 45 men at the Bates Preview
    --   2026, a 13:23.6 at 140.2 beside the winner's 13:21.8 at 109.2.
    --   A cross country division is one gender; its known runners say
    --   which, when there are {FIELD_MIN}+ of them and one side outnumbers
    --   the other {FIELD_SHARE} to 1. Only for rows whose own label is empty.
    SELECT r.person_id,
           CASE WHEN f.fm >= {FIELD_MIN} AND f.fm >= {FIELD_SHARE} * f.ff THEN 'M'
                WHEN f.ff >= {FIELD_MIN} AND f.ff >= {FIELD_SHARE} * f.fm THEN 'F' END
    FROM   results r
    JOIN   (SELECT fr.source, fr.meet_id, fr.div_id,
                   count(*) FILTER (WHERE fa.gender = 'M') AS fm,
                   count(*) FILTER (WHERE fa.gender = 'F') AS ff
            FROM   results fr JOIN athletes fa ON fa.athlete_id = fr.athlete_id
            WHERE  fa.gender IN ('M', 'F')
            GROUP  BY fr.source, fr.meet_id, fr.div_id) f
           ON f.source = r.source AND f.meet_id = r.meet_id
          AND f.div_id IS NOT DISTINCT FROM r.div_id
    LEFT   JOIN meets m        ON m.div_id = r.div_id AND r.source = 'anet'
    LEFT   JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id AND r.source = 'tfrrs'
    WHERE  r.person_id IS NOT NULL
      AND  ({labelExpr(ROW_LABEL_PACK['XC'])}) IS NULL
),
-- ★ THE PROFILES VOTE TOO, AT {PROFILE_VOTES} EACH (2026-09-06). A boy
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
),
rows_ AS (
    SELECT person_id,
           count(*) FILTER (WHERE g = 'M') AS n_m,
           count(*) FILTER (WHERE g = 'F') AS n_f
    FROM   ev WHERE g IS NOT NULL GROUP BY person_id
),
profs AS (
    SELECT person_id,
           count(*) FILTER (WHERE gender = 'M') AS p_m,
           count(*) FILTER (WHERE gender = 'F') AS p_f
    FROM   prof GROUP BY person_id
)
SELECT person_id,
       CASE WHEN n_f + {PROFILE_VOTES} * p_f > n_m + {PROFILE_VOTES} * p_m
            THEN 'F' ELSE 'M' END                        AS gender,
       n_m, n_f,
       -- split is decided by the ROWS alone: a profile is one letter, not
       -- evidence of a second person
       (least(n_m, n_f) >= 5
        AND least(n_m, n_f) * 3 >= n_m + n_f)               AS split
FROM (
    SELECT COALESCE(r.person_id, p.person_id) AS person_id,
           COALESCE(r.n_m, 0) AS n_m, COALESCE(r.n_f, 0) AS n_f,
           COALESCE(p.p_m, 0) AS p_m, COALESCE(p.p_f, 0) AS p_f
    FROM   rows_ r FULL OUTER JOIN profs p USING (person_id)
) s
WHERE n_m + n_f + p_m + p_f > 0;
ALTER TABLE person_gender_new ADD PRIMARY KEY (person_id);
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
            cur.execute(_BUILD)
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
