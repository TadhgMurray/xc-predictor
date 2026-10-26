#!/usr/bin/env python3
"""
link_freshmen.py -- join a college freshman's tfrrs identity to their own
high school career on anet.

    /srv/venv/bin/python scripts/link_freshmen.py               # DRY RUN
    /srv/venv/bin/python scripts/link_freshmen.py --show 80     # more pairs
    /srv/venv/bin/python scripts/link_freshmen.py --years 2024 2025 2026
    /srv/venv/bin/python scripts/link_freshmen.py --write       # link them
    /srv/venv/bin/python scripts/link_freshmen.py --undo        # take it back

★ THE HOLE (owner, 2026-09-25: "all freshman are unknown... they have not
  been linked with anet... especially if anet doesn't have the race so it
  can't link"). Every existing pass joins a tfrrs row to an anet person
  through something the two feeds SHARE -- a canon-linked meet and a time
  (link_idless_tfrrs), or a name unique across the WHOLE corpus
  (link_idless_by_name). A freshman shares nothing: the high school career is
  on anet, the college career on tfrrs, and not one race is in both. So the
  freshman is a stranger to their own history -- no name on the predictor,
  a page holding one result, no high school rows behind their rating.

★ THE EVIDENCE THIS USES IS THE CAREER ITSELF. A person who
    - has a SENIOR high school season on anet in academic year Y-1, and
    - a tfrrs person who first races, in college, in academic year Y,
  with the SAME NAME is almost always one runner who graduated. The step
  from grade 12 to college freshman is the one transition every college
  runner makes, and it is exactly the one the shared-race passes cannot see.

⚠ A WRONG MERGE IS WORSE THAN A MISSED ONE (unlink.py says so, and splits
  rather than merges for that reason). So every guard is on the side of
  leaving a pair alone:
    - the name must be UNIQUE ON BOTH SIDES within that year: exactly one
      anet senior of class Y-1 and exactly one tfrrs first-year in Y carry
      it, counting seniors ALREADY linked (two Jake Smiths is no link, even
      if one of them is taken);
    - two tokens at least, letters only after normalising;
    - the genders, where both are known, agree;
    - the tfrrs person has NO row before Y and NO anet row at all (it is a
      stranger, not someone already joined to something);
    - the anet senior has NO tfrrs row (not already joined to a college);
    - the tfrrs first-year has at least one row that can only be COLLEGE,
      and the anet senior has NO high school row from Y on (level_conflict.py's
      two definitions -- see "A SENIOR WHO IS STILL IN HIGH SCHOOL" below).

! REVERSIBLE, ROW BY ROW. --write logs every moved result -- (sport,
  result_id, from, to) -- in person_link_log before moving it, and --undo
  puts back exactly those rows. Derived tables (athlete_season, ratings,
  boards) follow on the next pipeline run.

Read-only without --write / --undo.
"""
import argparse
import datetime
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
# ! AND racecast/: jumpBands reads pool_view.repFactor, and without it 04a
#   died (pipeline 2026-10-17: "No module named 'pool_view'")
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.append(_p)

from database import getConn                                  # noqa: E402
# ★ THE LEVEL OF A ROW HAS ONE DEFINITION (engine/level_conflict.py), and the
#   linker asks it the same question the conflict flag does. Imported, not
#   spelled here: a linker and a flag that disagree about what a high school
#   row is would weld exactly the pairs the flag then has to hide.
from level_conflict import collegeSql, hsSql, stageCollegeSchools  # noqa: E402

RULE = "freshman"
SENIOR = ("12", "12th", "sr", "sr.", "senior")


def academicYear(d=None):
    d = d or datetime.date.today()
    return d.year if d.month >= 8 else d.year - 1


def normName(name):
    """'SMITH, Jake' and 'Jake  Smith' -> 'jake smith'; None when it is not a
    two-token name.

    ★ person_name.personKey, SUFFIX KEPT (2026-10-26). Accents are FOLDED, not
      deleted: 'José Peña' and 'Jose Pena' were 'jos pea' and 'jose pena',
      two people (about 670 such pairs at one school, scripts/diag_name_key).
      Jr / II / III stay: a suffix difference can be a father and a son."""
    from person_name import personKey
    return personKey(name, keep_suffix=True)


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def _step(cur, what, sql, params=None):
    """Run one statement and say how long it took -- a scan of a 200M-row
    table is minutes, and silence reads as a hang."""
    import time
    t0 = time.time()
    print(f"[link]   {what} ...", end="", flush=True)
    cur.execute(sql, params or {})
    print(f" {time.time() - t0:.0f}s"
          + (f", {cur.rowcount:,} rows" if cur.rowcount >= 0 else ""),
          flush=True)


# A college freshman's grade, however the feed spells it. The tfrrs side of a
# pair must carry one in its first season: tfrrs also hosts some high school
# and middle school meets, and the first dry run paired high school seniors
# with "first-years" at Forest Park Jr High and Darnell Cookman MS.
# ⚠ AND A GRADE OF "FR" IS NOT ENOUGH ON ITS OWN (2026-09-29). A bare Fr is a
#   ninth grader in high school and a first-year in college (grade_sanity
#   2c), and a tfrrs-hosted high school meet writes it for the former. So the
#   identity must ALSO hold a row that can only be college -- a college team
#   slug, or the FR-1 eligibility form at a known college
#   (level_conflict.collegeSql; the form alone is a tfrrs-hosted high school
#   meet as often as not, the server run of 2026-09-29).
FRESHMAN_RE = r"^(fr|freshman|13)"


def freshmen(cur, years):
    """{y: {key: (raw_name, college, gender, n_rows)}} -- tfrrs identities
    whose first race anywhere is in academic year y, graded as a college
    freshman, with no anet row at all. ONE scan per result table.

    ★ AN IDENTITY IS A person_id, OR A tfrrs ATHLETE ID WHEN THERE IS NONE
      (key 'p:<person_id>' or 'n:<id_system>:<native_id>'). The linking
      passes stamp person_id on tfrrs rows after the fact and none of them
      runs on a schedule, so this season's rows mostly carry only the tfrrs
      athlete id -- the first dry run found 0 freshmen in 2026 for exactly
      that reason. Linking by native_id is how merge_links/stamp_fanout
      write a tfrrs person too."""
    lo, hi = f"{min(years)}-08-01", f"{max(years) + 1}-08-01"
    cur.execute("SET LOCAL work_mem = '512MB'")
    cur.execute("DROP TABLE IF EXISTS lf_t")
    cur.execute("DROP TABLE IF EXISTS lf_early")
    # collegeSql reads the known-college school strings off this session
    # (a slugless FR-1 counts only at a known college, level_conflict)
    stageCollegeSchools(cur)
    key = ("CASE WHEN person_id IS NOT NULL THEN 'p:' || person_id "
           "ELSE 'n:' || COALESCE(id_system, 'tfrrs') || ':' || native_id END")
    _step(cur, "tfrrs rows in those seasons (a scan of both result tables)", f"""
        CREATE TEMP TABLE lf_t AS
        SELECT k,
               min(person_id)                                     AS person_id,
               min(native_id)                                     AS native_id,
               min(id_system)                                     AS id_system,
               min(date)                                          AS first,
               bool_or(lower(btrim(grade)) ~ %(fr)s)              AS freshman,
               bool_or({collegeSql('x')})                        AS college,
               mode() WITHIN GROUP (ORDER BY btrim(athlete_name)) AS name,
               mode() WITHIN GROUP (ORDER BY school)              AS school,
               count(*)                                           AS n
        FROM (
            SELECT {key} AS k, person_id, native_id, 'tfrrs'::text AS id_system,
                   date, grade, athlete_name, school, source, team_slug
            FROM   results
            WHERE  source = 'tfrrs'
              AND  (person_id IS NOT NULL OR native_id IS NOT NULL)
              AND  date >= %(lo)s AND date < %(hi)s
            UNION ALL
            SELECT {key} AS k, person_id, native_id, id_system,
                   date, grade, athlete_name, school, source, team_slug
            FROM   results_tf
            WHERE  source = 'tfrrs'
              AND  (person_id IS NOT NULL OR native_id IS NOT NULL)
              AND  date >= %(lo)s AND date < %(hi)s
              AND  COALESCE(is_relay, 0) = 0
        ) x
        WHERE  NULLIF(btrim(athlete_name), '') IS NOT NULL
        GROUP  BY k
    """, {"lo": lo, "hi": hi, "fr": FRESHMAN_RE})
    _step(cur, "keeping only college freshmen",
          "DELETE FROM lf_t WHERE NOT (COALESCE(freshman, false) "
          "AND COALESCE(college, false))")
    # ! A DETACHED ROW'S PERSON IS NOBODY'S FRESHMAN (2026-10-10). The owner
    #   moved that row off a career on the athlete's "not mine"
    #   (person_pins.py, result_detach); joining its fresh person to a
    #   senior of the same name would weld it straight back.
    if _hasTable(cur, "result_detach"):
        from person_pins import detachedPersonsSql
        cur.execute(f"DELETE FROM lf_t WHERE person_id IN ({detachedPersonsSql()})")
    cur.execute("CREATE INDEX ON lf_t (person_id)")
    cur.execute("ALTER TABLE lf_t ADD COLUMN y int")
    cur.execute("""UPDATE lf_t SET y = CASE WHEN substring(first, 6, 2) >= '08'
                   THEN substring(first, 1, 4)::int
                   ELSE substring(first, 1, 4)::int - 1 END""")
    # a stranger: nothing earlier than that season and nothing on anet
    _step(cur, "dropping linked people with an anet row or an earlier race", """
        DELETE FROM lf_t t
        WHERE t.person_id IS NOT NULL AND (
              EXISTS (SELECT 1 FROM results r WHERE r.person_id = t.person_id
                        AND (r.date < (t.y::text || '-08-01')
                             OR r.source <> 'tfrrs'))
           OR EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = t.person_id
                        AND (r.date < (t.y::text || '-08-01')
                             OR r.source <> 'tfrrs')))
    """)
    # ! ONE GROUPED SCAN, NOT A PROBE PER CANDIDATE: native_id has no index,
    #   so an EXISTS per unlinked freshman would scan the table each time
    _step(cur, "earliest race of each unlinked one (a scan of both tables)", """
        CREATE TEMP TABLE lf_early AS
        SELECT sys, native_id, min(date) AS first_any FROM (
            SELECT 'tfrrs'::text AS sys, native_id, date FROM results
            WHERE  source = 'tfrrs' AND native_id IN
                   (SELECT native_id FROM lf_t WHERE person_id IS NULL)
            UNION ALL
            SELECT COALESCE(id_system, 'tfrrs'), native_id, date FROM results_tf
            WHERE  source = 'tfrrs' AND native_id IN
                   (SELECT native_id FROM lf_t WHERE person_id IS NULL)
        ) x GROUP BY sys, native_id
    """)
    _step(cur, "dropping unlinked ones with an earlier race", """
        DELETE FROM lf_t t USING lf_early e
        WHERE t.person_id IS NULL AND e.native_id = t.native_id
          AND e.sys = t.id_system
          AND e.first_any < (t.y::text || '-08-01')
    """)
    gender = ("(SELECT g.gender FROM person_gender g "
              "WHERE g.person_id = t.person_id LIMIT 1)"
              if _hasTable(cur, "person_gender") else "NULL::text")
    cur.execute(f"SELECT t.y, t.k, t.name, t.school, {gender}, t.n "
                f"FROM lf_t t WHERE t.y = ANY(%s)", (list(years),))
    out = {y: {} for y in years}
    for y, k, nm, sch, g, n in cur.fetchall():
        out[y][k] = (nm, sch, g, n)
    return out


# ★ A SENIOR WHO IS STILL IN HIGH SCHOOL IS NOT A GRADUATE (the NESCAC
#   review, 2026-09-29). A senior is keyed on the LAST grade-12 row, and
#   nothing asked whether the same anet person kept racing high school
#   afterwards -- a person whose record says 12 in one year and 10 or 11 the
#   next (a mistyped grade, or two kids of one name on one anet id) became a
#   "graduate" of the earlier year, and the college first-year of the same
#   name was welded onto them. The result is the exact shape the review
#   found: a college season and a high school season in one academic year on
#   one person_id, the high school race rated on the collegian. A graduate of
#   class Y-1 has no high school row in Y or after; one who has is not paired.
#   (A pair refused here stays two people, which is what unlink.py and this
#   file's header both prefer: a missed merge is cheap, a wrong one is not.)
def seniors(cur, years):
    """{y: {person_id: (name, high_school, gender, has_tfrrs, still_hs)}} --
    anet persons with a senior high school row in academic year y - 1;
    still_hs says they raced high school again in year y or later. One scan per
    table for every year."""
    lo, hi = f"{min(years) - 1}-08-01", f"{max(years)}-08-01"
    # the "raced college since" test reads level_conflict's known colleges
    # off this session; staged once, reused if freshmen() already did
    stageCollegeSchools(cur)
    cur.execute("DROP TABLE IF EXISTS lf_a")
    _step(cur, "anet senior rows in the seasons before (a scan of both tables)", """
        CREATE TEMP TABLE lf_a AS
        SELECT person_id,
               CASE WHEN substring(max(date), 6, 2) >= '08'
                    THEN substring(max(date), 1, 4)::int + 1
                    ELSE substring(max(date), 1, 4)::int END      AS y,
               mode() WITHIN GROUP (ORDER BY school)              AS school
        FROM (
            SELECT person_id, date, school FROM results
            WHERE  source = 'anet' AND person_id IS NOT NULL
              AND  date >= %(lo)s AND date < %(hi)s
              AND  lower(btrim(grade)) = ANY(%(sr)s)
            UNION ALL
            SELECT person_id, date, school FROM results_tf
            WHERE  source = 'anet' AND person_id IS NOT NULL
              AND  date >= %(lo)s AND date < %(hi)s
              AND  lower(btrim(grade)) = ANY(%(sr)s)
        ) x
        GROUP  BY person_id
    """, {"lo": lo, "hi": hi, "sr": list(SENIOR)})
    cur.execute("CREATE INDEX ON lf_a (person_id)")
    _step(cur, "their names, genders, tfrrs rows, later school rows (index probes)", f"""
        CREATE TEMP TABLE lf_a2 AS
        SELECT a.y, a.person_id,
               (SELECT concat_ws(' ', btrim(x.first_name), btrim(x.last_name))
                FROM athletes x WHERE x.athlete_id = a.person_id
                ORDER BY (NULLIF(btrim(x.last_name), '') IS NOT NULL) DESC
                LIMIT 1)                                   AS name,
               a.school,
               (SELECT x.gender FROM athletes x
                WHERE x.athlete_id = a.person_id LIMIT 1)  AS gender,
               -- ! A COLLEGE tfrrs ROW, NOT ANY tfrrs ROW (owner, 2026-10-11:
               --   Connor Fanberg, Jesuit (LA) -> Johns Hopkins, never joined).
               --   A high schooler who races an open college meet is on
               --   tfrrs as "Unattached" with a high school grade; that is
               --   not being joined to a college, and it kept every such
               --   senior (common in Louisiana, Texas, the Northeast) out
               --   of the freshman pass. level_conflict's college test is
               --   the same one the freshman side uses.
               EXISTS (SELECT 1 FROM results r WHERE r.person_id = a.person_id
                         AND r.source = 'tfrrs' AND {collegeSql('r')})
               OR EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = a.person_id
                         AND r.source = 'tfrrs' AND {collegeSql('r')})   AS has_tfrrs,
               -- a high school row in the freshman year or later: not a graduate
               EXISTS (SELECT 1 FROM results r WHERE r.person_id = a.person_id
                         AND r.date >= (a.y::text || '-08-01')
                         AND {hsSql('r')})
               OR EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = a.person_id
                         AND r.date >= (a.y::text || '-08-01')
                         AND {hsSql('r')})                 AS still_hs
        FROM lf_a a WHERE a.y = ANY(%(ys)s)
    """, {"ys": list(years)})
    cur.execute("SELECT y, person_id, name, school, gender, has_tfrrs, still_hs "
                "FROM lf_a2")
    out = {y: {} for y in years}
    for y, pid, nm, sch, g, ht, still in cur.fetchall():
        out[y][pid] = (nm, sch, g, ht, bool(still))
    return out


def _g(v):
    v = str(v or "").strip().upper()[:1]
    return v if v in ("M", "F") else None


def pairsFor(fresh, senior):
    """[(tfrrs_pid, anet_pid, name, college, high_school)] -- names unique on
    both sides, genders agreeing where known, the senior not already joined
    and not still racing high school in the freshman year."""
    t_by, a_by = defaultdict(list), defaultdict(list)
    for pid, (nm, sch, g, n) in fresh.items():
        k = normName(nm)
        if k:
            t_by[k].append(pid)
    for pid, (nm, sch, g, ht, *_still) in senior.items():
        k = normName(nm)
        if k:
            a_by[k].append(pid)       # ! linked seniors count toward ambiguity
    out, skipped = [], defaultdict(int)
    for k, ts in t_by.items():
        as_ = a_by.get(k, [])
        if not as_:
            continue
        if len(ts) != 1 or len(as_) != 1:
            skipped["name not unique"] += 1
            continue
        t, a = ts[0], as_[0]
        if senior[a][3]:
            skipped["senior already on tfrrs"] += 1
            continue
        if len(senior[a]) > 4 and senior[a][4]:
            skipped["senior still in high school"] += 1
            continue
        gt, ga = _g(fresh[t][2]), _g(senior[a][2])
        if gt and ga and gt != ga:
            skipped["gender differs"] += 1
            continue
        out.append((t, a, fresh[t][0], fresh[t][1], senior[a][1]))
    return out, dict(skipped)


# ★ A YEAR BETWEEN SCHOOL AND COLLEGE (owner, 2026-10-11: Owen Staley,
#   Sleepy Hollow senior in 2024-25, then Tufts FR-1 from September 2026 --
#   "he was injured freshman year"). pairsFor only looks at seniors of the
#   year straight before, so a gap year, an injury year or a redshirt
#   before the first race left the first-year a stranger to their own high
#   school career. A first-year whose name has NO senior the year before
#   is matched against the seniors one year further back, on every guard
#   pairsFor uses: unique on both sides, no college tfrrs row, the genders
#   agreeing -- and "not still in high school" is judged from the gap year
#   on, so a senior who raced high school again is never the graduate.
# ! ONLY WHEN THE YEAR BEFORE HAS NOBODY OF THAT NAME: a same-name senior
#   there is either the match (pairsFor) or an ambiguity, never a reason to
#   reach further back.
def gapPairsFor(fresh, senior_prev, senior_gap, paired):
    """[(tfrrs_pid, anet_pid, name, college, high_school)] for first-years of
    year y matched to seniors of y - 2 (senior_gap, keyed as seniors(y - 1)).
    `senior_prev` is seniors(y), the year straight before; `paired` the
    tfrrs ids pairsFor already joined."""
    prev_names = {normName(v[0]) for v in senior_prev.values()}
    t_by, a_by = defaultdict(list), defaultdict(list)
    for pid, (nm, sch, g, n) in fresh.items():
        k = normName(nm)
        if k and pid not in paired and k not in prev_names:
            t_by[k].append(pid)
    for pid, (nm, sch, g, ht, *_still) in senior_gap.items():
        k = normName(nm)
        if k:
            a_by[k].append(pid)
    out, skipped = [], defaultdict(int)
    for k, ts in t_by.items():
        as_ = a_by.get(k, [])
        if not as_:
            continue
        if len(ts) != 1 or len(as_) != 1:
            skipped["gap: name not unique"] += 1
            continue
        t, a = ts[0], as_[0]
        if senior_gap[a][3]:
            skipped["gap: senior already on tfrrs"] += 1
            continue
        if len(senior_gap[a]) > 4 and senior_gap[a][4]:
            skipped["gap: senior still in high school"] += 1
            continue
        gt, ga = _g(fresh[t][2]), _g(senior_gap[a][2])
        if gt and ga and gt != ga:
            skipped["gap: gender differs"] += 1
            continue
        out.append((t, a, fresh[t][0], fresh[t][1], senior_gap[a][1]))
    return out, dict(skipped)


def pairsAll(fresh_by, senior_by, years):
    """{y: (pairs, skipped)} -- the straight pass, then the gap-year pass
    for whoever it left. senior_by must hold seniors(y - 1) for every y too
    (seniorYears)."""
    out = {}
    for y in years:
        p, skipped = pairsFor(fresh_by[y], senior_by[y])
        gp, gs = gapPairsFor(fresh_by[y], senior_by[y], senior_by.get(y - 1, {}),
                             {t for t, *_ in p})
        skipped.update(gs)
        out[y] = (p + gp, skipped)
    return out


def seniorYears(years):
    """The years seniors() must cover for pairsAll: each y and y - 1."""
    return sorted(set(years) | {y - 1 for y in years})


# ★ SAME NAME, TOLD APART BY RACE TIMES (owner, 2026-10-11: "it has to be
#   race times"). pairsFor refuses every name with two first-years or two
#   seniors -- 550-630 names a year, the largest reason freshmen stay
#   strangers. A senior's last high school season and the same runner's
#   first college season are one athlete a few months apart, so their
#   ratings, both on the high school scale (pool_view.repFactor), sit a
#   predictable step apart.
#
#   THE STEP IS MEASURED, NOT CHOSEN: every person already carrying both a
#   high school season in Y-1 and a college season in Y (athletes the
#   other passes joined) gives one step per sport; the band is the central
#   JUMP_COVERAGE of those steps. A same-name group is resolved only when
#   it is unambiguous in BOTH directions:
#     - the first-year's step to exactly one of the seniors is inside the
#       band (every sport the two share must agree), and every other senior
#       of the name is OUTSIDE it -- a senior with no comparable season
#       could be the one, so it refuses the group;
#     - and the same from the senior's side, over the group's first-years.
#   Every pairsFor guard still applies (no college tfrrs row, not still in
#   high school, genders agreeing).
# ! JUMP_COVERAGE IS THE ONE CHOICE HERE: how much of the measured spread
#   counts as "the same runner". The band's edges come from the data.
TIME_RULE = "freshman_time"
JUMP_COVERAGE = 0.95


def ambiguousGroups(fresh, senior):
    """[(name, [tfrrs keys], [anet persons])] -- names on both sides with
    more than one candidate on either side (pairsFor's 'name not unique')."""
    t_by, a_by = defaultdict(list), defaultdict(list)
    for pid, v in fresh.items():
        k = normName(v[0])
        if k:
            t_by[k].append(pid)
    for pid, v in senior.items():
        k = normName(v[0])
        if k:
            a_by[k].append(pid)
    return [(k, ts, a_by[k]) for k, ts in t_by.items()
            if k in a_by and (len(ts) > 1 or len(a_by[k]) > 1)]


def jumpBands(cur, years, coverage=JUMP_COVERAGE):
    """{sport: (lo, hi, n)} -- the central `coverage` of the step from a
    high school season in Y-1 to the same person's college season in Y, on
    the high school scale, over the years being linked and the four before."""
    from pool_view import repFactor
    lo_y, hi_y = min(years) - 5, max(years) - 1
    cur.execute(r"""
        SELECT a.sport, a.pool, a.mean_rating, b.pool, b.mean_rating
        FROM   athlete_season a
        JOIN   athlete_season b ON b.person_id = a.person_id
                               AND b.sport = a.sport AND b.year = a.year + 1
        WHERE  a.pool LIKE 'hs\_%%' AND b.pool LIKE 'college\_%%'
          AND  a.year BETWEEN %s AND %s
          AND  a.mean_rating IS NOT NULL AND b.mean_rating IS NOT NULL
    """, (lo_y, hi_y))
    steps = defaultdict(list)
    for sp, pa, ra, pb, rb in cur.fetchall():
        fa, fb = repFactor(pa, sp), repFactor(pb, sp)
        if fa and fb:
            steps[sp].append(float(rb) * fb - float(ra) * fa)
    out = {}
    for sp, v in steps.items():
        v.sort()
        tail = (1.0 - coverage) / 2.0
        out[sp] = (v[int(tail * (len(v) - 1))],
                   v[int((1.0 - tail) * (len(v) - 1))], len(v))
    return out


def _hsSeasons(cur, pids, years, level):
    """{(pid, year, sport): rating on the high school scale} for seasons of
    `level` ('hs' or 'college')."""
    from pool_view import repFactor
    if not pids:
        return {}
    cur.execute("""
        SELECT person_id, year, sport, pool, mean_rating FROM athlete_season
        WHERE  person_id = ANY(%s) AND year = ANY(%s)
          AND  pool LIKE %s AND mean_rating IS NOT NULL
    """, (list(pids), list(years), level + "\\_%"))
    out = {}
    for pid, y, sp, pool, r in cur.fetchall():
        f = repFactor(pool, sp)
        if f:
            out[(int(pid), int(y), sp)] = float(r) * f
    return out


def timePairsFor(groups, y, fresh, senior, fr_r, sr_r, bands):
    """[(tfrrs key, anet person, name, college, high school)] resolved by the
    step, and {reason: count} for the groups left alone. fr_r / sr_r are
    _hsSeasons for the first-years (year y) and seniors (year y - 1)."""
    def verdict(t, a):
        if not t.startswith("p:"):
            return None
        tp, shared, inside = int(t[2:]), 0, True
        for sp, (lo, hi, _n) in bands.items():
            f, s_ = fr_r.get((tp, y, sp)), sr_r.get((a, y - 1, sp))
            if f is None or s_ is None:
                continue
            shared += 1
            inside = inside and lo <= f - s_ <= hi
        return None if not shared else inside

    out, skipped = [], defaultdict(int)
    for _k, ts, as_ in groups:
        for t in ts:
            hits = [a for a in as_ if verdict(t, a) is True]
            if len(hits) != 1:
                skipped["time: no single senior in the band"] += 1
                continue
            a = hits[0]
            if any(verdict(t, o) is not False for o in as_ if o != a):
                skipped["time: another senior not ruled out"] += 1
                continue
            if any(verdict(o, a) is not False for o in ts if o != t):
                skipped["time: another first-year not ruled out"] += 1
                continue
            if senior[a][3]:
                skipped["time: senior already on tfrrs"] += 1
                continue
            if len(senior[a]) > 4 and senior[a][4]:
                skipped["time: senior still in high school"] += 1
                continue
            gt, ga = _g(fresh[t][2]), _g(senior[a][2])
            if gt and ga and gt != ga:
                skipped["time: gender differs"] += 1
                continue
            out.append((t, a, fresh[t][0], fresh[t][1], senior[a][1]))
    return out, dict(skipped)


def timePairsAll(cur, fresh_by, senior_by, years):
    """{y: (pairs, skipped)} -- the race-time pass over every year's
    same-name groups, and the bands it used."""
    bands = jumpBands(cur, years)
    out = {}
    for y in years:
        groups = ambiguousGroups(fresh_by[y], senior_by[y])
        tps = {int(t[2:]) for _k, ts, _a in groups for t in ts if t.startswith("p:")}
        aps = {a for _k, _t, as_ in groups for a in as_}
        fr_r = _hsSeasons(cur, tps, [y], "college")
        sr_r = _hsSeasons(cur, aps, [y - 1], "hs")
        out[y] = timePairsFor(groups, y, fresh_by[y], senior_by[y], fr_r, sr_r, bands)
    return out, bands


def dedupePairs(pairs):
    """Across years: a tfrrs person joins once, and an anet senior claimed by
    two different first-years (a repeated senior year) joins neither."""
    from collections import Counter
    by_anet = Counter(a for _t, a, *_ in {p[0]: p for p in pairs}.values())
    seen, out = set(), []
    for p in pairs:
        if p[0] in seen or by_anet[p[1]] > 1:
            continue
        seen.add(p[0])
        out.append(p)
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


def write(conn, pairs, rule=RULE):
    """Stamp the anet person onto every row of each tfrrs identity: by
    person_id ('p:') or by tfrrs athlete id ('n:system:id', the rows that
    carry no person_id yet -- as merge_links and stamp_fanout write one)."""
    with conn.cursor() as cur:
        cur.execute(LOG_DDL)
        by_p = [(int(t[2:]), a) for t, a, *_ in pairs if t.startswith("p:")]
        by_n = []
        for t, a, *_ in pairs:
            if t.startswith("n:"):
                _n, system, native = t.split(":", 2)
                by_n.append((system, int(native), a))
        from psycopg2.extras import execute_values
        cur.execute("DROP TABLE IF EXISTS lf_map")
        cur.execute("CREATE TEMP TABLE lf_map (from_person bigint PRIMARY KEY,"
                    " to_person bigint NOT NULL)")
        cur.execute("DROP TABLE IF EXISTS lf_nmap")
        cur.execute("CREATE TEMP TABLE lf_nmap (id_system text, native_id bigint,"
                    " to_person bigint NOT NULL, PRIMARY KEY (id_system, native_id))")
        if by_p:
            execute_values(cur, "INSERT INTO lf_map VALUES %s", by_p)
        if by_n:
            execute_values(cur, "INSERT INTO lf_nmap VALUES %s", by_n)
        moved = {}
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            sysx = ("'tfrrs'" if sport == "XC"
                    else "COALESCE(r.id_system, 'tfrrs')")
            # from_person 0 in the log = "had no person_id" (undo -> NULL)
            cur.execute(f"""
                INSERT INTO person_link_log (sport, result_id, from_person,
                                             to_person, rule)
                SELECT %s, r.result_id, m.from_person, m.to_person, %s
                FROM   {table} r JOIN lf_map m ON r.person_id = m.from_person
                UNION ALL
                SELECT %s, r.result_id, 0, n.to_person, %s
                FROM   {table} r JOIN lf_nmap n
                       ON r.source = 'tfrrs' AND r.person_id IS NULL
                      AND r.native_id = n.native_id AND {sysx} = n.id_system
                ON CONFLICT DO NOTHING
            """, (sport, rule, sport, rule))
            cur.execute(f"""
                UPDATE {table} r SET person_id = m.to_person
                FROM   lf_map m WHERE r.person_id = m.from_person
            """)
            a = cur.rowcount
            cur.execute(f"""
                UPDATE {table} r SET person_id = n.to_person
                FROM   lf_nmap n
                WHERE  r.source = 'tfrrs' AND r.person_id IS NULL
                  AND  r.native_id = n.native_id AND {sysx} = n.id_system
            """)
            moved[sport] = a + cur.rowcount
    conn.commit()
    return moved


def undo(conn, rule=RULE):
    with conn.cursor() as cur:
        if not _hasTable(cur, "person_link_log"):
            print("[link] nothing to undo")
            return
        back = {}
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            cur.execute(f"""
                UPDATE {table} r SET person_id = NULLIF(l.from_person, 0)
                FROM   person_link_log l
                WHERE  l.sport = %s AND l.rule = %s
                  AND  r.result_id = l.result_id AND r.person_id = l.to_person
            """, (sport, rule))
            back[sport] = cur.rowcount
        cur.execute("DELETE FROM person_link_log WHERE rule = %s", (rule,))
    conn.commit()
    print(f"[link] undone: {back}")


def explainName(name, y, fresh, senior, pairs):
    """--name: where one name stands in year y (owner, 2026-10-11: a grep of
    the dry run cannot tell "not a candidate" from "not shown")."""
    k = normName(name)
    fs = [(pid, v) for pid, v in fresh.items() if normName(v[0]) == k]
    ss = [(pid, v) for pid, v in senior.items() if normName(v[0]) == k]
    print(f"  [{name}] {y}: {len(fs)} college first-year(s), {len(ss)} high school senior(s) of {y - 1}")
    for pid, (nm, sch, g, n) in fs:
        print(f"      first-year  {pid}  {sch}  gender {g or '?'}  {n} rows")
    for pid, (nm, sch, g, ht, *still) in ss:
        print(f"      senior      {pid}  {sch}  gender {g or '?'}"
              f"  college tfrrs row: {'YES (skipped)' if ht else 'no'}"
              f"  raced HS after: {'YES (skipped)' if (still and still[0]) else 'no'}")
    hit = [p for p in pairs if normName(p[2]) == k]
    if hit:
        print(f"      -> PAIRED: tfrrs {hit[0][0]} -> anet person {hit[0][1]}")
    elif fs and ss:
        print("      -> not paired: see the flags above (both sides must be exactly one, genders agree)")
    elif not fs:
        print("      -> no college first-year by that name this year (already linked, an earlier"
              " row, an anet row, or no freshman/college grade on the rows)")
    else:
        print("      -> no high school senior by that name in the year before")


# ★ ONE PAIR, EVERY GUARD (owner, 2026-10-11: /athlete/29603096 and
#   /athlete/1009167329, "clearly the same person but I want it to be
#   automatic"). --name needs the name and only says where it landed; this
#   takes the two page ids and prints each check the pair has to pass, so
#   the one that refused it is named. Read-only.
def explainPair(cur, t_pid, a_pid, years, fresh_by, senior_by):
    print(f"\n[pair] college page {t_pid}  <-  high school page {a_pid}")
    for label, pid in (("college", t_pid), ("high school", a_pid)):
        cur.execute("""
            SELECT src, sport, min(date), max(date), count(*),
                   array_agg(DISTINCT lower(btrim(grade))) FILTER (WHERE grade IS NOT NULL),
                   mode() WITHIN GROUP (ORDER BY school),
                   mode() WITHIN GROUP (ORDER BY btrim(athlete_name))
            FROM (SELECT source AS src, 'XC' AS sport, date, grade, school, athlete_name
                  FROM results WHERE person_id = %(p)s
                  UNION ALL
                  SELECT source, 'TF', date, grade, school, athlete_name
                  FROM results_tf WHERE person_id = %(p)s) x
            GROUP BY 1, 2 ORDER BY 1, 2""", {"p": pid})
        rows = cur.fetchall()
        cur.execute("""SELECT concat_ws(' ', btrim(first_name), btrim(last_name)), gender
                       FROM athletes WHERE athlete_id = %s OR person_id = %s LIMIT 1""",
                    (pid, pid))
        prof = cur.fetchone()
        print(f"  {label} page {pid}: profile name {prof[0] if prof else '(no athletes row)'!r}"
              f", gender {(prof[1] if prof else None) or '?'}")
        for src, sp, lo, hi, n, grades, sch, nm in rows:
            print(f"    {src:<6} {sp}  {lo} .. {hi}  {n:>4} rows  school {sch!r}"
                  f"  name on rows {nm!r}  grades {sorted(grades or [])}")
    found = False
    for y in years:
        f = fresh_by.get(y, {}).get(f"p:{t_pid}")
        a = senior_by.get(y, {}).get(a_pid)
        if not f and not a:
            continue
        found = True
        print(f"  academic year {y} (college first year) / {y - 1} (senior year):")
        print(f"    college side: {'a first-year candidate, name ' + repr(f[0]) if f else 'NOT a first-year candidate this year'}")
        print(f"    senior side:  {'a senior candidate, name ' + repr(a[0]) if a else 'NOT a senior of ' + str(y - 1)}")
        if f and a:
            kf, ka = normName(f[0]), normName(a[0])
            print(f"    names: {kf!r} vs {ka!r} -> {'match' if kf == ka else 'DIFFERENT (refused)'}")
            n_f = sum(1 for v in fresh_by[y].values() if normName(v[0]) == kf)
            n_a = sum(1 for v in senior_by[y].values() if normName(v[0]) == ka)
            print(f"    same-name first-years {n_f}, same-name seniors {n_a}"
                  f" -> {'unique' if n_f == 1 and n_a == 1 else 'NOT UNIQUE (refused)'}")
            print(f"    senior already has a college tfrrs row: {'YES (refused)' if a[3] else 'no'}")
            print(f"    senior raced high school after: {'YES (refused)' if len(a) > 4 and a[4] else 'no'}")
            gt, ga = _g(f[2]), _g(a[2])
            print(f"    genders {gt or '?'} / {ga or '?'} -> {'DIFFER (refused)' if gt and ga and gt != ga else 'ok'}")
        # the gap-year pass: a first-year in y and a senior of y - 2
        ag = senior_by.get(y - 1, {}).get(a_pid)
        if f and not a and ag:
            found = True
            print(f"    gap year: a senior of {y - 2} -> tried by the gap-year pass "
                  f"(gapPairsFor); the pair runs below if it joins")
    if not found:
        print(f"  in none of the years {years} is either page a candidate.")
        print("    college side needs: tfrrs rows only, a freshman grade (FR/13) at a"
              " college, and no race before that season")
        print("    high school side needs: an anet row graded 12/SR in the season before")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    now = academicYear()
    ap.add_argument("--years", type=int, nargs="+",
                    default=[now - 2, now - 1, now],
                    help="academic years of the FRESHMAN season (default: "
                         "the last three)")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--name", help="explain this one name: is it a freshman, a senior, paired, or why not")
    ap.add_argument("--pair", type=int, nargs=2, metavar=("COLLEGE_PAGE", "HS_PAGE"),
                    help="explain why these two athlete pages were or were not joined")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--no-times", action="store_true",
                    help="skip the race-time pass over same-name groups")
    ap.add_argument("--undo-times", action="store_true",
                    help="take back only the race-time pass's joins")
    a = ap.parse_args()

    with getConn() as conn:
        if a.undo or a.undo_times:
            undo(conn, TIME_RULE if a.undo_times else RULE)
            return
        all_pairs, time_pairs = [], []
        with conn.cursor() as cur:
            print(f"[link] academic years {a.years}; each step prints when "
                  f"it finishes", flush=True)
            fresh_by = freshmen(cur, a.years)
            senior_by = seniors(cur, seniorYears(a.years))
            by_year = pairsAll(fresh_by, senior_by, a.years)
            for y in a.years:
                fresh, senior = fresh_by[y], senior_by[y]
                pairs, skipped = by_year[y]
                print(f"[link] {y}: {len(fresh):,} tfrrs first-years with no "
                      f"anet identity, {len(senior):,} anet seniors of {y - 1}; "
                      f"{len(pairs):,} pairs; skipped {skipped}")
                if a.name:
                    explainName(a.name, y, fresh, senior, pairs)
                for t, s, nm, col, hs in pairs[:a.show]:
                    print(f"    {nm:<28} {str(hs)[:30]:<30} -> {str(col)[:28]:<28}"
                          f"  tfrrs {t} -> anet person {s}")
                all_pairs += pairs
            if not a.no_times:
                by_time, bands = timePairsAll(cur, fresh_by, senior_by, a.years)
                print("[link] race-time pass: the step from a senior season to the "
                      "college first season, high school scale, central "
                      f"{JUMP_COVERAGE:.0%} of joined careers:")
                for sp, (lo, hi, n) in sorted(bands.items()):
                    print(f"    {sp}: {lo:+.1f} to {hi:+.1f}  ({n:,} careers)")
                for y in a.years:
                    tp, tskip = by_time[y]
                    print(f"[link] {y} race-time pass: {len(tp):,} pairs; skipped {tskip}")
                    for t, s_, nm, col, hs in tp[:a.show]:
                        print(f"    {nm:<28} {str(hs)[:30]:<30} -> {str(col)[:28]:<28}"
                              f"  tfrrs {t} -> anet person {s_}")
                    time_pairs += tp
            if a.pair:
                explainPair(cur, a.pair[0], a.pair[1], a.years, fresh_by, senior_by)
                hit = [p for p in all_pairs + time_pairs
                       if p[0] == f"p:{a.pair[0]}" and p[1] == a.pair[1]]
                print(f"  -> {'JOINED by this run' if hit else 'not joined'}")
        conn.rollback()                       # the temp tables only
        # one tfrrs person, one target, across years (a person is a
        # first-year in exactly one year, so this only guards a re-run)
        uniq = dedupePairs(all_pairs)
        taken = {p[0] for p in uniq}
        tuniq = dedupePairs([p for p in time_pairs if p[0] not in taken])
        if not a.write:
            print(f"[link] dry run: {len(uniq):,} pairs by name and "
                  f"{len(tuniq):,} by race times would be joined; --write to join them")
            return
        moved = write(conn, uniq)
        print(f"[link] joined {len(uniq):,} freshmen to their high school "
              f"careers; rows moved {moved}; logged in person_link_log "
              f"(--undo reverses)")
        if tuniq:
            moved = write(conn, tuniq, rule=TIME_RULE)
            print(f"[link] joined {len(tuniq):,} more by race times; rows moved "
                  f"{moved} (--undo-times reverses only these)")


if __name__ == "__main__":
    main()
