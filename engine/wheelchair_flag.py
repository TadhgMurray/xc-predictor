"""
wheelchair_flag.py -- decide WHO races a chair, once, so the engine can refuse
to rate them at all.

    python engine/wheelchair_flag.py              # report, writes nothing
    python engine/wheelchair_flag.py --write      # build the tables
    python engine/wheelchair_flag.py --review 40  # the doubtful ones, by name

Run from the PROJECT ROOT, BEFORE speed_ratings.py -- it is a fact about
people that the pack then reads. Issue #14.

★ THE FILTER THAT EXISTED WAS A RACE FILTER, AND THE QUESTION IS ABOUT A
  PERSON. speed_ratings_db dropped rows whose division matched
  (wheelchair|seated|ambulator). That keeps a chair race out of the solve --
  but ONE chair race under an ordinarily-named division is enough to set that
  athlete's ability, and every ordinary race they run is then rated against
  it. A racing chair covers 1500m far faster than a runner, so the ability
  comes out absurd and the pollution spreads through the pair solve to
  everyone they raced. Excluding the person is the only version of this that
  actually holds.

! THE OLD FILTER READ meets.division, WHICH IS anet ONLY, SO THE tfrrs BLOB
  IS READ HERE TOO -- AND MEASURING IT DID NOT VINDICATE THE REASON I ADDED
  IT. An earlier draft of this comment asserted that a tfrrs wheelchair
  division "was invisible" to the old filter, by analogy with Thetford and
  the distance tools. --census says otherwise: of 2,904 chair races,
  2,571 come from TF event names and 333 from anet XC divisions, and
  meets_tfrrs.division_distances -> <div_id> ->> 'div_name' contributes
  EXACTLY ZERO. tfrrs XC, in this corpus, has no chair divisions.

  The read stays -- it costs one LEFT JOIN, it is correct if such a division
  ever appears, and a filter that covers both feeds needs no caveat about
  which one it covers. But it is closing a hole nothing was falling through,
  not recovering athletes the old filter lost, and the comment should not
  claim otherwise.

★ ANY CONFIRMED CHAIR RACE CONDEMNS THE WHOLE CAREER, AND THAT ASYMMETRY IS
  DELIBERATE. The two mistakes are not equal: excluding a runner costs one
  athlete their ratings, while admitting a chair athlete corrupts an ability,
  every rating built on it, and -- through the pair solve -- the people they
  raced against. A division titled "17-18 Wheelchair" is not ambiguous, so
  the false-positive risk is a mislabelled division rather than a plausible
  reading, and --review prints exactly the population where that could hide:
  the athletes whose chair races are a MINORITY of their career.

! IT FLAGS, IT NEVER DELETES. The rows stay in results; the athlete keeps
  their page. wheelchair_person is a list the raters consult, so the decision
  is one DROP TABLE away from being reversed.

★ AMBULATORY ATHLETES ARE EXCLUDED PER-PERSON TOO -- OWNER'S CALL,
  2026-08-29, AND IT WAS PUT AS A QUESTION BECAUSE IT IS NOT THE SAME
  ARGUMENT AS THE CHAIR.

  A racing chair is different equipment, so a chair time on a runner's scale
  is meaningless and the exclusion is physics. Ambulatory means running on
  legs, and those open-race times are legitimate results -- so this is a
  policy about who the boards are for, not a measurement. The alternative
  considered and rejected was to keep 'ambulator' as a RACE filter only.

⚠ SO KNOW WHAT IT COSTS. Measured on the corpus at the time of the decision:
  481 people, 2,904 chair-or-ambulatory races, and 20,796 results withheld in
  total -- most of that second number being ORDINARY races by athletes who
  ran them. The review list is dominated by ambulatory sprints and field
  events (100m dashes, javelin, shot put), which the engine never rated
  anyway: _tfQuery already excludes is_field and everything under 800m. What
  this rule actually withholds, over and above the filter it replaces, is
  those athletes' 800m-and-up races.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from psycopg2.extras import RealDictCursor

from database import getConn


# ★ ONE PATTERN, NAMED ONCE. build_ranking_results._WHEELCHAIR_RX carries the
#   same three alternatives for its board gate; if this ever diverges, a row
#   can be rated by the engine and hidden by the site, or the reverse, and
#   nothing would say which is right.
#
# ! 'ambulator' IS IN THE FAMILY ON PURPOSE. It catches "Ambulatory", a para
#   classification that is not a running division either.
_WORDS = r"wheelchair|seated|ambulator|paralymp|para athl|adaptive"

# ★ AND THE WORLD PARA ATHLETICS CLASS CODES, because a meet often prints the
#   CODE and not the word: "Boys 100m T44", "Girls Shot Put F46". The classes,
#   per the owner's list (2026-08-29):
#       T/F 11-13  visual impairment      T/F 40-41  short stature
#       T/F 20     intellectual           T/F 42-47  limb impairments
#       T/F 31-38  coordination           T   51-54  wheelchair racing
#                                          F   51-57  seated throws
#                                          T/F 61-64  lower-limb amputee
#
# ! 51-57, NOT 51-54, AND THE OWNER'S LIST SAID 54. That list gives the TRACK
#   range; the seated THROWS classes run F51 to F57, so F55/F56/F57 would
#   have slipped through. Widening costs nothing because no T55/T56/T57
#   exists to be caught by mistake -- the pattern is now a superset of both
#   letters' real ranges rather than the intersection.
#
# ★ BOTH LETTERS, MATCHED THE SAME WAY -- OWNER'S CALL, 2026-08-30. T and F
#   are not two systems: they are the same classification on a track or a
#   field event, so T35 and F35 are one athlete's class. The instruction is
#   simply that none of them are rated and none of them reach the best-times
#   or marks boards.
#
# ⚠ AN EARLIER VERSION REQUIRED A PARA WORD BESIDE AN F CODE, ON THE THEORY
#   THAT MASTERS ATHLETICS WRITES "F40" FOR WOMEN AGED 40-44. That theory was
#   never measured -- it was written into this comment as though it had been,
#   which was wrong of me. It is now moot: the owner's rule is exclusion
#   either way, and a masters woman caught by it loses board eligibility she
#   was not going to be ranked for. If a masters population ever matters,
#   measure it before re-adding a guard.
_CODES = r"\m[TF](1[1-3]|20|3[1-8]|4[0-7]|5[1-7]|6[1-4])\M"

# ! \m AND \M ARE POSTGRES WORD BOUNDARIES, not \b. Postgres regex spells
#   them this way, and \b there means BACKSPACE -- a filter that silently
#   matches nothing at all.
WHEELCHAIR_RX = rf"({_WORDS})|({_CODES})"


_RACES = """
    DROP TABLE IF EXISTS wheelchair_race;
    CREATE UNLOGGED TABLE wheelchair_race AS
        -- XC, both feeds. The join is SOURCE-SCOPED: `meets` describes anet
        -- and a tfrrs div_id is a different namespace, so joining it to both
        -- would match the wrong division by coincidence of number.
        SELECT 'XC'::text                                     AS sport,
               r.result_id, r.person_id, r.date,
               COALESCE(m.division,
                        mt.division_distances -> r.div_id::text ->> 'div_name')
                                                              AS label,
               -- ! anet FIRST IN THE TEST, NOT JUST IN THE COALESCE. A row
           --   matching on both would otherwise have to be attributed by
           --   guess; --census is the only thing that can say which source
           --   is doing the work, so it must not be an artefact of ordering.
           CASE WHEN COALESCE(m.division, '') ~* '{rx}'
                    THEN 'anet.division' ELSE 'tfrrs.div_name' END AS via
        FROM   results r
        LEFT   JOIN meets m
                    ON m.div_id = r.div_id AND r.source = 'anet'
        LEFT   JOIN meets_tfrrs mt
                    ON mt.meet_id = r.meet_id AND r.source = 'tfrrs'
        WHERE  r.person_id IS NOT NULL
          AND (COALESCE(m.division, '') ~* '{rx}'
            OR COALESCE(mt.division_distances -> r.div_id::text ->> 'div_name',
                        '') ~* '{rx}')
        UNION ALL
        -- TF keeps the distance and the class in the EVENT name, which is
        -- where "Wheelchair 1500" lives. No division blob to read.
        SELECT 'TF', r.result_id, r.person_id, r.date, r.event_short,
               'tf.event_short'
        FROM   results_tf r
        WHERE  r.person_id IS NOT NULL
          AND (COALESCE(r.event_short, '') ~* '{rx}'
);
    CREATE INDEX ON wheelchair_race (person_id);
    ANALYZE wheelchair_race;
"""

# ! n_total COUNTS BOTH SPORTS, because the share is about a CAREER. An
#   athlete with one chair XC race and forty track races is exactly the row
#   --review exists to show, and counting only XC would hide them at 100%.
_PEOPLE = """
    DROP TABLE IF EXISTS wheelchair_person;
    CREATE TABLE wheelchair_person AS
    WITH tot AS (
        SELECT person_id, count(*) AS n_total FROM (
            SELECT person_id FROM results     WHERE person_id IS NOT NULL
            UNION ALL
            SELECT person_id FROM results_tf  WHERE person_id IS NOT NULL
        ) x GROUP BY 1
    )
    SELECT w.person_id,
           count(*)                                        AS n_chair,
           COALESCE(t.n_total, count(*))                   AS n_total,
           min(w.date)                                     AS first_date,
           max(w.date)                                     AS last_date,
           mode() WITHIN GROUP (ORDER BY w.label)          AS label,
           mode() WITHIN GROUP (ORDER BY w.via)            AS via
    FROM   wheelchair_race w
    LEFT   JOIN tot t ON t.person_id = w.person_id
    GROUP  BY w.person_id, t.n_total;
    ALTER TABLE wheelchair_person ADD PRIMARY KEY (person_id);
    ANALYZE wheelchair_person;
"""

# ★ THE TABLE, POSSIBLY EMPTY, FOR EVERY READER THAT ANTI-JOINS IT (2026-09-03).
#   The boards and fill_ratings exclude chair athletes by person through this
#   table; a database that has never run --write must still build. Same
#   contract as twin_flag.ensureTable. Column shape matches _PEOPLE so a
#   later --write can DROP and recreate it without a reader noticing.
_EMPTY_DDL = """
    CREATE TABLE IF NOT EXISTS wheelchair_person (
        person_id  bigint PRIMARY KEY,
        n_chair    bigint,
        n_total    bigint,
        first_date text,
        last_date  text,
        label      text,
        via        text
    )
"""


def ensureTable(cur):
    """wheelchair_person exists, possibly empty. Returns its row count so
    the caller can say out loud when the exclusion is a no-op."""
    cur.execute(_EMPTY_DDL)
    cur.execute("SELECT count(*) FROM wheelchair_person")
    return int(cur.fetchone()[0])


_SUMMARY = """
    SELECT count(*)                                        AS people,
           sum(n_chair)                                    AS chair_races,
           sum(n_total)                                    AS all_their_races,
           count(*) FILTER (WHERE n_chair * 2 < n_total)   AS minority,
           count(*) FILTER (WHERE via = 'tfrrs.div_name')  AS via_tfrrs,
           count(*) FILTER (WHERE via = 'tf.event_short')  AS via_tf
    FROM   wheelchair_person
"""

# ★ THE ONES A MISLABELLED DIVISION WOULD HIDE IN. A career that is mostly
#   chair races is not in doubt. A career with one chair race among forty is
#   either the leak this exists to close or a division typed wrong, and only
#   a human reading the label can tell which.
_REVIEW = """
    SELECT p.person_id, p.n_chair, p.n_total, p.label, p.via,
           p.first_date, p.last_date,
           a.first_name, a.last_name, a.school
    FROM   wheelchair_person p
    LEFT   JOIN LATERAL (
        SELECT at.first_name, at.last_name, at.school
        FROM   results r JOIN athletes at ON at.athlete_id = r.athlete_id
        WHERE  r.person_id = p.person_id
        LIMIT  1
    ) a ON TRUE
    WHERE  p.n_chair * 2 < p.n_total
    ORDER  BY p.n_total DESC
    LIMIT  %(lim)s
"""


def main():
    ap = argparse.ArgumentParser(
        description="Flag chair athletes so the engine never rates them.")
    ap.add_argument("--write", action="store_true",
                    help="build wheelchair_race and wheelchair_person")
    ap.add_argument("--review", type=int, default=0, metavar="N",
                    help="list N athletes whose chair races are a minority")
    ap.add_argument("--census", action="store_true",
                    help="races and people per source, so the two-feed claim "
                         "can be checked rather than assumed")
    args = ap.parse_args()

    with getConn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # ! BUILT EVEN FOR A DRY RUN, then rolled back. The report has to
        #   describe what --write WOULD do, and the only honest way to say
        #   that is to do it and not commit. UNLOGGED + a real table, so a
        #   rollback leaves nothing behind.
        cur.execute(_RACES.format(rx=WHEELCHAIR_RX))
        cur.execute(_PEOPLE)

        cur.execute(_SUMMARY)
        s = cur.fetchone()
        print(f"\n{s['people']:,} people race a chair, over "
              f"{s['chair_races']:,} chair races.")
        print(f"  excluding them withholds {s['all_their_races']:,} results "
              f"in total (chair and running alike -- that is the point)")
        print(f"  found via the tfrrs blob: {s['via_tfrrs']:,} people "
              f"-- INVISIBLE to the filter this replaces")
        print(f"  found via TF event names: {s['via_tf']:,}")
        print(f"  chair races are a MINORITY of the career for "
              f"{s['minority']:,} -- read those with --review")

        # ! THE PER-PERSON `via` IS A mode(), SO THE SUMMARY CANNOT ANSWER
        #   "did reading the tfrrs blob find anything". Someone with one
        #   tfrrs row and three TF rows reports as TF. This counts RACES.
        if args.census:
            cur.execute("SELECT via, sport, count(*) AS races, "
                        "count(DISTINCT person_id) AS people "
                        "FROM wheelchair_race GROUP BY 1, 2 ORDER BY 3 DESC")
            print("\ncensus by source:")
            for r in cur.fetchall():
                print(f"  {r['via']:<16} {r['sport']:<3} "
                      f"{r['races']:>7,} races  {r['people']:>6,} people")

        if args.review:
            cur.execute(_REVIEW, {"lim": args.review})
            print("\nthe doubtful ones (chair races < half the career):")
            for r in cur.fetchall():
                who = " ".join(x for x in (r["first_name"], r["last_name"]) if x)
                print(f"  {r['person_id']}  {r['n_chair']:>3}/{r['n_total']:<4} "
                      f"{(who or '?')[:24]:<24} {(r['school'] or '')[:22]:<22} "
                      f"{r['first_date']}..{r['last_date']}  "
                      f"[{r['via']}] {(r['label'] or '')[:34]}")

        if args.write:
            conn.commit()
            print("\n[chair] wrote wheelchair_race and wheelchair_person")
            print("        speed_ratings_db reads wheelchair_person on the "
                  "next pack -- rerun the engine for it to take effect")
        else:
            conn.rollback()
            print("\n       DRY RUN -- nothing written. Pass --write.")


if __name__ == "__main__":
    main()
