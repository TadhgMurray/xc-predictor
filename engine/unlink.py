"""
unlink.py -- find person_ids that hold more than one person, and split them.

THE SIGNAL: IMPLIED CLASS YEAR
    For a real athlete, `season + (12 - grade)` is a constant. A grade 9 in
    2018 and a grade 12 in 2021 both imply the class of 2021. Two rows under
    one person_id that imply class years eight years apart are two people.

    Luke Morelli's id is the example this was written for: a 2018 grade 9 at
    Pope John Central implies 2021, and 2024-25 grades 7 and 8 at Foundation
    Academy imply 2029 and 2029. One id, two athletes, and every rating built
    on it is a blend of both.

THE SECOND SIGNAL: A HOLE IN THE CAREER
    A racing career is continuous. Measured over 7.4M people, 99.1% have no
    gap longer than five years between consecutive racing seasons; only 3,328
    have one of twenty years or more. A gap that size is not an athlete taking
    time off, it is two athletes sharing an id.

    Erika Suhy is the example this was added for: two races at Robert Morris
    in autumn 2000, then twelve races unattached in 2026, and nothing in the
    twenty-five years between. One anet identity, two people.

★ AND THE GAP IS WHAT THE CLASS-YEAR RULE CANNOT SEE. Suhy's 2000 season reads
  "Fr" and her 2026 season reads "-". Neither is a numeric grade, so she
  contributed zero usable seasons and never entered the clustering at all.
  Dates need no grade, which is exactly the population the first signal is
  blind to.

! IT ONLY USES NUMERIC GRADES FOR CLASS YEAR. The collegiate class words carry
  no year offset that survives this arithmetic -- a "Senior" in 2018 and a
  "Senior" in 2023 are two different people and both are legitimately senior.
  A season with a word grade now still LOADS, so its dates can testify, but it
  contributes no class year.

⚠ AND WORD GRADES MUST NOT BE GIVEN ONE. On a years-of-schooling scale
  (Fr=13 ... Sr=16) Kerem Ayhan's implied class year runs 2006 to 2012 -- a
  six-year spread that would split ONE REAL PERSON in two. He is a Lehigh SR-4
  who went pro and whose club rows read grade 12. The identity is right and
  the grade is wrong; grade_sanity's rule 5b fixes the grade. This script must
  not "fix" the identity.

! AND IT SPLITS, IT DOES NOT MERGE. Deciding two ids are the same person is a
  much harder claim than deciding one id is two people, and a wrong merge is
  far more damaging than a missed one. This only ever takes apart.

WHAT IT WRITES
    Nothing, unless --write. With it: the minority clusters get fresh
    person_ids in `results` and `results_tf`, and a `person_split` audit table
    records every reassignment so it can be traced or reversed.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, "scripts")
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# * TWO YEARS OF SLACK, NOT ZERO. A grade is recorded per race and is
#   sometimes a year stale, an athlete can repeat a year, and a season
#   straddling a rollover can read either side. One year of disagreement is
#   noise; a gap this size or larger is a different person.
MAX_CLASS_SPREAD = 3

# Seasons below this in a cluster are folded into the nearest larger one
# rather than becoming their own person. A single stray season is far more
# likely to be a bad grade than a whole separate athlete.
MIN_SEASONS_PER_CLUSTER = 1

# * FIFTEEN YEARS, WHERE THE DISTRIBUTION STOPS BEING PEOPLE. Consecutive
#   racing seasons, biggest gap per person, over 7.4M people:
#
#       under 6 years   7,349,890     99.1%
#       6-9                41,536
#       10-14              17,305
#       15-19               6,994
#       20+                 3,328      0.04%
#
#   Six to nine years is a college athlete returning for road races and is
#   ordinary. Ten to fourteen still holds real comebacks. Fifteen is where
#   what remains is 10,322 people and essentially none of them is one athlete.
#
# ⚠ THE GAP OPENS A CLUSTER; IT DOES NOT DECIDE A SPLIT. Only 158 of those
#   10,322 have three or more seasons on BOTH sides -- 98.5% are one real
#   career plus a thin fragment. The existing thin-cluster guards below
#   (MIN_RACES_TO_SPLIT, or a disjoint school or name) still have to agree,
#   and for Suhy they do: "Robert Morris" and "11-Unattached" share nothing.
MAX_RACING_GAP = 15


# ! TEST MEETS ARE RESERVED SENTINEL DATES, NOT DATA. 2222-09-01, 0025-09-20
#   and their siblings are placeholders for meets that never happened: 721
#   rows across both tables. Without this filter they imply class years of 25
#   and 2234, which read as a two-thousand-year spread and forked a new person
#   off every athlete who touched one. Nearly all of the first dry run's
#   42,078 flagged ids were that, not real merges.
#
#   The same insane-years rule the rest of the engine applies, spelled here
#   because this script runs before any of it.
YEAR_MIN = 1990
YEAR_MAX = 2035

# ★ A GRADE ON AN UNATTACHED OR CLUB ROW IS NOT A SCHOOL'S GRADE (owner,
#   2026-10-12: /athlete/2000002910, Jack Falkowski -- a redshirt freshman
#   racing unattached at college meets the autumn after his senior year,
#   his rows reading "9"). Class of 2025 by his school seasons, "class of
#   2029" by the unattached one: a spread of four, and the id was split.
#   An entry form's grade column on an unattached or club entry is the
#   scraper copying something forward (a profile's year, a club's year
#   count -- grade_sanity rules 7 and 8), not a school saying which class
#   the athlete is in. Only a row on a SCHOOL-shaped team gives a class
#   year; the others still load and testify by their dates (the gap rule).
def _schoolShapedSql(col="school"):
    from grade_sanity import CLUB_NAME_RE, NOT_SCHOOL_RE
    x = f"lower(btrim(COALESCE({col}, '')))"
    return f"NOT ({x} ~ '{NOT_SCHOOL_RE}' OR {x} ~ '{CLUB_NAME_RE}')"


def classYearsSql(only=None):
    """The per-season class years; `only` names a temp table of person_ids
    (column person_id) to restrict the scan to (rejoin)."""
    return _CLASS_YEARS_SQL.replace("{SCHOOL_SHAPED}", _schoolShapedSql()).replace(
        "{ONLY}", f"AND person_id IN (SELECT person_id FROM {only})" if only else "")


_CLASS_YEARS_SQL = f"""
    WITH allraces AS (
        SELECT person_id,
               substr(date, 1, 4)::int         AS season,
               NULLIF(TRIM(grade), '')         AS grade,
               school,
               NULLIF(TRIM(athlete_name), '')  AS athlete_name
        FROM   results
        WHERE  person_id IS NOT NULL AND date IS NOT NULL
          AND  substr(date, 1, 4)::int BETWEEN {YEAR_MIN} AND {YEAR_MAX}
          {{ONLY}}
        UNION ALL
        SELECT person_id, substr(date, 1, 4)::int, NULLIF(TRIM(grade), ''),
               school, NULLIF(TRIM(athlete_name), '')
        FROM   results_tf
        WHERE  person_id IS NOT NULL AND date IS NOT NULL
          AND  substr(date, 1, 4)::int BETWEEN {YEAR_MIN} AND {YEAR_MAX}
          {{ONLY}}
    ),
    per_season AS (
        SELECT person_id, season,
               mode() WITHIN GROUP (ORDER BY grade)
                   FILTER (WHERE grade ~ '^[0-9]+$'
                             AND {{SCHOOL_SHAPED}})        AS grade,
               mode() WITHIN GROUP (ORDER BY school)      AS school,
               mode() WITHIN GROUP (ORDER BY athlete_name) AS name,
               count(*)                                   AS n_races
        FROM   allraces
        GROUP  BY 1, 2
    )
    -- ! EVERY SEASON LOADS NOW, GRADED OR NOT. The class year is still
    --   computed only where a numeric grade 1-12 exists -- everything else
    --   gets NULL and is clustered on dates alone. Filtering these rows OUT
    --   is what made Suhy invisible: both her seasons carry a word grade or
    --   none, so the old WHERE removed her entirely.
    SELECT person_id, season, grade, school, name, n_races,
           CASE WHEN grade IS NOT NULL AND grade::int BETWEEN 1 AND 12
                THEN season + (12 - grade::int) END AS class_year
    FROM   per_season
    ORDER  BY person_id, season
"""


def loadSeasons(cur, only=None):
    """{person_id: [(season, class_year, grade, school, n_races), ...]}."""
    cur.execute(classYearsSql(only))
    out = {}
    for pid, season, grade, school, name, n_races, class_year in cur.fetchall():
        out.setdefault(int(pid), []).append(
            (int(season),
             None if class_year is None else int(class_year),
             grade, school, int(n_races), name))
    return out


def cluster(seasons):
    """Split one person's seasons into groups, on class year OR a racing gap.

    Single-link, sorted by SEASON. A new cluster starts where either boundary
    fires between consecutive seasons:

        class year   the two seasons imply class years more than
                     MAX_CLASS_SPREAD apart -- only when BOTH have one
        racing gap   more than MAX_RACING_GAP calendar years with no racing
                     at all, which needs no grade from either side

    * SINGLE-LINK, NOT K-MEANS OR A FIXED COUNT. The number of people behind an
      id is exactly what is unknown, and a gap-based split needs no guess at
      it. A genuine athlete's class years drift by a year or two and never
      leave a hole; two people leave one.

    ★ SORTED BY SEASON, NOT BY CLASS YEAR, AND THAT HAD TO CHANGE. A season
      with no numeric grade has no class year to sort on, and the racing gap
      is a statement about dates. Season order is the only ordering both
      boundaries can read. For a normally-progressing athlete the two orders
      are the same anyway -- class year is season plus a constant.

    ! A MISSING CLASS YEAR IS NOT A DISAGREEMENT. When either side lacks one,
      the class-year test abstains and only the gap can open a boundary. An
      ungraded season carried between two graded ones stays with them.
    """
    rows = sorted(seasons, key=lambda r: r[0])       # by season
    groups, current = [], [rows[0]]
    for prev, row in zip(rows, rows[1:]):
        gap = row[0] - prev[0] > MAX_RACING_GAP
        drift = (prev[1] is not None and row[1] is not None
                 and abs(row[1] - prev[1]) > MAX_CLASS_SPREAD)
        if gap or drift:
            groups.append(current)
            current = [row]
        else:
            current.append(row)
    groups.append(current)
    return groups


# * A ONE-RACE CLUSTER IS USUALLY A TYPO, NOT A PERSON. Half of the first
#   run's 40,989 splits forked one or two races off an established athlete,
#   and a single mistyped grade looks exactly like that. Minting a person for
#   it turns two races into an unrateable fragment.
#
# ! BUT A TYPO DOES NOT MOVE YOU TO ANOTHER SCHOOL OR CHANGE YOUR NAME. Luke
#   Morelli's minority cluster is ONE race, at Pope John Central, while the
#   majority is at Foundation Academy. A race-count floor alone would keep him
#   merged, which is the case that started this.
#
#   So a thin cluster splits only when something other than the grade also
#   disagrees. Both tests are disjointness, not difference: an athlete who
#   transfers shares no school between clusters either, but they also progress
#   normally and never reach this code.
MIN_RACES_TO_SPLIT = 3


def _norm(value):
    """Lowercase, punctuation stripped, tokens sorted.

    Sorted tokens so "Morelli, Luke" and "Luke Morelli" compare equal: the two
    scrapers disagree on order and neither is wrong.
    """
    if not value:
        return None
    keep = "".join(c if c.isalnum() or c.isspace() else " " for c in value)
    return " ".join(sorted(keep.lower().split())) or None


def _disjoint(group_a, group_b, index):
    """True when the two clusters share NO value at all for that field.

    Missing values do not count as disagreement: a cluster with no recorded
    school cannot contradict one that has one.
    """
    a = {_norm(r[index]) for r in group_a} - {None}
    b = {_norm(r[index]) for r in group_b} - {None}
    return bool(a) and bool(b) and not (a & b)


def findSplits(by_person):
    """[(person_id, [cluster, ...])] for ids holding more than one person.

    The LARGEST cluster by race count keeps the original id: it is the one
    most of the corpus already refers to, so moving it would break the most
    links elsewhere.
    """
    splits = []
    for pid, seasons in by_person.items():
        if len(seasons) < 2:
            continue
        groups = cluster(seasons)
        if len(groups) < 2:
            continue
        groups.sort(key=lambda g: -sum(r[4] for r in g))

        # The majority keeps the id. Every other cluster has to earn its own.
        keep = [groups[0]]
        for g in groups[1:]:
            races = sum(r[4] for r in g)
            if (races >= MIN_RACES_TO_SPLIT
                    or _disjoint(groups[0], g, 3)      # school
                    or _disjoint(groups[0], g, 5)):    # name
                keep.append(g)
            # Otherwise the cluster is dropped from consideration: its seasons
            # stay on the original id, which is what a mistyped grade deserves.
        if len(keep) < 2:
            continue
        splits.append((pid, keep))
    return splits


def report(splits, limit=25):
    total_new = sum(len(g) - 1 for _, g in splits)
    print(f"\n[unlink] {len(splits):,} person_ids hold more than one athlete")
    print(f"[unlink] {total_new:,} new person_ids would be minted")
    if not splits:
        return

    # ! THE DISTRIBUTION, NOT JUST THE WORST TAIL. Sorting the sample by widest
    #   spread shows only the most extreme cases, which is exactly where a
    #   systematic artefact hides -- the first run's top rows were all test
    #   meets and said nothing about the other 42,000.
    from collections import Counter

    def spread(groups):
        """Widest class-year disagreement, or the racing gap when there is no
        class year to disagree about. Both are years, both are the size of the
        thing that opened the split, so one column can show either."""
        years = [r[1] for g in groups for r in g if r[1] is not None]
        if len(years) >= 2:
            return max(years) - min(years)
        seasons = [r[0] for g in groups for r in g]
        return max(seasons) - min(seasons)

    buckets = Counter()
    for _, g in splits:
        s = spread(g)
        buckets["4" if s == 4 else "5" if s == 5 else
                "6-10" if s <= 10 else "11-20" if s <= 20 else "21+"] += 1
    print("\n    class-year spread of the split ids")
    for k in ("4", "5", "6-10", "11-20", "21+"):
        n = buckets.get(k, 0)
        print(f"        spread {k:<6} {n:>8,}  {n / len(splits):6.1%}")

    # And how thin the minority clusters are: a one-race cluster splitting off
    # a hundred-race athlete is far more likely a bad grade than a person.
    sizes = Counter(min(sum(r[4] for r in g) for g in gs[1:]) for _, gs in splits)
    thin = sum(v for k, v in sizes.items() if k <= 2)
    print(f"\n    minority cluster is 1-2 races: {thin:,}  "
          f"({thin / len(splits):.1%})")
    print()

    # Worst first: the widest spread is the most certainly wrong.
    for pid, groups in sorted(splits, key=lambda s: -spread(s[1]))[:limit]:
        print(f"    person_id {pid}  (class years span {spread(groups)})")
        for i, g in enumerate(groups):
            tag = "keeps id" if i == 0 else "NEW id"
            yrs = f"{min(r[0] for r in g)}-{max(r[0] for r in g)}"
            schools = sorted({r[3] for r in g if r[3]})[:2]
            cls = [r[1] for r in g if r[1] is not None]
            cls_txt = (f"class {min(cls)}-{max(cls)}" if cls
                       else "class  --   ")
            print(f"        {tag:9} seasons {yrs:9} {cls_txt:<16} "
                  f"{sum(r[4] for r in g):>4} races  {', '.join(schools)[:40]}")
        print()


# above link_tfrrs_rows.MINT_BASE's ranges (tfrrs 1e9, DirectAthletics 1.5e9,
# each up to +MINT_MAX_NATIVE); tests/test_unlink_ids.py holds them apart
SPLIT_BASE = 2_000_000_000


def write(cur, splits):
    """Reassign the minority clusters and record every move.

    ! NEW IDS COME FROM ONE max(), NOT FROM A SEQUENCE PER ROW. Reading the
      maximum once and counting up guarantees no collision with an existing id
      and no dependence on a sequence that other scripts may also be drawing
      from.
    ⚠ AND FROM ITS OWN RANGE, ABOVE EVERY MINT (2026-09-29). link_tfrrs_rows
      mints tfrrs people at 1,000,000,000 + the tfrrs id and DirectAthletics
      people at 1,500,000,000 + theirs, up to 2,000,000,000. max()+1 landed
      INSIDE that range, above today's highest id, where a later mint of a
      new tfrrs/DA athlete would take the same number and merge two unrelated
      people. A split person is numbered from SPLIT_BASE up, where no mint
      reaches.
    """
    from psycopg2.extras import execute_values

    cur.execute("""
        SELECT GREATEST(
            COALESCE((SELECT max(person_id) FROM results
                      WHERE person_id >= %(b)s), 0),
            COALESCE((SELECT max(person_id) FROM results_tf
                      WHERE person_id >= %(b)s), 0))
    """, {"b": SPLIT_BASE})
    next_id = max(int(cur.fetchone()[0]) + 1, SPLIT_BASE)

    moves = []            # (old_id, season, new_id)
    for pid, groups in splits:
        for g in groups[1:]:                       # groups[0] keeps the id
            for season, class_year, grade, school, n_races, name in g:
                moves.append((pid, season, next_id, class_year, school))
            next_id += 1

    # ! KEPT ACROSS RUNS, NOT DROPPED (2026-10-12): rejoin() reads every
    #   split ever made, and a second --write used to erase the first's.
    cur.execute("""CREATE TABLE IF NOT EXISTS person_split (
                       old_person_id bigint NOT NULL,
                       season        int    NOT NULL,
                       new_person_id bigint NOT NULL,
                       class_year    int,          -- NULL when the season
                                                    -- carried no numeric grade
                       school        text,
                       PRIMARY KEY (old_person_id, season))""")
    execute_values(cur, "INSERT INTO person_split VALUES %s ON CONFLICT DO NOTHING",
                   moves, page_size=5000)
    cur.execute("CREATE INDEX IF NOT EXISTS person_split_new_idx "
                "ON person_split (new_person_id)")
    cur.execute("ANALYZE person_split")

    # ! THE UPDATE JOINS ON SEASON, NOT JUST person_id. A split moves SOME of
    #   an id's seasons and leaves the rest; updating by person_id alone would
    #   move every race the athlete ever ran.
    cur.execute("DROP TABLE IF EXISTS tmp_split_moves; CREATE TEMP TABLE tmp_split_moves "
                "(old_person_id bigint, season int, new_person_id bigint)")
    execute_values(cur, "INSERT INTO tmp_split_moves VALUES %s",
                   [m[:3] for m in moves], page_size=5000)
    for table in ("results", "results_tf"):
        cur.execute(f"""
            UPDATE {table} r
            SET    person_id = s.new_person_id
            FROM   tmp_split_moves s
            WHERE  r.person_id = s.old_person_id
              AND  substr(r.date, 1, 4)::int = s.season
        """)
        print(f"    {table}: {cur.rowcount:,} rows reassigned")

    return len(moves)


# ================================================================== #
#  REJOIN -- A SPLIT THE RULES NO LONGER MAKE IS PUT BACK
# ================================================================== #
#
# ★ WHY (owner, 2026-10-12: "we cannot just go manually on these people we
#   need to find what is wrong and fix it automatically"). A split is made
#   once, by hand, and then stands forever; when the rule that made it is
#   corrected -- an unattached row's grade is no class year any more -- the
#   people it wrongly took apart stay apart. Every split in person_split is
#   re-judged here with the CURRENT rules on the two ids' seasons together,
#   and one the rules would not make now is undone.
#
# ! TWO GUARDS, BECAUSE A WRONG MERGE IS WORSE THAN A MISSED ONE (this
#   module's own rule): the joined seasons must cluster as one person
#   (findSplits returns nothing -- the gap, the class years, and a thin
#   cluster's school and name all agree), AND the two sides' names must
#   share a spelling (not _disjoint on name), whatever their sizes.
# ! IDEMPOTENT, so it runs every night (pipeline 04a4): a rejoined split
#   leaves person_split, and a second run finds nothing. Only the seasons
#   the split moved go back; rows another linker gave the new id stay.
#   Logged in person_link_log, rule 'unsplit'; --rejoin --undo restores.
REJOIN_RULE = "unsplit"


def rejoinPairs(by_person, pairs):
    """[(old, new, seasons)] of the splits the current rules would not make.

    by_person: loadSeasons over both sides' ids; pairs: [(old, new, seasons)]
    from person_split. Pure."""
    out = []
    for old, new, seasons in pairs:
        a, b = by_person.get(old), by_person.get(new)
        if not a or not b:
            continue                   # one side is gone: nothing to join
        if _disjoint(a, b, 5):
            continue                   # no shared name spelling
        if findSplits({old: a + b}):
            continue                   # still two people by today's rules
        out.append((old, new, sorted(seasons)))
    return out


def rejoin(cur, do_write=False, show=25):
    """Re-judge every split in person_split; undo the ones that no longer
    hold (see REJOIN_RULE). Returns the number of ids put back."""
    cur.execute("SELECT to_regclass('person_split')")
    if cur.fetchone()[0] is None:
        print("[unlink/rejoin] no person_split: nothing was ever split")
        return 0
    cur.execute("""SELECT old_person_id, new_person_id, array_agg(season)
                   FROM person_split GROUP BY 1, 2""")
    pairs = [(int(o), int(n), [int(x) for x in ss]) for o, n, ss in cur.fetchall()]
    print(f"[unlink/rejoin] {len(pairs):,} splits on record")
    if not pairs:
        return 0
    cur.execute("DROP TABLE IF EXISTS rj_ids; CREATE TEMP TABLE rj_ids (person_id bigint PRIMARY KEY)")
    from psycopg2.extras import execute_values
    ids = sorted({p for o, n, _ in pairs for p in (o, n)})
    execute_values(cur, "INSERT INTO rj_ids VALUES %s", [(i,) for i in ids], page_size=10000)
    cur.execute("ANALYZE rj_ids")
    back = rejoinPairs(loadSeasons(cur, only="rj_ids"), pairs)
    print(f"[unlink/rejoin] {len(back):,} splits the current rules would not make")
    for old, new, ss in back[:show]:
        print(f"    {new} -> {old}  seasons {', '.join(map(str, ss))}")
    if not do_write or not back:
        if back:
            print("[unlink/rejoin] DRY RUN, pass --write to apply")
        return len(back)
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    from link_freshmen import LOG_DDL
    cur.execute(LOG_DDL)
    cur.execute("DROP TABLE IF EXISTS rj_back; CREATE TEMP TABLE rj_back "
                "(old_id bigint, new_id bigint, season int)")
    execute_values(cur, "INSERT INTO rj_back VALUES %s",
                   [(o, n, y) for o, n, ss in back for y in ss], page_size=10000)
    moved = {}
    for sport, table in (("XC", "results"), ("TF", "results_tf")):
        cur.execute(f"""
            INSERT INTO person_link_log (sport, result_id, from_person, to_person, rule)
            SELECT %s, r.result_id, b.new_id, b.old_id, %s
            FROM   {table} r JOIN rj_back b
                   ON r.person_id = b.new_id AND substr(r.date, 1, 4)::int = b.season
            ON CONFLICT DO NOTHING""", (sport, REJOIN_RULE))
        cur.execute(f"""
            UPDATE {table} r SET person_id = b.old_id
            FROM   rj_back b
            WHERE  r.person_id = b.new_id AND substr(r.date, 1, 4)::int = b.season""")
        moved[sport] = cur.rowcount
    cur.execute("""DELETE FROM person_split s USING rj_back b
                   WHERE s.old_person_id = b.old_id AND s.season = b.season
                     AND s.new_person_id = b.new_id""")
    print(f"[unlink/rejoin] {len(back):,} ids put back; rows moved {moved}; "
          f"logged in person_link_log rule '{REJOIN_RULE}'")
    return len(back)


def rejoinUndo(cur):
    """Put every rejoined row back on its split id, and the split on record."""
    cur.execute("SELECT to_regclass('person_link_log')")
    if cur.fetchone()[0] is None:
        print("[unlink/rejoin] nothing to undo")
        return
    cur.execute("""
        INSERT INTO person_split (old_person_id, season, new_person_id)
        SELECT DISTINCT l.to_person, substr(r.date, 1, 4)::int, l.from_person
        FROM   person_link_log l
        JOIN   (SELECT 'XC' AS sport, result_id, date FROM results
                UNION ALL SELECT 'TF', result_id, date FROM results_tf) r
               ON r.sport = l.sport AND r.result_id = l.result_id
        WHERE  l.rule = %s
        ON CONFLICT DO NOTHING""", (REJOIN_RULE,))
    back = {}
    for sport, table in (("XC", "results"), ("TF", "results_tf")):
        cur.execute(f"""
            UPDATE {table} r SET person_id = l.from_person
            FROM   person_link_log l
            WHERE  l.sport = %s AND l.rule = %s
              AND  r.result_id = l.result_id AND r.person_id = l.to_person""",
                    (sport, REJOIN_RULE))
        back[sport] = cur.rowcount
    cur.execute("DELETE FROM person_link_log WHERE rule = %s", (REJOIN_RULE,))
    print(f"[unlink/rejoin] undone: {back}")


def main(do_write=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            print("[unlink] loading per-season class years...")
            by_person = loadSeasons(cur)
            print(f"    {len(by_person):,} people with at least one season")

            splits = findSplits(by_person)
            report(splits)

            if do_write:
                n = write(cur, splits)
                conn.commit()
                print(f"\n[unlink] wrote person_split ({n:,} season moves)")
                print("[unlink] re-normalize and re-solve: every rating built "
                      "on a merged id is a blend of two athletes")
            else:
                print("[unlink] DRY RUN, pass --write to apply")


if __name__ == "__main__":
    if "--rejoin" in sys.argv:
        from database import getConn
        with getConn() as _conn:
            with _conn.cursor() as _cur:
                if "--undo" in sys.argv:
                    rejoinUndo(_cur)
                else:
                    rejoin(_cur, do_write="--write" in sys.argv)
            _conn.commit()
    else:
        main(do_write="--write" in sys.argv)