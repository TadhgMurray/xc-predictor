"""
college_flag.py -- each athlete's first COLLEGIATE season.

WHY THIS IS BROADER THAN THE NCAA RULE
    pro_flag's NCAA veto keys on meet NAMES containing 'ncaa'. That misses every
    collegiate race at a conference meet, a regional, a dual, or an ordinary
    invitational -- which is most of them. An athlete can run four years of NCAA
    competition without ever appearing at a meet with 'NCAA' in its name.

★ THE LEVEL COMES FROM poolFor, NOT FROM A NEW RULE. poolFor already resolves a
  school string to a level using school_levels.pkl -- that IS the "is this an
  actual university" test, and it is the single source of truth the engine pools
  on. Re-deriving it here would create exactly the drift the codebase warns
  about (14: MIRRORS vs IMPORTS).

★ AND THIS DIRECTION IS GENUINELY ABSORBING. Once an athlete has raced for a
  college, they cannot afterwards be a high schooler or a middle schooler. That
  is unlike "professional", which Sadie Engelhardt disproved by going pro and
  then to college.

HOW IT STAYS CHEAP
    poolFor's inputs are LOW CARDINALITY -- (grade, source, school). So the
    distinct combinations are pulled out first, classified in Python, pushed
    back as a temp table, and the per-person minimum is computed in SQL. One
    scan, no 61M-row Python loop.

    Gender is passed as 'M' throughout: it only decides the SUFFIX (college_m vs
    college_f), never the LEVEL, and the level is all this needs.

★ AND A SCHOOL GRADE VETOES THE WHOLE THING. A row whose grade normalises
  to 1-12 is not a collegiate race, whatever its school string says. See
  schoolGradeVeto below -- this is the guard that stops a state or national
  team name from promoting a middle schooler.

Writes college_first_season (person_id, first_season). Changes no pool itself.
"""

import sys
sys.path.insert(0, 'scripts')

def loadSchools(cur):
    """
    Distinct SCHOOL strings across both results tables.

    ★ SCHOOL ONLY -- NOT (grade, source, school). Three reasons, and they all
      point the same way:

      1. CORRECTNESS. The rule is "the school is an actual university". Keying
         on grade lets 'jr'/'sr' -- used by high school AND college alike --
         decide the level. Keying on source lets poolFor's tfrrs fallback fire
         on a MISSING grade. Measured: 1,657,649 of 1,842,348 collegiate
         signatures were collegiate only via the source fallback.

      2. SPEED. 2.46M distinct triples collapse to far fewer distinct schools,
         so the Python classification loop shrinks by roughly that factor.

      3. THE JOIN. One indexed text column hashes far better than three.
    """
    cur.execute("""
        SELECT school FROM results     GROUP BY school
        UNION
        SELECT school FROM results_tf  GROUP BY school
    """)
    return [r[0] for r in cur.fetchall()]


def gradeSets(cur):
    """
    Distinct raw grade strings, split into COLLEGE CLASSES and HS UPPERCLASS,
    using the engine's own normalizeGrade -> GRADE_TO_LEVEL.

    ★ THE OWNER-CONFIRMED DOMAIN FACT, from normalize_distance: high school
      grades are NUMBERS (9-12), college classes are NAMES (Fr/So/Jr/Sr/RS).
      There is NO overlap. An earlier version here matched 'jr'/'sr' as high
      school upperclassmen -- exactly backwards, since those mean college.

    ★ AND IT USES THEIR NORMALIZER, NOT A REGEX. normalizeGrade already handles
      'Freshman', 'SENIOR', 'Fr.', 'Soph', the truncated '6t'/'7t'/'8t' ordinals
      and tfrrs's 'FR-1'/'SO-2' class+eligibility form -- ~17.7M rows' worth of
      spellings a pattern like '^(11|12)$' silently misses. Deliberately NOT
      mapped there: '-', '--', '?', 'NA', '0', '13', years, '19+'. Those stay
      unmatched here too, which is correct.

    Output: (college_class_grades, hs_upper_grades) -- both lists of RAW strings
    as they appear in the data, so the join needs no function call on the
    results side.
    """
    from normalize_distance import normalizeGrade, GRADE_TO_LEVEL

    cur.execute("""
        SELECT grade FROM results     GROUP BY grade
        UNION
        SELECT grade FROM results_tf  GROUP BY grade
    """)
    coll, upper = [], []
    for (raw,) in cur.fetchall():
        if raw is None:
            continue
        key = normalizeGrade(raw)
        level = GRADE_TO_LEVEL.get(key)
        if level == "college":
            coll.append(raw)          # measured only; NOT used by the gate
        elif key in ("11", "12"):
            upper.append(raw)
    return coll, upper


# ★ NOT SCHOOLS AT ALL. A national team is not a college, and neither is an
#   unattached entry -- but school_levels.pkl carries "United States" as a
#   college, and variants of "Unattached" as hs/ms.
#
#   Measured cost: Jackson Spencer ran World XC for the USA on 2026-01-10 as a
#   high school senior. That single row put school = "United States", the gate
#   read it as collegiate, and his whole senior season moved to college_m. His
#   ability improved 902 -> 876 while his rating FELL 139.8 -> 113.4, because
#   876 was suddenly measured against college_m's pool_mean.
_NOT_SCHOOLS = (
    r"^(unattached|una|unat|individual|n/?a|none|\?+|0)\b"
    r"|unattached"
    r"|^(united states|usa|u\.?s\.?a\.?|great britain|canada|japan|kenya"
    r"|ethiopia|jamaica|australia|new zealand|ireland|mexico|puerto rico"
    r"|south africa|nigeria|germany|france|italy|spain|poland|norway|sweden"
    r"|netherlands|belgium|portugal|brazil|china|india|uganda|eritrea"
    r"|morocco|algeria|bahrain|qatar|turkey|israel|philippines|colombia)$"
)


def collegeSchools(schools):
    """
    The schools poolFor resolves to a collegiate level ON THEIR OWN.

    Grade is passed as None and source as a neutral value, so neither can carry
    the decision -- only the school string, resolved through school_levels.pkl,
    which is the engine's own definition of "is this a university".
    """
    from normalize_distance import poolFor

    import re

    out, rejected = [], 0
    pat = re.compile(_NOT_SCHOOLS, re.I)
    for school in schools:
        if not school:
            continue
        if pat.search(school.strip()):
            rejected += 1
            continue
        pool = poolFor(None, "M", "anet", school)
        if pool and pool.startswith("college"):
            out.append(school)
    if rejected:
        print(f"[college] {rejected:,} strings rejected as national teams or "
              f"unattached entries (not schools)")
    return out


def schoolGradeVeto(cur):
    """Raw grade spellings that mean a SCHOOL grade, 1-12.

    ★ GRADE AS A VETO, NOT AS A SIGNAL, AND THE DIFFERENCE IS EVERYTHING.
      buildFlags already records that adding "grade is a college class" as a
      POSITIVE signal was tried and reverted: it took hs_m promotions from
      18,192 to 380,542 because some feeds write Fr/So/Jr/Sr for high school
      freshmen, so a word grade cannot prove a collegiate race.

      The converse does not have that problem. Nothing writes '8' for a
      college sophomore. A numeric school grade is unambiguous, and its own
      comment already draws the conclusion: "A ninth grader has not raced
      collegiately." This turns that sentence into code.

      Measured cost of not having it: person 27072849, a grade 8 girl from
      The Willow School, ran RunningLane on 2026-05-22 under school
      "Louisiana" -- a STATE team name that resolves to a university. Two
      rows, exactly min_races, so college_first_season took 2026-05-22 and
      resolvePool's stage 2 promoted every race from that date on. Her two
      RunningLane results rated 114.7 and 110.8 against 144.7 a fortnight
      earlier: same times, same normalisation, college_f's pool mean.

    ⚠ WHY NOT JUST BLOCK STATE NAMES IN _NOT_SCHOOLS. Because bare 'Indiana',
      'Michigan' and 'Louisiana' are REAL universities in this corpus. The
      string cannot be judged on its own; the grade beside it can.

    Uses the engine's own normaliser, so every spelling it knows -- '09',
    '8th', '6t' -- is covered without a second pattern to drift.
    """
    from normalize_distance import normalizeGrade, GRADE_TO_LEVEL

    cur.execute("""
        SELECT grade FROM results     GROUP BY grade
        UNION
        SELECT grade FROM results_tf  GROUP BY grade
    """)
    out = []
    for (raw,) in cur.fetchall():
        if raw is None:
            continue
        key = normalizeGrade(raw)
        # A numeric key the engine maps to a school level. Class words map to
        # 'college' and are deliberately NOT here -- they are the ambiguous
        # ones, and vetoing on them would delete real collegiate rows.
        if key and key.isdigit() and GRADE_TO_LEVEL.get(key) in (
                "elem", "ms", "hs"):
            out.append(raw)
    return out


def buildFlags(cur, colleges, coll_grades, upper_grades, veto_grades=()):
    """
    Both gates, ONE SCAN PER TABLE.

    Previously college and upperclass were separate passes, so four scans of
    230M rows. A single LEFT JOIN with two CASE columns gets both in one, and
    the WHERE keeps only rows that feed at least one gate.

    ★ DATES, NOT YEARS. A senior's spring high-school track and their first
      autumn of college share a calendar year, and the engine treats the
      calendar year as the season. A year gate promoted 288,264 genuine
      high-school athlete-seasons; comparing dates puts spring before the
      boundary and autumn after.
    """
    cur.execute("DROP TABLE IF EXISTS tmp_college_school")
    cur.execute("CREATE TEMP TABLE tmp_college_school (school text PRIMARY KEY)")
    rows = [(c,) for c in colleges]
    try:
        from psycopg2.extras import execute_values
        execute_values(cur, "INSERT INTO tmp_college_school VALUES %s", rows,
                       page_size=5000)
    except Exception:
        cur.executemany("INSERT INTO tmp_college_school VALUES (%s)", rows)
    cur.execute("ANALYZE tmp_college_school")

    # Grade sets as tables too, so the scan is three hash lookups and no
    # per-row regex or lower().
    for name, vals in (("tmp_coll_grade", coll_grades),
                       ("tmp_upper_grade", upper_grades),
                       ("tmp_veto_grade", veto_grades)):
        cur.execute(f"DROP TABLE IF EXISTS {name}")
        cur.execute(f"CREATE TEMP TABLE {name} (g text PRIMARY KEY)")
        if vals:
            try:
                from psycopg2.extras import execute_values
                execute_values(cur, f"INSERT INTO {name} VALUES %s",
                               [(v,) for v in vals], page_size=5000)
            except Exception:
                cur.executemany(f"INSERT INTO {name} VALUES (%s)",
                                [(v,) for v in vals])
        cur.execute(f"ANALYZE {name}")

    cur.execute("DROP TABLE IF EXISTS tmp_flags")
    cur.execute("""CREATE TEMP TABLE tmp_flags
                   (person_id bigint, coll date, upper date)""")

    for table in ("results", "results_tf"):
        cur.execute(f"""
            INSERT INTO tmp_flags (person_id, coll, upper)
            SELECT r.person_id,
                   -- ⚠ SCHOOL ONLY. Adding "grade is a college class" was
                   --   tried and REVERTED. It took hs_m promotions from 18,192
                   --   to 380,542, and a random sample of the extra rows was
                   --   high schoolers without exception -- including two GRADE
                   --   9 athletes whose "first collegiate date" was the race
                   --   date itself. A ninth grader has not raced collegiately.
                   --
                   --   normalize_distance records the domain fact as
                   --   "high school grades are NUMBERS, college classes are
                   --   NAMES, there is no overlap". That holds for the sources
                   --   it was confirmed against, but NOT across the whole
                   --   corpus: some feeds write Fr/So/Jr/Sr for HIGH SCHOOL
                   --   freshmen and sophomores, which is ordinary usage. One
                   --   such row then marks a real high schooler collegiate for
                   --   the rest of their career.
                   --
                   --   The school string carries no such ambiguity: a
                   --   university name is a university name.
                   -- ★ THE VETO. The school still nominates the row, but a
                   --   grade of 1-12 beside it withdraws the nomination. Only
                   --   the COLLEGE column is gated: the upperclass column
                   --   below is ABOUT school grades, so vetoing on them there
                   --   would empty it.
                   CASE WHEN s.school IS NOT NULL AND vg.g IS NULL
                        THEN r.date::date END,
                   CASE WHEN ug.g IS NOT NULL THEN r.date::date END
            FROM {table} r
            LEFT JOIN tmp_college_school s  ON s.school = r.school
            LEFT JOIN tmp_upper_grade    ug ON ug.g     = r.grade
            LEFT JOIN tmp_veto_grade     vg ON vg.g     = r.grade
            WHERE r.person_id IS NOT NULL
              AND r.date ~ '^(19|20)[0-9]{{2}}'
              AND ((s.school IS NOT NULL AND vg.g IS NULL)
                   OR ug.g IS NOT NULL)
        """)
        print(f"    {table}: {cur.rowcount:,} flag rows")

    cur.execute("SELECT to_regclass('ncaa_first_season')")
    if cur.fetchone()[0] is not None:
        # no date on that table; a collegiate season starts 1 August.
        cur.execute("""INSERT INTO tmp_flags (person_id, coll, upper)
                       SELECT person_id, make_date(first_season, 8, 1), NULL
                       FROM ncaa_first_season""")
        print(f"    unioned {cur.rowcount:,} rows from ncaa_first_season")

    # ★ MINIMUM RACES, mirroring pro_flag. pro_flag requires 3 qualifying races
    #   before flagging an athlete-season; college_flag required ONE, and that
    #   asymmetry is what let a single World XC start rewrite a senior year.
    #   Two is enough to exclude a one-off national-team or guest entry while
    #   still catching a genuine collegian by their second race.
    made = {}
    for name, col, min_races in (("college_first_season", "coll", 2),
                                 ("upperclass_first_season", "upper", 1)):
        cur.execute(f"DROP TABLE IF EXISTS {name}")
        cur.execute(f"""CREATE TABLE {name} (
                            person_id    bigint NOT NULL PRIMARY KEY,
                            first_season int    NOT NULL,
                            first_date   date   NOT NULL)""")
        cur.execute(f"""INSERT INTO {name}
                        SELECT person_id,
                               EXTRACT(year FROM min({col}))::int,
                               min({col})
                        FROM tmp_flags WHERE {col} IS NOT NULL
                        GROUP BY person_id
                        HAVING count(*) >= {min_races}""")
        made[name] = cur.rowcount
    return made


def report(cur):
    """Sanity: how many school-pool seasons this would promote."""
    cur.execute("SELECT to_regclass('pair_athlete_season')")
    if cur.fetchone()[0] is None:
        return
    for label, table, target, pools in (
            ("college_*", "college_first_season", "college",
             "s.pool LIKE 'hs%' OR s.pool LIKE 'ms%' OR s.pool LIKE 'elem%'"),
            ("hs_*", "upperclass_first_season", "hs",
             "s.pool LIKE 'ms%' OR s.pool LIKE 'elem%'")):
        cur.execute(f"""
            SELECT s.pool, count(*) AS seasons,
                   round(max(s.rating_seasonal)::numeric, 1) AS top_rating
            FROM pair_athlete_season s
            JOIN {table} c ON c.person_id::text = s.person_id
            WHERE s.season >= c.first_season AND ({pools})
            GROUP BY 1 ORDER BY seasons DESC
        """)
        rows = cur.fetchall()
        print(f"\n[college] seasons that would become {label}:")
        if not rows:
            print("    none")
            continue
        for pool, seasons, top in rows:
            print(f"    {pool:<12} {seasons:>8,}   top rating {top}")


def main(write=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            schools = loadSchools(cur)
            print(f"[college] {len(schools):,} distinct school strings")
            coll = collegeSchools(schools)
            print(f"[college] {len(coll):,} resolve to a university "
                  f"(school alone -- grade and source cannot decide)")
            if not coll:
                print("[college] none -- check school_levels.pkl is loaded")
                return

            coll_grades, upper_grades = gradeSets(cur)
            veto_grades = schoolGradeVeto(cur)
            print(f"[college] {len(veto_grades):,} grade spellings mean a "
                  f"school grade 1-12 and VETO a collegiate row")
            print(f"[college] grades: {len(upper_grades):,} spellings mean "
                  f"grade 11/12 (upperclass gate)")
            print(f"[college]         {len(coll_grades):,} spellings look like a "
                  f"college class -- NOT used, see the note in buildFlags")

            made = buildFlags(cur, coll, coll_grades, upper_grades,
                              veto_grades)
            for name, n in made.items():
                print(f"[college] {name}: {n:,} athletes")
            report(cur)

            if write:
                conn.commit()
                print("[college] committed")
            else:
                conn.rollback()
                print("[college] DRY RUN -- pass --write to keep the table")


if __name__ == "__main__":
    main(write="--write" in sys.argv)