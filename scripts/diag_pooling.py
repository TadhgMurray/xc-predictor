#!/usr/bin/env python3
"""
diag_pooling.py -- who is in the wrong pool, by kind, ranked by how many
ratings each kind moves. READ-ONLY (session temp tables only).

    python scripts/diag_pooling.py                    # 1 person in 10, ~fast
    python scripts/diag_pooling.py --sample 1         # everybody (slow)
    python scripts/diag_pooling.py --only flip,ms_in_hs --show 20
    python scripts/diag_pooling.py --fill-check 0     # skip the Python check
    python scripts/diag_pooling.py --meet 270331      # one meet, by division

★ WHY (pooling review, 2026-10-25; docs/REVIEW-POOLING-2026-10.md).
  speed_rating is relative to its pool's mean, and the pool also picks the
  distance curve, so a wrong pool bends a rating differently at every
  distance. Each section below counts one way to be in the wrong pool:

    rows      rated rows (results + results_tf) that look wrong
    ranked    of those, how many sit on a board (ranking_results)
    athletes  distinct people

  Counts under --sample N are the sample times N, so they are estimates; the
  examples are real rows. Nothing is written except session temp tables.

  The stamped pool is results.rating_pool -- the pool the engine (or the
  fill, for a row the solve did not rate) actually rated the row in.

SECTIONS (--only takes these keys)
  flip          one athlete-season (person, sport, academic year) rated in
                more than one level -- a season split across pools
  xflip         the same academic year at different levels in XC and TF
  ms_in_hs      a middle-school grade (6-8, grade_sanity's when it has one)
                rated in a high school pool
  hs_in_ms      a high-school grade (9-12) rated in ms or elem
  hs_in_college a high-school grade rated college BEFORE the person's first
                college season (the college veto should stop this)
  college_in_hs a college team's row (tfrrs slug or team_pool 'college'), or
                a season at/after the person's first college season, rated hs
  pro_in_school pro_athlete_season says professional, rated hs/ms/college
  pro_pool_slow rated pro but not in pro_ability_season (not sub-14:00 /
                15:30): what the ability gate should have removed
  postcollege   rated college 5+ seasons after the first college season
  gender        pool sex differs from person_gender's; pools of unknown sex
  grade_typo    athlete-seasons whose own rows disagree on the grade, and
                grades that go backwards year over year
  unattached    unattached rows (team_id 0 or an Unattached school) not
                rated at their race's ceiling (race_top_level)
  club          rows on a team_pool 'pro' / 'club' team rated in a school pool
  race_level    ms rows in races level_graph calls hs, and hs rows in ms races
  merge         one person in two levels two+ apart in one season, or a level
                that goes back down (college -> hs) a later season
  stale         fact tables keyed on person_ids that no longer have a row
                (built before a relink/merge), and when each table was built
  fill          (Python) fill_ratings' pool decision vs the stamped pool on
                engine-rated rows: the pool a nightly-priced row would get
"""
import argparse
import os
import sys
import time
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from season_year import seasonYearSqlInt                    # noqa: E402

_LEVEL_RANK_SQL = ("CASE {c} WHEN 'elem' THEN 0 WHEN 'ms' THEN 1 WHEN 'hs' "
                   "THEN 2 WHEN 'college' THEN 3 WHEN 'pro' THEN 4 END")
_SCHOOL_GRADE = r"^(0?[1-9]|1[0-2])$"
# grade -> level, the same split as normalize_distance.GRADE_TO_LEVEL
_GRADE_LEVEL_SQL = ("CASE WHEN {g} ~ '^0?[1-5]$' THEN 'elem' "
                    "WHEN {g} ~ '^0?[6-8]$' THEN 'ms' "
                    "WHEN {g} ~ '^(9|09|1[0-2])$' THEN 'hs' END")


def _raceKey(alias):
    from level_graph import _raceKeyExpr
    return _raceKeyExpr(alias)


class Diag:
    def __init__(self, conn, sample, show):
        self.conn = conn
        self.sample = max(1, int(sample))
        self.show = show
        self.found = []                    # (key, title, rows, ranked, athletes)

    # -- plumbing ------------------------------------------------------ #
    def q(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def exists(self, name):
        return self.q("SELECT to_regclass(%s)", (f"public.{name}",))[0][0] is not None

    def cols(self, table):
        return {r[0] for r in self.q(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s", (table,))}

    def scale(self, n):
        return int(n or 0) * self.sample

    def record(self, key, title, flagged_sql, note=""):
        """flagged_sql selects (sport, result_id, person_id) from dp_rows.
        Counts it, with how many of those rows are ranked."""
        t0 = time.time()
        self.q("DROP TABLE IF EXISTS dp_flag")
        self.q(f"CREATE TEMP TABLE dp_flag AS {flagged_sql}")
        rows, people = self.q(
            "SELECT count(*), count(DISTINCT person_id) FROM dp_flag")[0]
        ranked = 0
        if rows and self.have_rr:
            ranked = self.q("""
                SELECT count(*) FROM dp_flag f
                WHERE EXISTS (SELECT 1 FROM ranking_results k
                              WHERE k.result_id = f.result_id
                                AND k.sport = f.sport)""")[0][0]
        self.found.append((key, title, self.scale(rows), self.scale(ranked),
                           self.scale(people)))
        print(f"\n=== {key}: {title}")
        if note:
            print(f"    {note}")
        print(f"    rows {self.scale(rows):>12,}   ranked {self.scale(ranked):>12,}"
              f"   athletes {self.scale(people):>10,}   ({time.time() - t0:.0f}s)")
        return rows

    def examples(self, sql, header):
        if not self.show:
            return
        got = self.q(sql + f" LIMIT {int(self.show)}")
        if got:
            print("    " + header)
            for r in got:
                print("      " + "  ".join("-" if v is None else str(v) for v in r))

    def crosstab(self, sql, header, limit=20):
        got = self.q(sql)
        if got:
            print("    " + header)
            for r in got[:limit]:
                *keys, n = r
                print(f"      {self.scale(n):>12,}  " + "  ".join(str(k) for k in keys))

    # -- the base: every rated row, sampled by person ------------------ #
    def build(self, since):
        self.have_rr = self.exists("ranking_results")
        parts = []
        for table, sport in (("results", "XC"), ("results_tf", "TF")):
            c = self.cols(table)
            if "rating_pool" not in c:
                print(f"[diag] {table} has no rating_pool -- skipped")
                continue
            team = "r.team_id" if "team_id" in c else "NULL::bigint"
            slug = "r.team_slug" if "team_slug" in c else "NULL::text"
            name = "r.athlete_name" if "athlete_name" in c else "NULL::text"
            ev = "r.event_id" if (sport == "TF" and "event_id" in c) else "NULL::bigint"
            parts.append(f"""
                SELECT '{sport}'::text AS sport, r.result_id, r.person_id,
                       {seasonYearSqlInt(None, 'r.date')} AS ay, r.date,
                       NULLIF(btrim(r.grade), '') AS grade, r.source, r.school,
                       {team} AS team_id, {slug} AS team_slug, {name} AS name,
                       r.meet_id, r.div_id, {ev} AS event_id,
                       split_part(r.rating_pool, '|', 1) AS pool,
                       r.speed_rating AS rating, {_raceKey('r')} AS race
                FROM   {table} r
                WHERE  r.rating_pool IS NOT NULL AND r.speed_rating IS NOT NULL
                  AND  r.person_id IS NOT NULL
                  AND  r.date ~ '^(19|20)[0-9][0-9]-[0-9][0-9]'
                  AND  r.date >= %(since)s
                  AND  mod(r.person_id, %(n)s) = 0""")
        if not parts:
            raise SystemExit("[diag] no rated rows to read")
        t0 = time.time()
        self.q("DROP TABLE IF EXISTS dp_rows")
        with self.conn.cursor() as cur:
            cur.execute(f"""
                CREATE TEMP TABLE dp_rows AS
                SELECT u.*,
                       substring(u.pool from '^(elem|ms|hs|college|pro)_') AS lvl,
                       CASE WHEN u.pool LIKE '%%\\_m' THEN 'M'
                            WHEN u.pool LIKE '%%\\_f' THEN 'F' ELSE '?' END AS sex
                FROM ({" UNION ALL ".join(parts)}) u""",
                        {"since": since, "n": self.sample})
        self.q("CREATE INDEX ON dp_rows (person_id, ay)")
        self.q("ANALYZE dp_rows")
        n, p = self.q("SELECT count(*), count(DISTINCT person_id) FROM dp_rows")[0]
        print(f"[diag] base: {self.scale(n):,} rated rows, {self.scale(p):,} athletes "
              f"(sample 1 in {self.sample}, since {since}; {time.time() - t0:.0f}s)")
        self.crosstab("SELECT sport, pool, count(*) FROM dp_rows GROUP BY 1, 2 "
                      "ORDER BY 3 DESC", "rows per pool:", limit=40)
        # grade_sanity's grade for the season where it has one: the grade the
        # pool decision actually used
        self.have_gf = self.exists("grade_fix")
        gf = ("LEFT JOIN grade_fix gf ON gf.person_id = r.person_id AND gf.season = r.ay"
              if self.have_gf else "")
        gcol = "COALESCE(gf.grade, r.grade)" if self.have_gf else "r.grade"
        self.q("DROP TABLE IF EXISTS dp_grade")
        self.q(f"""CREATE TEMP TABLE dp_grade AS
                   SELECT r.sport, r.result_id, r.person_id, r.ay, r.lvl,
                          {gcol} AS g,
                          {_GRADE_LEVEL_SQL.format(g=gcol)} AS glvl,
                          {"gf.method" if self.have_gf else "NULL::text"} AS method
                   FROM dp_rows r {gf}""")
        self.q("ANALYZE dp_grade")
        self.have_cfs = self.exists("college_first_season")
        if self.have_cfs:
            self.q("DROP TABLE IF EXISTS dp_cfs")
            self.q(f"""CREATE TEMP TABLE dp_cfs AS
                       SELECT person_id,
                              {seasonYearSqlInt(None, "first_date::text")} AS first_ay
                       FROM college_first_season
                       WHERE person_id IN (SELECT DISTINCT person_id FROM dp_rows)""")
            self.q("CREATE INDEX ON dp_cfs (person_id)")

    # -- the sections -------------------------------------------------- #
    def flip(self):
        self.q("DROP TABLE IF EXISTS dp_season")
        self.q("""CREATE TEMP TABLE dp_season AS
                  SELECT person_id, sport, ay, lvl, count(*) AS n
                  FROM dp_rows WHERE lvl IS NOT NULL GROUP BY 1, 2, 3, 4""")
        self.q("""CREATE TEMP TABLE IF NOT EXISTS dp_major AS SELECT 1""")
        self.q("DROP TABLE dp_major")
        self.q("""CREATE TEMP TABLE dp_major AS
                  SELECT DISTINCT ON (person_id, sport, ay)
                         person_id, sport, ay, lvl AS major, n
                  FROM dp_season
                  WHERE (person_id, sport, ay) IN (
                        SELECT person_id, sport, ay FROM dp_season
                        GROUP BY 1, 2, 3 HAVING count(*) > 1)
                  ORDER BY person_id, sport, ay, n DESC, lvl""")
        self.record("flip", "one season rated in two or more levels (minority rows)", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN dp_major m USING (person_id, sport, ay)
            WHERE r.lvl IS DISTINCT FROM m.major""",
            "the rows outside the season's majority level; each is rated "
            "against a different pool mean than the rest of the season")
        self.crosstab("""
            SELECT m.major || ' -> ' || COALESCE(r.lvl, '?'), r.sport, count(*)
            FROM dp_rows r JOIN dp_major m USING (person_id, sport, ay)
            WHERE r.lvl IS DISTINCT FROM m.major GROUP BY 1, 2 ORDER BY 3 DESC""",
            "majority -> minority level:")
        self.examples("""
            SELECT r.person_id, r.name, r.sport, r.ay, m.major, r.lvl, r.date,
                   r.grade, r.school, r.team_id, round(r.rating::numeric, 1)
            FROM dp_rows r JOIN dp_major m USING (person_id, sport, ay)
            WHERE r.lvl IS DISTINCT FROM m.major ORDER BY r.rating DESC""",
            "person  name  sport  ay  season-level  row-level  date  grade  school  team  rating")

    def xflip(self):
        self.record("xflip", "XC and TF of one academic year at different levels", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN (SELECT person_id, ay FROM (
                     SELECT person_id, ay, sport,
                            mode() WITHIN GROUP (ORDER BY lvl) AS l
                     FROM dp_rows WHERE lvl IS NOT NULL GROUP BY 1, 2, 3) s
                  GROUP BY 1, 2 HAVING count(DISTINCT l) > 1) x
              USING (person_id, ay)""",
            "both sports' rows counted; a senior's spring track and the "
            "following fall are different academic years, so this is not that")

    def ms_in_hs(self):
        self.record("ms_in_hs", "grade 6-8 rated in a high school pool", """
            SELECT sport, result_id, person_id FROM dp_grade
            WHERE lvl = 'hs' AND glvl IN ('ms', 'elem')""")
        self.crosstab("""SELECT COALESCE(method, '(no grade_fix)'), g, count(*)
                         FROM dp_grade WHERE lvl = 'hs' AND glvl IN ('ms', 'elem')
                         GROUP BY 1, 2 ORDER BY 3 DESC""",
                      "grade_sanity method, grade used:")

    def hs_in_ms(self):
        self.record("hs_in_ms", "grade 9-12 rated in a middle school / elementary pool", """
            SELECT sport, result_id, person_id FROM dp_grade
            WHERE lvl IN ('ms', 'elem') AND glvl = 'hs'""")
        self.crosstab("""SELECT COALESCE(method, '(no grade_fix)'), g, lvl, count(*)
                         FROM dp_grade WHERE lvl IN ('ms', 'elem') AND glvl = 'hs'
                         GROUP BY 1, 2, 3 ORDER BY 4 DESC""",
                      "grade_sanity method, grade used, pool level:")

    def hs_in_college(self):
        cfs = ("LEFT JOIN dp_cfs c ON c.person_id = g.person_id"
               if self.have_cfs else "")
        before = ("(c.first_ay IS NULL OR g.ay < c.first_ay)"
                  if self.have_cfs else "TRUE")
        self.record("hs_in_college", "grade 9-12 rated college before any college season", f"""
            SELECT g.sport, g.result_id, g.person_id FROM dp_grade g {cfs}
            WHERE g.lvl = 'college' AND g.glvl = 'hs' AND {before}""",
            "the college veto (seasonVerdictFor) exists to stop exactly this")
        self.examples(f"""
            SELECT r.person_id, r.name, r.sport, r.ay, g.g, g.method, r.school,
                   r.team_slug, round(r.rating::numeric, 1)
            FROM dp_grade g JOIN dp_rows r USING (sport, result_id) {cfs}
            WHERE g.lvl = 'college' AND g.glvl = 'hs' AND {before}
            ORDER BY r.rating DESC""",
            "person  name  sport  ay  grade  method  school  slug  rating")

    def college_in_hs(self):
        tp = "LEFT JOIN team_pool tp ON tp.team_id = r.team_id" if self.exists("team_pool") else ""
        tpc = "tp.kind = 'college'" if tp else "FALSE"
        cfs = "LEFT JOIN dp_cfs c ON c.person_id = r.person_id" if self.have_cfs else ""
        after = "(c.first_ay IS NOT NULL AND r.ay >= c.first_ay)" if self.have_cfs else "FALSE"
        flag = f"""(split_part(r.team_slug, '_', 2) = 'college' OR {tpc})"""
        self.record("college_in_hs", "a college team's row, or a season after college started, rated hs", f"""
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r {tp} {cfs}
            WHERE r.lvl = 'hs' AND ({flag} OR {after})""")
        self.crosstab(f"""
            SELECT CASE WHEN {flag} THEN 'college team' ELSE 'after first college season' END,
                   r.sport, count(*)
            FROM dp_rows r {tp} {cfs}
            WHERE r.lvl = 'hs' AND ({flag} OR {after}) GROUP BY 1, 2 ORDER BY 3 DESC""",
            "why:")
        self.examples(f"""
            SELECT r.person_id, r.name, r.sport, r.ay, r.grade, r.school,
                   r.team_slug, r.team_id, round(r.rating::numeric, 1)
            FROM dp_rows r {tp} {cfs}
            WHERE r.lvl = 'hs' AND ({flag} OR {after}) ORDER BY r.rating DESC""",
            "person  name  sport  ay  grade  school  slug  team  rating")

    def pro_in_school(self):
        if not self.exists("pro_athlete_season"):
            print("\n=== pro_in_school: no pro_athlete_season -- skipped")
            return
        self.record("pro_in_school", "pro_flag says professional, rated hs/ms/college", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN pro_athlete_season p ON p.person_id = r.person_id AND p.season = r.ay
            WHERE r.lvl IN ('elem', 'ms', 'hs', 'college')""",
            "some of these are the school veto and the ability gate working; "
            "the split below says which")
        able = ("EXISTS (SELECT 1 FROM pro_ability_season a WHERE a.person_id = r.person_id "
                "AND a.season = r.ay)" if self.exists("pro_ability_season") else "NULL")
        self.crosstab(f"""
            SELECT r.lvl, CASE WHEN {able} THEN 'sub-14/15:30' ELSE 'not fast enough' END,
                   count(*)
            FROM dp_rows r
            JOIN pro_athlete_season p ON p.person_id = r.person_id AND p.season = r.ay
            WHERE r.lvl IN ('elem', 'ms', 'hs', 'college') GROUP BY 1, 2 ORDER BY 3 DESC""",
            "level, ability:")

    def pro_pool_slow(self):
        if not self.exists("pro_ability_season"):
            print("\n=== pro_pool_slow: no pro_ability_season -- skipped")
            return
        self.record("pro_pool_slow", "rated pro, not sub-14:00 / 15:30 that season", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            WHERE r.lvl = 'pro' AND NOT EXISTS (
                SELECT 1 FROM pro_ability_season a
                WHERE a.person_id = r.person_id AND a.season = r.ay)""",
            "the hand list is exempt from the gate since 2026-10-25; the rest "
            "should be ~0 after a run with the gate on")
        self.crosstab("""
            SELECT CASE WHEN r.team_id = 0 THEN 'team_id 0'
                        WHEN lower(r.school) LIKE 'unattached%%' THEN 'unattached string'
                        ELSE 'has a team' END, r.sport, count(*)
            FROM dp_rows r WHERE r.lvl = 'pro' AND NOT EXISTS (
                SELECT 1 FROM pro_ability_season a
                WHERE a.person_id = r.person_id AND a.season = r.ay)
            GROUP BY 1, 2 ORDER BY 3 DESC""", "entry:")

    def postcollege(self):
        if not self.have_cfs:
            print("\n=== postcollege: no college_first_season -- skipped")
            return
        self.record("postcollege", "rated college 5+ seasons after the first college season", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN dp_cfs c ON c.person_id = r.person_id
            WHERE r.lvl = 'college' AND r.ay >= c.first_ay + 5""",
            "NCAA eligibility is 5 seasons in 6 years with a redshirt; these "
            "are mostly graduates racing open, club or unattached")
        self.crosstab("""
            SELECT r.ay - c.first_ay, count(*) FROM dp_rows r
            JOIN dp_cfs c ON c.person_id = r.person_id
            WHERE r.lvl = 'college' AND r.ay >= c.first_ay + 4
            GROUP BY 1 ORDER BY 1""", "seasons since first college season:")

    def gender(self):
        self.record("gender_unknown", "rated in a pool of unknown sex", """
            SELECT sport, result_id, person_id FROM dp_rows WHERE sex = '?'""")
        if not self.exists("person_gender"):
            print("\n=== gender: no person_gender -- skipped")
            return
        self.record("gender", "pool sex differs from the person's gender", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN person_gender g ON g.person_id = r.person_id
            WHERE g.gender IN ('M', 'F') AND r.sex IN ('M', 'F')
              AND r.sex <> g.gender
              AND NOT COALESCE(g.split, false)""",
            "people person_gender calls split (two real athletes, or a "
            "mixed-race label) are left out and counted below")
        self.crosstab("""
            SELECT 'split person, rows', count(*) FROM dp_rows r
            JOIN person_gender g ON g.person_id = r.person_id
            WHERE COALESCE(g.split, false)""", "")

    def grade_typo(self):
        self.record("grade_typo", "athlete-seasons whose own rows disagree on the grade", f"""
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN (SELECT person_id, ay FROM dp_rows
                  WHERE grade ~ '{_SCHOOL_GRADE}'
                  GROUP BY 1, 2 HAVING count(DISTINCT ltrim(grade, '0')) > 1) d
              USING (person_id, ay)""",
            "every row of such a season; grade_sanity's majority/field rules "
            "decide these, so the question is how many still land split (flip)")
        self.q("DROP TABLE IF EXISTS dp_sg")
        self.q(f"""CREATE TEMP TABLE dp_sg AS
                   SELECT person_id, ay,
                          mode() WITHIN GROUP (ORDER BY ltrim(grade, '0')::int) AS g
                   FROM dp_rows WHERE grade ~ '{_SCHOOL_GRADE}' GROUP BY 1, 2""")
        self.record("grade_back", "a school grade that goes DOWN the next season", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN (SELECT a.person_id, b.ay FROM dp_sg a
                  JOIN dp_sg b ON b.person_id = a.person_id AND b.ay = a.ay + 1
                  WHERE b.g < a.g) d USING (person_id, ay)""",
            "a typo, a stale grade, or two people merged into one")

    def unattached(self):
        if not self.exists("race_top_level"):
            print("\n=== unattached: no race_top_level -- skipped")
            return
        un = "(r.team_id = 0 OR lower(btrim(COALESCE(r.school, ''))) IN " \
             "('unattached', 'unattached runner', 'unat', 'independent', 'individual', 'none'))"
        self.record("unattached", "unattached row not rated at its race's ceiling", f"""
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN race_top_level t ON t.race = r.race
            WHERE {un} AND r.lvl IS DISTINCT FROM t.top_level""",
            "the owner's rule (2026-09-20); grade-screened ceilings are allowed "
            "to differ, so not every row here is wrong. tfrrs 'Unattached' rows "
            "ignored the ceiling until 2026-10-25")
        self.crosstab(f"""
            SELECT r.source, t.top_level || ' -> ' || COALESCE(r.lvl, '?'), count(*)
            FROM dp_rows r JOIN race_top_level t ON t.race = r.race
            WHERE {un} AND r.lvl IS DISTINCT FROM t.top_level
            GROUP BY 1, 2 ORDER BY 3 DESC""", "source, ceiling -> rated:")

    def club(self):
        if not self.exists("team_pool"):
            print("\n=== club: no team_pool -- skipped")
            return
        self.record("club", "row on a team_pool 'pro' team rated in a school or college pool", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN team_pool t ON t.team_id = r.team_id
            WHERE t.kind = 'pro' AND r.lvl IN ('elem', 'ms', 'hs', 'college')""",
            "team_pool pro is unconditional in resolvePool; only the ability "
            "gate (not fast enough -> trust grade) sends these back")
        self.crosstab("""
            SELECT t.kind, COALESCE(r.lvl, '?'), count(*) FROM dp_rows r
            JOIN team_pool t ON t.team_id = r.team_id
            WHERE t.kind IN ('pro', 'club') GROUP BY 1, 2 ORDER BY 3 DESC""",
            "team kind, rated level:")

    def race_level(self):
        if not self.exists("race_level"):
            print("\n=== race_level: no race_level -- skipped")
            return
        self.record("race_level", "ms row in an hs race, or hs row in an ms race", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN race_level l ON l.race = r.race
            WHERE (r.lvl = 'ms' AND l.level = 'hs') OR (r.lvl = 'hs' AND l.level = 'ms')""",
            "an eighth grader racing varsity is legitimate (they stay ms by "
            "their grade); an hs row in an ms race is the suspicious half")
        self.crosstab("""
            SELECT l.level || ' race, ' || r.lvl || ' row', count(*) FROM dp_rows r
            JOIN race_level l ON l.race = r.race
            WHERE (r.lvl = 'ms' AND l.level = 'hs') OR (r.lvl = 'hs' AND l.level = 'ms')
            GROUP BY 1 ORDER BY 2 DESC""", "")

    def merge(self):
        rank = _LEVEL_RANK_SQL.format(c="lvl")
        self.q("DROP TABLE IF EXISTS dp_pl")
        self.q(f"""CREATE TEMP TABLE dp_pl AS
                   SELECT person_id, ay, min({rank}) AS lo, max({rank}) AS hi
                   FROM dp_rows WHERE lvl IS NOT NULL GROUP BY 1, 2""")
        self.record("merge_wide", "one season spanning levels two or more apart (e.g. ms and college)", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN dp_pl p USING (person_id, ay) WHERE p.hi - p.lo >= 2""",
            "a seventh grader does not race college; two people under one id, "
            "or a club year-count read as a grade")
        self.examples("""
            SELECT r.person_id, min(r.name), string_agg(DISTINCT r.ay || ':' || r.lvl, ' '
                   ORDER BY r.ay || ':' || r.lvl)
            FROM dp_rows r JOIN dp_pl p USING (person_id, ay)
            WHERE p.hi - p.lo >= 2 GROUP BY r.person_id""",
            "person  name  seasons")
        self.record("merge_back", "a level that goes back DOWN in a later season (college -> hs)", """
            SELECT r.sport, r.result_id, r.person_id FROM dp_rows r
            JOIN (SELECT DISTINCT b.person_id, b.ay FROM dp_pl a
                  JOIN dp_pl b ON b.person_id = a.person_id AND b.ay > a.ay
                  WHERE b.hi < a.lo AND a.lo >= 2) d USING (person_id, ay)""",
            "the later, lower seasons' rows; pro -> anything is excluded only "
            "when the pro season is hs-or-lower too")

    def stale(self):
        print("\n=== stale: the pooling facts and when they were built")
        print("    ! run_pipeline.sh does not rebuild level_graph (race_level, "
              "race_top_level, school_level_graph), season_level, college_flag "
              "or build_team_pool -- only overnight_fit_pool_solve / owner_checks do")
        tables = ("grade_fix", "athlete_season_level", "pro_athlete_season",
                  "pro_ability_season", "college_first_season", "team_pool",
                  "race_level", "race_top_level", "school_level_graph",
                  "person_gender", "age_band_result")
        # every person_id with a result, sampled the same way: one scan of
        # each results table, however many fact tables are checked
        self.q("DROP TABLE IF EXISTS dp_pids")
        self.q("""CREATE TEMP TABLE dp_pids AS
                  SELECT person_id FROM results WHERE mod(person_id, %(n)s) = 0
                  UNION
                  SELECT person_id FROM results_tf WHERE mod(person_id, %(n)s) = 0""",
               {"n": self.sample})
        self.q("CREATE UNIQUE INDEX ON dp_pids (person_id) WHERE person_id IS NOT NULL")
        for t in tables:
            if not self.exists(t):
                print(f"      {t:<24} absent")
                continue
            st = self.q("""SELECT n_live_tup, greatest(last_analyze, last_autoanalyze),
                                  greatest(last_vacuum, last_autovacuum)
                           FROM pg_stat_user_tables WHERE relname = %s""", (t,))
            n, an, va = st[0] if st else (None, None, None)
            orphan = ""
            if "person_id" in self.cols(t):
                # a person_id with no row in either results table: the
                # table was built before a relink moved that person
                got = self.q(f"""
                    SELECT count(*) FROM (SELECT DISTINCT person_id FROM {t}
                                          WHERE mod(person_id, %(n)s) = 0) f
                    WHERE NOT EXISTS (SELECT 1 FROM dp_pids p WHERE p.person_id = f.person_id)""",
                    {"n": self.sample})[0][0]
                orphan = f"   person_ids with no result: {self.scale(got):,}"
            print(f"      {t:<24} rows {n or 0:>11,}   analyzed {str(an)[:16]:<16}{orphan}")
        if self.exists("team_pool"):
            print("      team_pool built: " + ", ".join(
                f"{d} ({n:,} teams)" for d, n in
                self.q("SELECT built, count(*) FROM team_pool GROUP BY 1 ORDER BY 1")))

    def fill(self, n_rows):
        """fill_ratings' pool for a row, against the pool the engine stamped.
        Python, because the decision is Python: a sample of engine-rated
        rows through fill_ratings._rowPool -- what any row the solve did not
        rate (every nightly-priced row) gets."""
        if not n_rows:
            return
        t0 = time.time()
        try:
            import fill_ratings as F
            import build_ranking_results as B
        except Exception as exc:                             # noqa: BLE001
            print(f"\n=== fill: cannot import fill_ratings ({exc}) -- skipped")
            return
        print("\n=== fill: fill_ratings' pool vs the engine's, on engine-rated rows")
        moved, seen, sexed = Counter(), Counter(), Counter()
        examples = defaultdict(list)
        import psycopg2.extras
        try:
            B.prepareGenderTemp(self.conn)               # TEMP tables, all
            B.prepareXcTfrrsDistTemp(self.conn)
            B.prepareSprintEvents(self.conn)
            B.prepareTfStateTemp(self.conn)
        except Exception as exc:                             # noqa: BLE001
            print(f"    the board build's temp tables failed ({exc}) -- skipped")
            return
        for sport in ("XC", "TF"):
            # the board query fill_ratings inverts, turned to a sample of the
            # rows the ENGINE rated: same joins, same facts, same resolver
            sql = B._sourceSql(self.conn, sport).replace(
                F._MARK[sport][0],
                "WHERE r.speed_rating IS NOT NULL AND r.rating_pool IS NOT NULL"
                " AND mod(r.result_id, 97) = 0", 1)
            with self.conn.cursor(cursor_factory=psycopg2.extras.NamedTupleCursor) as cur:
                try:
                    cur.execute(sql + f" LIMIT {int(n_rows)}",
                                {"since": "1900-01-01", "until": "2100-01-01"})
                    rows = cur.fetchall()
                except Exception as exc:                     # noqa: BLE001
                    print(f"    {sport}: query failed ({type(exc).__name__}: {exc}) -- skipped")
                    continue
            for row in rows:
                stamped = str(row.rating_pool).split("|", 1)[0]
                got = F._rowPool(row, sport)
                a = stamped.split("_", 1)[0]
                b = (got or "none").split("_", 1)[0]
                seen[sport] += 1
                if got and got != stamped and a == b:
                    sexed[sport] += 1
                if a != b:
                    moved[(sport, a, b)] += 1
                    if len(examples[(sport, a, b)]) < 3:
                        examples[(sport, a, b)].append(row.person_id)
        total = sum(seen.values())
        n_moved = sum(moved.values())
        print(f"    {total:,} engine-rated rows checked; {n_moved:,} "
              f"({100 * n_moved / max(total, 1):.1f}%) would get another level "
              f"from fill_ratings ({time.time() - t0:.0f}s)")
        if sum(sexed.values()):
            print(f"    and {sum(sexed.values()):,} more at the same level with "
                  f"the other sex (the boards' gender, not the engine's)")
        for (sport, a, b), n in moved.most_common(20):
            print(f"      {n:>8,}  {sport}  engine {a:<8} fill {b:<8} e.g. "
                  + ", ".join(str(p) for p in examples[(sport, a, b)]))
        self.found.append(("fill", "fill_ratings would pool another level (% of rows)",
                           None, None, None))
        self.fill_share = 100 * n_moved / max(total, 1)

    def meet(self, meet_id):
        """One meet, every division: how its rated rows were pooled, and the
        rows whose grade says another level. Not sampled."""
        print(f"\n=== meet {meet_id}: pools by division")
        for table, sport in (("results", "XC"), ("results_tf", "TF")):
            if "rating_pool" not in self.cols(table):
                continue
            got = self.q(f"""
                SELECT r.div_id, r.source,
                       split_part(r.rating_pool, '|', 1) AS pool, count(*),
                       count(*) FILTER (WHERE r.rating_pool IS NULL) AS unrated
                FROM   {table} r WHERE r.meet_id = %s
                GROUP  BY 1, 2, 3 ORDER BY 1, 2, 4 DESC""", (meet_id,))
            for div, src, pool, n, _u in got:
                print(f"    {sport} div {div} ({src})  {pool or '(unrated)':<18}{n:>6,}")
            odd = self.q(f"""
                SELECT r.person_id, r.athlete_name, r.div_id, r.grade, r.school,
                       r.team_id, split_part(r.rating_pool, '|', 1),
                       round(r.speed_rating::numeric, 1)
                FROM   {table} r WHERE r.meet_id = %s
                  AND  ({_GRADE_LEVEL_SQL.format(g="btrim(r.grade)")})
                       IS DISTINCT FROM
                       substring(r.rating_pool from '^(elem|ms|hs|college|pro)_')
                  AND  btrim(r.grade) ~ '{_SCHOOL_GRADE}'
                ORDER  BY r.speed_rating DESC NULLS LAST LIMIT 40""", (meet_id,))
            if odd:
                print(f"    {sport}: rows whose school grade names another level "
                      "(person, name, div, grade, school, team, pool, rating)")
                for r in odd:
                    print("      " + "  ".join("-" if v is None else str(v) for v in r))

    def summary(self):
        print("\n" + "=" * 78)
        print("RANKED BY RATINGS MOVED (rows; estimates x{} under --sample)".format(self.sample))
        print(f"  {'kind':<15}{'rows':>13}{'ranked':>13}{'athletes':>11}   what")
        for key, title, rows, ranked, people in sorted(
                [f for f in self.found if f[2] is not None], key=lambda f: -f[2]):
            print(f"  {key:<15}{rows:>13,}{ranked:>13,}{people:>11,}   {title}")
        if getattr(self, "fill_share", None) is not None:
            print(f"  {'fill':<15}{self.fill_share:>12.1f}%{'':>13}{'':>11}   "
                  "of engine-rated rows fill_ratings would pool at another level")


SECTIONS = ("flip", "xflip", "ms_in_hs", "hs_in_ms", "hs_in_college",
            "college_in_hs", "pro_in_school", "pro_pool_slow", "postcollege",
            "gender", "grade_typo", "unattached", "club", "race_level",
            "merge", "stale", "fill")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sample", type=int, default=10,
                    help="1 person in N (default 10); 1 = everybody")
    ap.add_argument("--since", default="2018-08-01",
                    help="rows dated on or after (default 2018-08-01)")
    ap.add_argument("--only", default="", help="comma-separated sections")
    ap.add_argument("--show", type=int, default=8, help="examples per section")
    ap.add_argument("--fill-check", type=int, default=20000,
                    help="rows per sport through fill_ratings' resolver; 0 skips")
    ap.add_argument("--meet", type=int, default=None,
                    help="also show one meet's pools by division (athletic.net meet id)")
    ap.add_argument("--dsn", default=None, help=argparse.SUPPRESS)  # tests
    a = ap.parse_args(argv)
    only = [s for s in a.only.split(",") if s] or list(SECTIONS)
    bad = [s for s in only if s not in SECTIONS]
    if bad:
        ap.error(f"unknown section(s) {bad}; choose from {', '.join(SECTIONS)}")

    if a.dsn:
        import psycopg2
        return _run(psycopg2.connect(a.dsn), a, only)
    from database import getConn
    with getConn() as conn:
        try:
            return _run(conn, a, only)
        finally:
            conn.autocommit = False          # back to the pool as it came


def _run(conn, a, only):
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET work_mem = '256MB'")
        cur.execute("SET temp_buffers = '1GB'")
    d = Diag(conn, a.sample, a.show)
    t0 = time.time()
    if a.meet is not None:
        d.meet(a.meet)
        if not a.only:
            return d                         # --meet alone: just the meet
    d.build(a.since)
    for s in only:
        if s == "fill":
            d.fill(a.fill_check)
        else:
            getattr(d, s)()
    d.summary()
    print(f"\n[diag] done in {(time.time() - t0) / 60:.1f} min; nothing written")
    return d


if __name__ == "__main__":
    main()
