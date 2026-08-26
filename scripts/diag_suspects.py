# Project: xc-predictor
# File:    scripts/diag_suspects.py
# Purpose: POST-CONVERGENCE suspect detector. READ-ONLY. Finds divisions and
#          individual results whose speed_rating cannot be explained by the
#          athletes in them, and emits a WORKSHEET of proposed verdicts. Writes
#          nothing to the DB or to corrections.py.
#
# ============================================================================
# WHAT CHANGED, AND WHY (the baseline is now LOCAL, not global)
# ============================================================================
# The old baseline was athlete_ratings.speed_rating -- ONE global number per
# athlete, averaged over their whole career and every distance they ever ran.
# That produced FALSE POSITIVES: a college field racing a real 8k here was
# compared against a baseline built mostly from their 5k races, so the whole
# field showed a systematic offset and the division flagged even though nothing
# was wrong with the race. The global baseline carried the athlete's distance
# MIX into the residual.
#
# The fix: compare each result to the SAME athlete's ratings in the races
# immediately AROUND it -- up to 5 before and 5 after, by date, same-day
# excluded. That local bracket cancels the athlete's true level, distance mix,
# and era -- because it is THEM, days on either side. A real suspect is a spike
# against your own bracket that reverts afterwards. A fast field or an elite
# race shows NO bracket gap; the surrounding races sit at the same level.
#
#   base_ij = median rating over athlete i's bracket (<=5 before + <=5 after
#             result j, other DATES only, >= 3 bracket races required)
#   e_ij    = ln(this_race_rating_ij) - ln(base_ij)
#
#   c_j  = median_i e_ij   over a division -> whole field spiked together
#                                          -> a division-level LABEL problem
#   dev  = e_ij - c_j                       -> one runner spiked against a clean
#                                             field -> a cooked ROW
#
# Median (not mean) of the bracket, so one corrupt neighbour cannot drag the
# baseline. Athletes with no identity (person_id NULL) or < 3 bracket races get
# NO residual -- the method only speaks where an athlete has a real history.
#
# ============================================================================
# SEASONS RATIO  --  seasons_flagged / seasons_total (anet venue only)
#   Near 1.0 => persistent => label. Near 0 => one-off => weather/insane day.
#   tfrrs has no reliable venue -> n/a, leans on magnitude.
#
# EMITS (under --out, default scripts/):
#   suspects_div_<sport>.tsv  suspects_row_<sport>.tsv  suspects_<sport>.txt
#
# USAGE
#   python scripts/diag_suspects.py --sport XC
#   python scripts/diag_suspects.py --sport XC --c-threshold 0.20 --min-field 12
#   python scripts/diag_suspects.py --sport XC --dump 8916 5
# ============================================================================

import argparse
import importlib.util
import math
import os
import re
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- CONSTANTS
# ================================================================== #
_TABLE = {"XC": "results", "TF": "results_tf"}
_VENUE_TABLE = {"XC": "meets", "TF": None}

_CORRECTIONS_CANDIDATES = ("engine/corrections.py", "scripts/corrections.py",
                           "corrections.py", "backfill/corrections.py")

# Trusted year window (the DB carries 0023/2222 dates). Rows outside it sort
# unreliably, so they are excluded from every bracket.
_YEAR_LO, _YEAR_HI = 1980, 2035

# Bracket: up to this many results each side by date, same-day excluded; a
# result needs at least _BRACKET_MIN of them to earn a residual.
_BRACKET_SIDE = 5
_BRACKET_MIN = 3

# Auto division threshold. When --c-threshold is NOT supplied, the flag cutoff is
# derived from the data: p99 of |c| (the measured division noise floor) times this
# margin, so a flagged division is always comfortably OUTSIDE the ordinary spread.
# 1.10 reproduces the old hand-set XC cutoff (~0.20) and fixes the TF over-flag on
# its own -- no per-sport tuning. Raise for stricter/fewer flags, lower for more.
_THRESHOLD_MARGIN = 1.10

# Row implausibility, measured on the athlete's OWN normalized 5K (bracket median),
# then division-corrected (see _rowRelSQL) so a genuinely slow race -- mud, heat --
# where the WHOLE field is slow does NOT flag its runners. A real 5K performance
# barely moves; these lines sit where a swing stops being a real race and becomes a
# bad row. Asymmetric on purpose: you basically cannot run much FASTER than yourself
# for one race, but a blow-up can be slower, so the slow line is looser. Both are
# CLI-overridable (--row-fast / --row-slow); the sweep table (printed every run)
# shows how the flag count moves as you change them.
_ROW_FAST_PCT = 0.12   # >=12% faster than field-expected -> implausible (drop digit / wrong person)
_ROW_SLOW_PCT = 0.40   # >=40% slower than field-expected -> implausible (not a real race)

# Absolute guard: the ONLY hard time bound. ~5K WR (12:35) minus a margin; nobody
# alive runs under this, so flag on sight regardless of the relative test.
_NT_GUARD = 730.0      # 5K-equiv seconds (~12:10)

# Percent lines the sweep diagnostic reports, so you can watch the flag count move.
# Dense on purpose: the cost is the one-time residual build, not the counting, so a
# fine grid is ~free and lets you read the curve and pick the exact cut afterwards.
_SWEEP_FAST = (0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12,
               0.14, 0.16, 0.18, 0.20, 0.25)
_SWEEP_SLOW = (0.10, 0.12, 0.14, 0.15, 0.16, 0.18, 0.20, 0.22, 0.25,
               0.30, 0.35, 0.40, 0.50, 0.60)



# ================================================================== #
# CHUNK 2 -- IDENTIFIER SAFETY
# ================================================================== #

# _ident : %s binds VALUES not IDENTIFIERS, so a table name is interpolated and
#          therefore validated once, at the boundary.
def _ident(name):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


# ================================================================== #
# CHUNK 3 -- LOAD THE SSOT (annotate "already fixed")
# ================================================================== #

def _loadCorrections(path, sport):
    """Import corrections.py by PATH (no shared package across dirs). Returns
    THIS SPORT's five annotation tables, or empty sets + a warning. Per-sport
    (2026-07-13): the `already` column must reflect corrections that apply to
    THIS table -- annotating an XC worksheet with TF keys (or vice versa)
    marks alive rows as handled and hides them from the next triage pass."""
    if path is None:
        for cand in _CORRECTIONS_CANDIDATES:
            if os.path.exists(cand):
                path = cand
                break
    if path is None or not os.path.exists(path):
        print("  [warn] corrections.py not found -- 'already-fixed' annotation off")
        return {"dist_ov": set(), "dist_drop": set(),
                "gender_ov": set(), "res_drop": set(), "res_ov": set()}
    spec = importlib.util.spec_from_file_location("corrections", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(f"  [ok] corrections loaded from {path} (sport={sport})")
    return {"dist_ov": set(mod._DISTANCE_OVERRIDES_BY_SPORT[sport]),
            "dist_drop": set(mod._DISTANCE_DROP_BY_SPORT[sport]),
            "gender_ov": set(mod._GENDER_OVERRIDES_BY_SPORT[sport]),
            "res_drop": set(mod._RESULT_DROP_BY_SPORT[sport]),
            "res_ov": set(mod._RESULT_OVERRIDE_BY_SPORT[sport])}


# _alreadyTag : one word for whether (meet,div) is handled. An already-corrected
#               division that still flags is a REGRESSION.
def _alreadyTag(corr, meet, div):
    key = (meet, div)
    if key in corr["dist_drop"]:
        return "DROP"
    if key in corr["dist_ov"]:
        return "OVERRIDE"
    if key in corr["gender_ov"]:
        return "GENDER"
    return ""


# ================================================================== #
# CHUNK 4 -- BUILD THE BRACKET-RESIDUAL TEMP TABLE (the heavy pass)
# ================================================================== #

# _buildResid
# Purpose : materialize e_ij for every rated result whose athlete has a usable
#           date bracket, ONCE. This is a single ordered pass + one hash self-
#           join, NOT a per-row lookup (a correlated subquery here would re-run
#           32M times -- the anti-pattern that cost 25 minutes before).
#   _base   : rated rows with an identity and a TRUSTED date. `d` is an integer
#             day-key (YYYYMMDD) so equal `d` = same day.
#   _ranked : DENSE_RANK over date within each athlete. All rows sharing a date
#             share a rank, so a different-date neighbour is >= 1 rank away and a
#             same-day row is 0 away.
#   _resid  : for each result a, median the ln-ratings of neighbours whose rank
#             is within +/-_BRACKET_SIDE of a's rank but on a DIFFERENT date
#             (drank <> a.drank). A hash self-join on ident, one plan.
def _buildResid(cur, table):
    t = _ident(table)
    cur.execute(f"""
        CREATE TEMP TABLE _base AS
        SELECT r.result_id, r.source, r.meet_id, r.div_id,
               COALESCE(r.person_id, r.athlete_id)  AS ident,
               ln(r.speed_rating)                   AS lsr,
               -- ★ LEVEL BUCKET (2026-08-27, owner's rule: NEVER compare
               --   across pools to condemn a row). speed_rating is a
               --   PER-POOL scale: hs, college and ms carry different
               --   anchors, so a bracket spanning 8th grade -> 9th, or
               --   senior spring -> college fall, mixes two scales and
               --   manufactures a swing exactly at the transition -- the
               --   place an improving athlete is most vulnerable. The
               --   bucket is the grade's level (tfrrs rows, grade NULL,
               --   are college by the standing rule); a NULL bucket gets
               --   no residual at all -- the method abstains rather than
               --   guesses. Pro years inside the college bucket are the
               --   one residual blind spot; accepted, they are rare and
               --   pro rows are board-gated anyway.
               CASE
                 WHEN r.grade ~ '^(K|0?[1-8])$'      THEN 'ms'
                 WHEN r.grade ~ '^(9|10|11|12)$'     THEN 'hs'
                 WHEN r.grade ~* '^(fr|so|jr|sr)'    THEN 'col'
                 WHEN r.source = 'tfrrs'             THEN 'col'
               END                                  AS lvl,
               r.normalized_time                    AS nt,
               (substring(r.date, 1, 4))::int * 10000
                 + COALESCE(NULLIF(substring(r.date, 6, 2), '')::int, 0) * 100
                 + COALESCE(NULLIF(substring(r.date, 9, 2), '')::int, 0) AS d,
               CASE WHEN r.time_seconds IS NOT NULL
                     AND r.time_seconds < 999999
                     AND r.normalized_time > 0
                    THEN r.time_seconds / r.normalized_time END AS dn
        FROM {t} r
        WHERE r.speed_rating > 0
          AND r.normalized_time IS NOT NULL
          AND COALESCE(r.person_id, r.athlete_id) IS NOT NULL
          AND r.date IS NOT NULL
          AND substring(r.date, 1, 4) ~ '^[0-9]{{4}}$'
          AND (substring(r.date, 1, 4))::int BETWEEN {_YEAR_LO} AND {_YEAR_HI}
    """)
    cur.execute("CREATE INDEX ON _base (ident, d)")

    cur.execute("""
        CREATE TEMP TABLE _ranked AS
        SELECT b.*,
               DENSE_RANK() OVER (PARTITION BY ident ORDER BY d) AS drank
        FROM _base b
    """)
    cur.execute("CREATE INDEX ON _ranked (ident, drank)")

    cur.execute(f"""
        CREATE TEMP TABLE _resid AS
        SELECT a.result_id, a.source, a.meet_id AS meet, a.div_id AS div,
               a.ident, a.dn, a.nt,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY nb.nt) AS nt_base,
               a.lsr - percentile_cont(0.5) WITHIN GROUP (ORDER BY nb.lsr) AS e
        FROM _ranked a
        JOIN _ranked nb
          ON nb.ident = a.ident
         AND nb.drank BETWEEN a.drank - {_BRACKET_SIDE} AND a.drank + {_BRACKET_SIDE}
         AND nb.drank <> a.drank
         -- same level bucket only: a residual is only ever computed against
         -- races on the SAME rating scale (see lvl above). NULL abstains.
         AND a.lvl IS NOT NULL
         AND nb.lvl = a.lvl
        GROUP BY a.result_id, a.source, a.meet_id, a.div_id, a.ident, a.dn, a.nt, a.lsr
        HAVING count(*) >= {_BRACKET_MIN}
    """)
    cur.execute("CREATE INDEX ON _resid (source, meet, div)")
    cur.execute("SELECT count(*) FROM _resid")
    return cur.fetchone()[0]


# ================================================================== #
# CHUNK 5 -- DIVISION SCAN
# ================================================================== #

def _divisionStats(cur, min_field):
    """Per-division n, median gap c, IQR spread, effective distance d_eff."""
    cur.execute("""
        SELECT source, meet, div,
               count(*)                                          AS n,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY e)   AS c,
               percentile_cont(0.75) WITHIN GROUP (ORDER BY e)
             - percentile_cont(0.25) WITHIN GROUP (ORDER BY e)   AS iqr,
               5000.0 * percentile_cont(0.5) WITHIN GROUP (ORDER BY dn) AS d_eff
        FROM _resid
        GROUP BY source, meet, div
        HAVING count(*) >= %s
    """, (min_field,))
    return cur.fetchall()


def _census(divisions):
    """Noise floor: (median|c|, p99|c|) so the threshold is chosen not guessed."""
    mags = sorted(abs(row[4]) for row in divisions)
    if not mags:
        return 0.0, 0.0
    return mags[len(mags) // 2], mags[min(len(mags) - 1, int(0.99 * len(mags)))]


# _resolveThreshold : decide the division flag cutoff. An explicit --c-threshold
#                     (c_thr is not None) always wins, so manual tuning still works.
#                     Otherwise auto-derive it from the census: sit it a margin above
#                     p99|c| so a flag is, by construction, outside the noise floor.
def _resolveThreshold(c_thr, p99):
    if c_thr is not None:
        return c_thr
    return p99 * _THRESHOLD_MARGIN


# ================================================================== #
# CHUNK 6 -- ROW SCAN  (relative % swing, division-corrected)
# ================================================================== #

# _rowRelCTE : the shared query core. For every rated row it builds
#   ind_ratio = this race's normalized 5K / the athlete's OWN bracket-median 5K
#   dmr       = the division's MEDIAN ind_ratio (its collective swing -- mud, heat)
#   rel       = ind_ratio / dmr  -> the runner's swing AFTER removing the field's.
# rel is the number every threshold is applied to: rel<1 faster than expected,
# rel>1 slower. Because the field's own swing is divided out, a genuinely slow race
# (whole field slow together) yields rel~1 for everyone and flags nobody. Returned
# as a CTE named `rel` so the scan and the sweep share one definition.
def _rowRelCTE():
    return """
        WITH ind AS (
            SELECT source, meet, div, result_id, ident, nt, e,
                   nt / NULLIF(nt_base, 0) AS ind_ratio
            FROM _resid
        ),
        divm AS (
            SELECT source, meet, div, count(*) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY e)         AS c,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY ind_ratio) AS dmr
            FROM ind GROUP BY source, meet, div
        ),
        rel AS (
            SELECT i.source, i.meet, i.div, i.result_id, i.ident, i.nt, i.e,
                   i.ind_ratio, d.n, i.e - d.c AS dev,
                   i.ind_ratio / NULLIF(d.dmr, 0) AS rel
            FROM ind i JOIN divm d USING (source, meet, div)
        )
    """


# _rowOutliers : rows crossing the fast/slow % lines or the absolute guard. No fixed
#   count -- EVERY crosser is counted (count(*) OVER () rides along as `total`), and
#   only the WRITE is bounded by `limit` so the .tsv stays sane. fast_pct/slow_pct
#   come in as fractions; 0.12 -> flag rel <= 0.88.
def _rowOutliers(cur, min_field, fast_pct, slow_pct, limit):
    cur.execute(_rowRelCTE() + """
        SELECT source, meet, div, result_id, ident, e, dev, nt, ind_ratio, rel,
               count(*) OVER () AS total
        FROM rel
        WHERE n >= %s
          AND (rel <= %s OR rel >= %s OR nt < %s)
        ORDER BY abs(ln(NULLIF(rel, 0))) DESC NULLS LAST
        LIMIT %s
    """, (min_field, 1 - fast_pct, 1 + slow_pct, _NT_GUARD, limit))
    return cur.fetchall()


# _sweepTable : the calibration diagnostic. One pass over `rel`, counting how many
#   rows cross each candidate % line, so the fast/slow cut is chosen by watching the
#   count move -- not guessed. Read straight off the already-built _resid.
def _sweepTable(cur, min_field):
    fast = ", ".join(f"count(*) FILTER (WHERE rel <= {1 - p:.4f}) AS f{int(p*100)}"
                     for p in _SWEEP_FAST)
    slow = ", ".join(f"count(*) FILTER (WHERE rel >= {1 + p:.4f}) AS s{int(p*100)}"
                     for p in _SWEEP_SLOW)
    cur.execute(_rowRelCTE() + f"""
        SELECT {fast}, {slow}, count(*) FILTER (WHERE nt < {_NT_GUARD}) AS guard
        FROM rel WHERE n >= %s
    """, (min_field,))
    row = cur.fetchone()
    fast_n = row[:len(_SWEEP_FAST)]
    slow_n = row[len(_SWEEP_FAST):len(_SWEEP_FAST) + len(_SWEEP_SLOW)]
    guard_n = row[-1]
    # Built as lines, PRINTED and RETURNED: the sweep is the deliverable of a
    # tightened run, so it must land in suspects_<sport>.txt too -- it was the
    # only run output that lived on the console alone, and the 2026-07-12
    # overnight lost its console (lesson recorded here).
    lines = ["",
             "  --sweep (rows that would flag at each % line):",
             "    faster: " + "  ".join(f"{int(p*100)}%={n}"
                                        for p, n in zip(_SWEEP_FAST, fast_n)),
             "    slower: " + "  ".join(f"{int(p*100)}%={n}"
                                        for p, n in zip(_SWEEP_SLOW, slow_n)),
             f"    guard (<{_NT_GUARD:.0f}s): {guard_n}"]
    print("\n".join(lines))
    return lines


# _classifyRow : which line a flagged row crossed (GUARD wins -- it is the only
#   absolute rule; then FAST; then SLOW). Every row the scan returns crossed one.
def _classifyRow(rel, nt, fast_pct, slow_pct):
    if nt is not None and nt < _NT_GUARD:
        return "GUARD"
    if rel is not None and rel <= 1 - fast_pct:
        return "FAST"
    if rel is not None and rel >= 1 + slow_pct:
        return "SLOW"
    return "SLOW"


# _safeDropRows : auto-drop FAST + GUARD (you cannot run much faster than yourself,
#   and nobody beats the guard); SLOW is looser and held back unless --drop-slow.
def _safeDropRows(rows, drop_slow):
    safe = {"FAST", "GUARD"} | ({"SLOW"} if drop_slow else set())
    return [r for r in rows if not r["already"] and r["proposed"] in safe]


# ================================================================== #
# CHUNK 7 -- SEASONS-RATIO ENRICHMENT (anet venue only)
# ================================================================== #

def _seasonOf(date_text):
    """Fall season of a text date, or None if uninterpretable."""
    if not date_text or len(date_text) < 4 or not date_text[:4].isdigit():
        return None
    y = int(date_text[:4])
    return y if _YEAR_LO <= y <= _YEAR_HI else None


def _venueAndSeason(cur, table, venue_table, anet_divs):
    """course_name (via meets, keyed by div_id) + a representative season, in one
    query, for a batch of anet flagged divisions."""
    if not anet_divs or venue_table is None:
        return {}
    t, v = _ident(table), _ident(venue_table)
    keys = ", ".join(f"({m},{d})" for (m, d) in anet_divs)
    cur.execute(f"""
        SELECT k.meet_id, k.div_id, m.course_name, min(r.date) AS a_date
        FROM (VALUES {keys}) AS k(meet_id, div_id)
        JOIN {t} r ON r.meet_id = k.meet_id AND r.div_id = k.div_id
                  AND r.source = 'anet'
        LEFT JOIN {v} m ON m.div_id = k.div_id
        GROUP BY k.meet_id, k.div_id, m.course_name
    """)
    out = {}
    for meet, div, course, a_date in cur.fetchall():
        out[(meet, div)] = (course, _seasonOf(a_date))
    return out


def _venueSeasonTotals(cur, table, venue_table, courses):
    """Distinct seasons each flagged venue ever hosted -- the denominator."""
    if not courses or venue_table is None:
        return {}
    t, v = _ident(table), _ident(venue_table)
    names = ", ".join("%s" for _ in courses)
    cur.execute(f"""
        SELECT m.course_name, r.date
        FROM {v} m JOIN {t} r ON r.div_id = m.div_id AND r.source = 'anet'
        WHERE m.course_name IN ({names})
    """, tuple(courses))
    seasons = {}
    for course, date_text in cur.fetchall():
        s = _seasonOf(date_text)
        if s is not None:
            seasons.setdefault(course, set()).add(s)
    return {c: len(ss) for c, ss in seasons.items()}


def _seasonsFrac(ven_season, totals):
    """seasons_flagged / seasons_total per division, per venue. Missing -> (None,None)."""
    flagged_by_course = {}
    for (meet, div), (course, season) in ven_season.items():
        if course is not None and season is not None:
            flagged_by_course.setdefault(course, set()).add(season)
    out = {}
    for (meet, div), (course, season) in ven_season.items():
        if course is None:
            out[(meet, div)] = (None, None)
        else:
            out[(meet, div)] = (len(flagged_by_course.get(course, set())),
                                totals.get(course))
    return out


# ================================================================== #
# CHUNK 8 -- CLASSIFICATION (propose, do not decide)
# ================================================================== #

def _pct(c):
    """log gap -> human percent."""
    return (math.exp(c) - 1.0) * 100.0


def _proposeDiv(already, c, flagged_seasons, total_seasons):
    """Triage hint, not a verdict. REGRESSION > PERSISTENT > TRANSIENT >
    INVESTIGATE. (SHORT-OOS retired: out-of-support short races are no longer
    pre-labelled, so they fall through to INVESTIGATE like anything else. d_eff is
    still emitted in the worksheet for the human to eyeball.)"""
    if already:
        return "REGRESSION"
    if (total_seasons is not None and total_seasons >= 3
            and flagged_seasons is not None
            and flagged_seasons / total_seasons >= 0.5):
        return "PERSISTENT"
    if (total_seasons is not None and flagged_seasons == 1
            and abs(_pct(c)) < 12.0):
        return "TRANSIENT"
    return "INVESTIGATE"


# ================================================================== #
# CHUNK 9 -- EVIDENCE DUMP (--dump)
# ================================================================== #

def _dumpEvidence(table, meet, div):
    """Each runner's this-race rating vs their own other-date median, so a flag
    can be eyeballed: a suspect is a spike against a runner's OWN history."""
    t = _ident(table)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            WITH here AS (
                SELECT COALESCE(r.person_id, r.athlete_id) AS ident,
                       r.speed_rating AS sr_here, r.date
                FROM {t} r
                WHERE r.meet_id = %s AND r.div_id = %s
                  AND r.speed_rating > 0
                  AND COALESCE(r.person_id, r.athlete_id) IS NOT NULL
            )
            SELECT h.ident, h.sr_here,
                   (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r2.speed_rating)
                    FROM {t} r2
                    WHERE COALESCE(r2.person_id, r2.athlete_id) = h.ident
                      AND r2.speed_rating > 0 AND r2.date <> h.date) AS own_med
            FROM here h ORDER BY h.sr_here DESC
        """, (meet, div))
        rows = cur.fetchall()
        print(f"\n=== meet {meet} div {div} : this race vs each runner's own "
              f"other-date median ===")
        print(f"  {'ident':>20} {'sr_here':>8} {'own_med':>8} {'gap%':>7}")
        spikes = 0
        for ident, sr_here, omed in rows:
            if omed:
                gap = (sr_here / omed - 1.0) * 100.0
                spikes += abs(gap) > 15
                print(f"  {str(ident):>20} {sr_here:>8.1f} {omed:>8.1f} {gap:>+7.1f}")
            else:
                print(f"  {str(ident):>20} {sr_here:>8.1f} {'--':>8} {'no br':>7}")
        print(f"\n  {spikes}/{len(rows)} runners spiked >15% vs their own history. "
              f"Whole field = label; a few = cooked rows; none = clean.")
        conn.rollback()


# ================================================================== #
# CHUNK 9b -- BASE-GATE AUDIT (--audit)
# ================================================================== #

# _auditBase
# Purpose : answer "why did 'rated rows with a bracket' change?" without hand-SQL.
#   `passes_base` is the exact population _base admits -- compare it to the run's
#   "rated rows" line. If passes_base already dropped, the loss is at a gate below;
#   the per-gate FILTER counts say WHICH one (unrated / no normalized_time / no
#   identity / bad date). If passes_base is steady but the run's number fell, the
#   loss is at the bracket stage instead (athlete date-sequences fragmented).
#   FILTER (WHERE ...) is Postgres conditional aggregation: count only the rows
#   matching that predicate, several tallies in a single scan. Read-only; rolls back.
def _auditBase(table):
    t = _ident(table)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT
              count(*)                                                          AS total,
              count(*) FILTER (WHERE speed_rating IS NULL OR speed_rating <= 0)  AS unrated,
              count(*) FILTER (WHERE normalized_time IS NULL)                    AS no_norm,
              count(*) FILTER (WHERE COALESCE(person_id, athlete_id) IS NULL)    AS no_ident,
              count(*) FILTER (WHERE date IS NULL
                                  OR substring(date, 1, 4) !~ '^[0-9]{{4}}$')    AS bad_date,
              count(*) FILTER (WHERE speed_rating > 0
                                 AND normalized_time IS NOT NULL
                                 AND COALESCE(person_id, athlete_id) IS NOT NULL
                                 AND date IS NOT NULL
                                 AND substring(date, 1, 4) ~ '^[0-9]{{4}}$'
                                 AND (substring(date, 1, 4))::int
                                       BETWEEN {_YEAR_LO} AND {_YEAR_HI})        AS passes_base
            FROM {t}
        """)
        total, unrated, no_norm, no_ident, bad_date, passes = cur.fetchone()
        conn.rollback()
    print(f"\n=== {table} base-gate audit (read-only) ===")
    print(f"  total rows            : {total:,}")
    print(f"  -- fails a gate --")
    print(f"  unrated (sr<=0/NULL)  : {unrated:,}")
    print(f"  normalized_time NULL  : {no_norm:,}")
    print(f"  no identity           : {no_ident:,}")
    print(f"  bad/missing date      : {bad_date:,}")
    print(f"  ------------------------")
    print(f"  passes ALL base gates : {passes:,}")
    print(f"  (compare to the run's 'rated rows with a bracket'; the shortfall")
    print(f"   below passes_base is what the >=3 bracket rule then removes)")


# ================================================================== #
# CHUNK 10 -- WORKSHEET EMISSION
# ================================================================== #

def _writeDivWorksheet(path, flagged):
    cols = ("kind", "source", "meet_id", "div_id", "n", "c_pct", "iqr_pct",
            "d_eff", "seasons_flagged", "seasons_total", "already", "proposed",
            "decision", "target")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# " + "\t".join(cols) + "\n")
        f.write("# decision <- KEEP/OVERRIDE/DROP/GENDER ; target <- metres or M/F\n")
        f.write("# c_pct = whole-field spike vs each runner's OWN bracket\n")
        for d in flagged:
            f.write("\t".join(str(x) for x in (
                "DIV", d["source"], d["meet"], d["div"], d["n"],
                f"{_pct(d['c']):+.1f}", f"{_pct(d['iqr']) if d['iqr'] else 0:.1f}",
                f"{d['d_eff']:.0f}" if d["d_eff"] is not None else "na",
                d["flagged_seasons"] if d["flagged_seasons"] is not None else "na",
                d["total_seasons"] if d["total_seasons"] is not None else "na",
                d["already"] or "-", d["proposed"], "", "")) + "\n")


def _writeRowWorksheet(path, rows):
    cols = ("kind", "source", "meet_id", "div_id", "result_id", "ident",
            "nt", "ind_ratio", "swing_pct", "already", "proposed", "decision")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# " + "\t".join(cols) + "\n")
        f.write("# nt = normalized 5K (s); ind_ratio = nt / athlete's bracket median\n")
        f.write("# swing_pct = division-corrected swing vs field-expected "
                "(+slower / -faster); this is what the fast/slow lines test\n")
        f.write("# proposed FAST/GUARD = safe drop; SLOW = looser, --drop-slow only\n")
        for r in rows:
            nt = f"{r['nt']:.0f}" if r.get("nt") is not None else "na"
            ind = f"{r['ind_ratio']:.2f}" if r.get("ind_ratio") is not None else "na"
            swing = (f"{(r['rel']-1)*100:+.0f}" if r.get("rel") is not None else "na")
            f.write("\t".join(str(x) for x in (
                "ROW", r["source"], r["meet"], r["div"], r["result_id"],
                r["ident"], nt, ind, swing,
                r["already"] or "-", r["proposed"], "")) + "\n")


def _writeHuman(path, sport, resid_n, med, p99, flagged, rows, thr):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"SUSPECT REPORT -- {sport}   (bracket baseline: "
                f"+/-{_BRACKET_SIDE} races, same-day excluded, >= {_BRACKET_MIN})\n")
        f.write("=" * 70 + "\n")
        f.write(f"rated rows with a bracket : {resid_n:,}\n")
        f.write(f"division noise floor      : median|c|={_pct(med):.2f}%  "
                f"p99|c|={_pct(p99):.2f}%\n")
        f.write(f"c-threshold in use        : {_pct(thr):.2f}%   "
                f"({'OUTSIDE noise -- good' if thr > p99 else 'INSIDE noise -- raise'})\n")
        f.write(f"flagged divisions         : {len(flagged)}\n")
        f.write(f"flagged rows              : {len(rows)}\n\n")
        f.write("WORST DIVISIONS (whole field spiked vs their own brackets)\n")
        f.write("-" * 70 + "\n")
        f.write(f"{'c%':>7} {'n':>5} {'d_eff':>6} {'seasons':>9} {'prop':>11}  "
                f"{'already':>8}  meet/div\n")
        for d in flagged[:40]:
            frac = (f"{d['flagged_seasons']}/{d['total_seasons']}"
                    if d["total_seasons"] is not None else "n/a")
            deff = f"{d['d_eff']:.0f}" if d["d_eff"] is not None else "na"
            f.write(f"{_pct(d['c']):+7.1f} {d['n']:>5} {deff:>6} {frac:>9} "
                    f"{d['proposed']:>11}  {(d['already'] or '-'):>8}  "
                    f"{d['meet']}/{d['div']}\n")
        f.write("\nWORST ROWS (one runner spiked vs their own bracket)\n")
        f.write("-" * 70 + "\n")
        f.write(f"{'dev%':>7} {'ident':>22}  meet/div  result_id\n")
        for r in rows[:40]:
            f.write(f"{_pct(r['dev']):+7.1f} {str(r['ident']):>22}  "
                    f"{r['meet']}/{r['div']}  {r['result_id']}\n")


# ================================================================== #
# CHUNK 11 -- ORCHESTRATION
# ================================================================== #

def _collectDivisions(cur, table, venue_table, corr, c_thr, min_field):
    raw = _divisionStats(cur, min_field)
    med, p99 = _census(raw)
    # None => auto-derive from the noise floor; an explicit value passes through.
    c_thr = _resolveThreshold(c_thr, p99)
    flagged_raw = [r for r in raw if abs(r[4]) >= c_thr]
    anet_divs = [(m, d) for (src, m, d, *_ ) in flagged_raw if src == "anet"]
    ven_season = _venueAndSeason(cur, table, venue_table, anet_divs)
    courses = {c for (c, s) in ven_season.values() if c is not None}
    totals = _venueSeasonTotals(cur, table, venue_table, list(courses))
    frac = _seasonsFrac(ven_season, totals)
    out = []
    for src, meet, div, n, c, iqr, d_eff in flagged_raw:
        fseas, tseas = frac.get((meet, div), (None, None))
        already = _alreadyTag(corr, meet, div)
        out.append({"source": src, "meet": meet, "div": div, "n": n, "c": c,
                    "iqr": iqr, "d_eff": d_eff, "flagged_seasons": fseas,
                    "total_seasons": tseas, "already": already,
                    "proposed": _proposeDiv(already, c, fseas, tseas)})
    out.sort(key=lambda d: abs(d["c"]), reverse=True)
    return out, med, p99, c_thr


def _collectRows(cur, corr, min_field, fast_pct, slow_pct, limit):
    raw = _rowOutliers(cur, min_field, fast_pct, slow_pct, limit)
    total = raw[0][-1] if raw else 0     # count(*) OVER () -- true crossers, pre-LIMIT
    out = []
    for src, meet, div, rid, ident, e, dev, nt, ind_ratio, rel, _tot in raw:
        already = "DROP" if rid in corr["res_drop"] else (
                  "OVERRIDE" if rid in corr["res_ov"] else "")
        tier = _classifyRow(rel, nt, fast_pct, slow_pct)
        out.append({"source": src, "meet": meet, "div": div, "result_id": rid,
                    "ident": ident, "e": e, "dev": dev, "nt": nt,
                    "ind_ratio": ind_ratio, "rel": rel, "already": already,
                    "proposed": "REGRESSION" if already else tier})
    return out, total


# _labelCounts : tally flagged divisions by their proposed label, for the summary.
#                dict.get(label, 0) + 1 counts without needing every key preset.
#                Reused for rows too (keys on d["proposed"], which is the row tier).
def _labelCounts(flagged):
    counts = {}
    for d in flagged:
        counts[d["proposed"]] = counts.get(d["proposed"], 0) + 1
    return counts


# _writeDropList : emit a corrections.py-ready block for the rows we auto-drop. Only
#                  FAST + GUARD (+ SLOW under --drop-slow) reach here, so each id
#                  crossed an implausibility line, not a soft statistical cutoff.
def _writeDropList(path, drops, unsafe=False):
    with open(path, "w", encoding="utf-8") as f:
        if unsafe:
            f.write("# !! ECHO-UNTESTED: generated below the default fast line.\n")
            f.write("# !! Do NOT merge -- run triage_suspects.py; corroboration\n")
            f.write("# !! issues the drop verdicts at this sensitivity.\n")
        f.write("# Auto-generated by diag_suspects.py -- implausible-swing DROPs.\n")
        f.write("# Each row swings too far from the athlete's own norm (field-\n")
        f.write("# corrected) to be a real race. Merge these ids into _RESULT_DROP.\n")
        f.write("_RESULT_DROP_ADDITIONS = {\n")
        for r in drops:
            nt = f"{r['nt']:.0f}" if r.get("nt") is not None else "na"
            swing = (f"{(r['rel']-1)*100:+.0f}%" if r.get("rel") is not None else "na")
            f.write(f"    {r['result_id']},  # {r['proposed']:<6} swing={swing} "
                    f"nt={nt}  meet/div {r['meet']}/{r['div']}\n")
        f.write("}\n")


def _run(sport, c_thr, fast_pct, slow_pct, min_field, row_limit, out_dir,
         corr_path, drop_slow):
    table, venue_table = _TABLE[sport], _VENUE_TABLE[sport]
    corr = _loadCorrections(corr_path, sport)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '2GB'")
        print("  building bracket residual (heavy pass) ...")
        resid_n = _buildResid(cur, table)
        print(f"  {resid_n:,} results earned a bracket residual")
        # c_thr comes back RESOLVED here: if it went in as None (auto), this is
        # the data-derived value, so the report and prints below show what was
        # actually used to flag.
        flagged, med, p99, c_thr = _collectDivisions(cur, table, venue_table,
                                                     corr, c_thr, min_field)
        rows, row_total = _collectRows(cur, corr, min_field,
                                       fast_pct, slow_pct, row_limit)
        sweep_lines = _sweepTable(cur, min_field)   # calibration, same _resid
        conn.rollback()
    os.makedirs(out_dir, exist_ok=True)
    div_path = os.path.join(out_dir, f"suspects_div_{sport.lower()}.tsv")
    row_path = os.path.join(out_dir, f"suspects_row_{sport.lower()}.tsv")
    txt_path = os.path.join(out_dir, f"suspects_{sport.lower()}.txt")
    # Below the default fast line the raw FAST+GUARD list is echo-UNTESTED:
    # at 6% it holds ~1.8M real-race ids and merging it would bias every
    # athlete history fast. The .txt extension + name keep it out of
    # apply_triage's whitelist AND out of a tired human's paste buffer.
    raw_unsafe = fast_pct < _ROW_FAST_PCT
    drop_name = (f"result_drop_{sport.lower()}.py" if not raw_unsafe else
                 f"UNSAFE_raw_fast_{int(fast_pct*100)}pct_{sport.lower()}.txt")
    drop_path = os.path.join(out_dir, drop_name)
    _writeDivWorksheet(div_path, flagged)
    _writeRowWorksheet(row_path, rows)
    _writeHuman(txt_path, sport, resid_n, med, p99, flagged, rows, c_thr)
    # Sweep appended to the report file: a lost console now costs nothing.
    with open(txt_path, "a", encoding="utf-8") as f:
        f.write("\n".join(sweep_lines) + "\n")
    drops = _safeDropRows(rows, drop_slow)
    _writeDropList(drop_path, drops, raw_unsafe)
    regressions = [d for d in flagged if d["proposed"] == "REGRESSION"]
    counts = _labelCounts(flagged)
    print(f"\n  census: median|c|={_pct(med):.2f}%  p99|c|={_pct(p99):.2f}%  "
          f"(threshold {_pct(c_thr):.2f}%)")
    if c_thr <= p99:
        print("  !! threshold is INSIDE the noise floor -- raise --c-threshold")
    # row_total is EVERY crosser (pre-limit); len(rows) is what was written.
    print(f"  flagged divisions: {len(flagged)}")
    print(f"  flagged rows: {row_total:,} over the "
          f"{int(fast_pct*100)}%/{int(slow_pct*100)}% lines "
          f"(wrote {len(rows):,}; raise --row-limit for more)")
    for label in ("INVESTIGATE", "PERSISTENT", "TRANSIENT", "REGRESSION"):
        if counts.get(label):
            print(f"    {label:<12} {counts[label]}")
    row_tiers = _labelCounts(rows)
    print(f"  written-row tiers:")
    for tier in ("GUARD", "FAST", "SLOW", "REGRESSION"):
        if row_tiers.get(tier):
            print(f"    {tier:<12} {row_tiers[tier]}")
    print(f"  -> {len(drops)} rows auto-dropped"
          f"{' (incl. SLOW)' if drop_slow else ' (SLOW held back; use --drop-slow)'}")
    if regressions:
        print(f"  !! {len(regressions)} ALREADY-CORRECTED divisions still flag:")
        for d in regressions[:5]:
            print(f"       meet {d['meet']} div {d['div']} ({d['already']}) "
                  f"c={_pct(d['c']):+.1f}%")
    print(f"\n  wrote:\n    {div_path}\n    {row_path}\n    {txt_path}\n    {drop_path}")
    # ! result_drop_<sport>.py is a REPORT now, not a verdict (2026-08-27,
    #   issue #23): apply_triage no longer merges it, because its rows never
    #   passed the echo court -- this file is what carried the July mass-drop
    #   waves. The applied lane is triage_suspects' echo-tested output.
    print("  result_drop_*.py is a report; run triage_suspects.py (echo "
          "court) and\n  apply_triage.py to convict. Re-run this to confirm "
          "corrections clear.")


# ================================================================== #
# CHUNK 12 -- CLI
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Post-convergence suspect detector, bracket baseline (read-only).")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--c-threshold", type=float, default=None,
                    help="log gap to flag a whole division; omit to auto-derive "
                         "from the noise floor (p99|c| * margin ~= 1.10)")
    ap.add_argument("--row-fast", type=float, default=_ROW_FAST_PCT,
                    help="flag a row this fraction FASTER than field-expected "
                         "(default 0.12 = 12%%)")
    ap.add_argument("--row-slow", type=float, default=_ROW_SLOW_PCT,
                    help="flag a row this fraction SLOWER than field-expected "
                         "(default 0.40 = 40%%)")
    ap.add_argument("--min-field", type=int, default=12,
                    help="skip divisions with fewer bracketed finishers")
    ap.add_argument("--row-limit", type=int, default=50000,
                    help="cap on rows WRITTEN (the printed count is always the true "
                         "total over the lines)")
    ap.add_argument("--out", default="scripts")
    ap.add_argument("--corrections", default=None)
    ap.add_argument("--dump", nargs=2, type=int, metavar=("MEET", "DIV"),
                    help="show each runner vs their own bracket, then exit")
    ap.add_argument("--audit", action="store_true",
                    help="read-only: report the base-gate row breakdown, then exit")
    ap.add_argument("--drop-slow", action="store_true",
                    help="also auto-drop the SLOW tier -- looser: may delete a real "
                         "catastrophic race")
    args = ap.parse_args()
    initPool()
    if args.audit:
        _auditBase(_TABLE[args.sport])
        return
    if args.dump:
        _dumpEvidence(_TABLE[args.sport], args.dump[0], args.dump[1])
        return
    _run(args.sport, args.c_threshold, args.row_fast, args.row_slow, args.min_field,
         args.row_limit, args.out, args.corrections, args.drop_slow)


if __name__ == "__main__":
    main()