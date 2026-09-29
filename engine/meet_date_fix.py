"""
meet_date_fix.py -- an anet meet stored under the wrong YEAR is put back in
its own year, in place, before anything reads a season off it.

    python engine/meet_date_fix.py                    # dry run: the report
    python engine/meet_date_fix.py --sport XC --show 80
    python engine/meet_date_fix.py --meet 227716      # one meet's line, both sports
    python engine/meet_date_fix.py --write            # record + apply (step 00_meet_dates)
    python engine/meet_date_fix.py --undo 227716 --sport XC   # put one back, for good

Issue: the first server run of engine/level_conflict.py (2026-09-29).

★ WHAT THE RUN FOUND. The NESCAC review's four collegians (Nick Walker /
  Bates, Jared Rife / Middlebury, Tyler Johnson / Trinity, Max Bennett /
  Conn) each carried an anet row at meet 227716 dated 2025-10-26, grades 11
  and 12, schools Winchester / Belmont / Watertown -- the Middlesex League.
  But anet hands out meet ids in the order meets are LISTED, and 227716 sits
  among 2023 listings: meet 228191 is 2023-08-26, meet 229092 is 2023-09-02,
  and the rows' own native ids (52163xxx) are 2023-era (native 52093608 is
  2023-10-28). It is their OWN 2023 high school race under a wrong year. The
  person was right; the DATE was wrong. level_conflict flagged the race off
  four real careers, and every reader that asks "which season is this" --
  season_year, grade_sanity, the pack, the boards, the athlete page -- asked
  it of 2025.

★ THE CLOCK THAT CATCHES IT: THE MEET ID. A meet is listed weeks or months
  before it is run, so the dates of the meets listed around it -- its id
  neighbours -- say when it ran, to within the normal lead time. The
  neighbourhood is the median date of the K = 200 nearest meet ids (100 each
  side, the meet itself left out). A median, so up to 100 bad dates in one
  window cannot move it; 200 meets is a few days of anet listings in season,
  so the window is narrow in time as well.

★ THE CUT IS MEASURED, NOT CHOSEN (owner: no arbitrary numbers). For every
  anet meet, dev = its date - its neighbourhood's. The NORMAL SPREAD is the
  99th percentile of |dev| over the whole sport: 99 meets in 100 sit inside
  it, and it is printed with p50 / p90 / p99.9 so the shape can be read. A
  wrong year moves a date by a whole year (365 days) or more. So the cut is
  HALFWAY between the edge of normal and the smallest wrong-year error:

      cut = (spread + 365.25) / 2

  -- a meet past it is nearer "a year off" than "listed early". And if the
  spread itself reaches half a year, no cut can separate the two (a normal
  meet would sit as far out as a wrong one): the sport is then reported
  and nothing in it is changed.

★ ONLY THE YEAR IS CORRECTED, AND ONLY WHEN THAT IS ALL THAT IS WRONG.
  The fix keeps the month and day and moves the year by k = round(dev /
  365.25): 2025-10-26 -> 2023-10-26. It is proposed only when the moved date
  lands INSIDE the normal spread of its neighbourhood -- the month-day is
  plausible and only the year was off. A date that no whole number of years
  can bring home (a 1900-01-01 placeholder, a day typed wrong too) is
  reported, never guessed.

⚠ AND THE ATHLETES MUST AGREE. A meet far from its id neighbours is not
  always a typo: a coach who uploads an old season's results gets a NEW id
  for an OLD meet, and that date is right. The athletes' grades tell the two
  apart. Each high schooler at the meet with a numeric grade has other anet
  rows, and those say which class they are in (class_of = season + 13 -
  grade, the mode over their other rows); their grade AT THIS MEET then says
  which season this meet was (season = class_of - 13 + grade). The fix is
  applied only when those votes are a strict majority for the corrected
  season and outnumber the stored one. No votes (nobody with a grade and a
  history) is no evidence, and is reported. For meet 227716 the vote is the
  Middlesex League's own juniors and seniors, whose other 2023 races put
  this one in 2023.

⚠ WHY IN PLACE, AND NOT AN OVERRIDE TABLE READ AT THE JOIN. The distance
  corrections live in corrections.py -> dist_override (engine/
  dump_overrides.py), which backfill_normalize joins: one reader, one join.
  A date has no such single reader. results.date / results_tf.date are read
  directly by season_year, grade_sanity, the linkers, twin_flag,
  level_conflict, the pack, the weather lookups, the boards and the pages --
  an override at the join would have to be threaded through every one, and
  the one that was missed would disagree with the rest. So the date is
  corrected where it is stored, the way link_tfrrs_rows stamps person_id,
  and made reversible the way that is: every fixed meet is a row of
  meet_date_fix (sport, meet_id, stored and fixed first/last day, the
  neighbourhood, the vote), and --undo puts a meet back and marks it
  'reverted' so no later run re-applies it.
  meets.meet_date (cross country) and meets_tf_meta.meet_date (track) are
  moved with the rows, so the meet's own date agrees with its results.

! IDEMPOTENT, AND IT SURVIVES A RE-SCRAPE. The fix moves only the rows still
  inside the stored [first, last] days; once moved they are outside it, so a
  second run moves nothing. A re-scrape that writes the wrong year back is
  found by the next --write: every 'applied' row of meet_date_fix is
  re-applied on every run, whether or not today's survey still sees the meet
  (it cannot -- its date is right now).

! STEP 00, BEFORE 01_season_year. Everything after it reads the season off
  results.date. It is NOT on the always-run list: a --from 7 run keeps the
  verdicts 01-04 made on last run's dates, and moving a date under them
  would make the pack disagree with grade_sanity and the twins about which
  season a row is in. A date fixed by a --from run's scrape waits for the
  next full run, with everything else about that meet.
"""
import argparse
import datetime
import os
import sys
import time
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from season_year import academicYear, seasonYearSqlInt       # noqa: E402

TABLES = {"XC": "results", "TF": "results_tf"}
AUDIT = "meet_date_fix"
K = 200                     # id neighbours per meet (the header says why)
YEAR = 365.25               # days a wrong year moves a date
SPREAD_Q = 0.99             # the normal spread: 99 meets in 100 inside it
_CHUNK = 20000              # meets per numpy block (20k x 200 int64 = 32 MB)
_DAY = "^[0-9]{4}-[0-9]{2}-[0-9]{2}"
_HS_GRADE = "^(9|10|11|12)$"


# ------------------------------------------------------------------ #
#  THE ARITHMETIC -- pure, tested without a database
# ------------------------------------------------------------------ #

def neighbourDays(days, k=K):
    """For days (ordinals) sorted by meet id: the median day of the k
    nearest ids, the meet itself left out. At the ends the window slides
    inward so every meet still has k neighbours (fewer only when the sport
    has fewer meets)."""
    import numpy as np
    d = np.asarray(days, dtype=np.int64)
    n = len(d)
    out = np.full(n, np.nan)
    if n < 2:
        return out
    w = min(int(k), n - 1)
    half = w // 2
    offs = np.arange(w + 1)
    for lo in range(0, n, _CHUNK):
        i = np.arange(lo, min(n, lo + _CHUNK))
        start = np.clip(i - half, 0, n - (w + 1))
        idx = start[:, None] + offs[None, :]
        # every window holds its own meet exactly once; drop it
        idx = idx[idx != i[:, None]].reshape(len(i), w)
        out[lo:lo + len(i)] = np.median(d[idx], axis=1)
    return out


def measure(devs):
    """The shape of |date - neighbourhood| over a sport, and the cut it
    implies: {'n', 'p50', 'p90', 'p99', 'p999', 'spread', 'cut', 'separable'}."""
    import numpy as np
    a = np.abs(np.asarray(devs, dtype=float))
    a = a[np.isfinite(a)]
    if not len(a):
        return {"n": 0, "separable": False}
    q = {name: float(np.quantile(a, p)) for name, p in
         (("p50", 0.5), ("p90", 0.9), ("p99", SPREAD_Q), ("p999", 0.999))}
    spread = q["p99"]
    return dict(n=int(len(a)), spread=spread, cut=(spread + YEAR) / 2.0,
                # a normal meet as far out as half a year leaves no room
                # between "listed early" and "a year off"
                separable=spread < YEAR / 2.0, **q)


def yearShift(dev, spread):
    """Whole years to SUBTRACT from the stored date, or None: only when
    moving by them lands the date inside the normal spread."""
    k = int(round(dev / YEAR))
    if k == 0 or abs(dev - k * YEAR) > spread:
        return None
    return k


def shiftDay(day, years):
    """'2025-10-26' moved by `years` (negative: back). Feb 29 into a year
    without one is Feb 28 -- what Postgres's date + interval does too."""
    d = datetime.date.fromisoformat(day)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:
        return d.replace(year=d.year + years, day=28).isoformat()


def survey(meets, k=K):
    """meets: [(meet_id, first_day, last_day, n_rows)] -> (shape, rows),
    rows [{meet_id, first, last, n, neighbour, dev, shift, fixed, fixed_last,
    reason}] for the meets past the cut. shift/fixed are None where the
    year cannot be corrected, and reason says why."""
    good = []
    for mid, first, last, n in meets:
        try:
            a = datetime.date.fromisoformat(str(first)[:10])
            b = datetime.date.fromisoformat(str(last)[:10])
        except ValueError:
            continue                            # not a date: nothing to place
        good.append((int(mid), a, b, int(n)))
    good.sort()
    nb = neighbourDays([a.toordinal() for _m, a, _b, _n in good], k)
    devs = [a.toordinal() - m for (_mid, a, _b, _n), m in zip(good, nb)]
    shape = measure(devs)
    out = []
    if not shape.get("n"):
        return shape, out
    for (mid, a, b, n), med, dev in zip(good, nb, devs):
        if abs(dev) <= shape["cut"]:
            continue
        row = dict(meet_id=mid, first=a.isoformat(), last=b.isoformat(), n=n,
                   neighbour=datetime.date.fromordinal(int(round(med))).isoformat(),
                   dev=int(round(dev)), shift=None, fixed=None, fixed_last=None,
                   reason=None)
        k_years = yearShift(dev, shape["spread"])
        if not shape["separable"]:
            row["reason"] = "the sport's spread leaves no cut"
        elif (b - a).days > shape["spread"]:
            row["reason"] = "its rows' dates disagree with each other"
        elif k_years is None:
            row["reason"] = "no whole number of years brings it home"
        else:
            row["shift"] = k_years
            row["fixed"] = shiftDay(row["first"], -k_years)
            row["fixed_last"] = shiftDay(row["last"], -k_years)
        out.append(row)
    return shape, out


def vote(implied, stored_season, fixed_season):
    """(apply?, reason, for_fixed, for_stored, n) from the seasons the
    athletes' grades put this meet in."""
    n = len(implied)
    for_fixed = sum(1 for s in implied if s == fixed_season)
    for_stored = sum(1 for s in implied if s == stored_season)
    if n == 0:
        return False, "no graded athlete with a history: no evidence", 0, 0, 0
    if for_fixed * 2 > n and for_fixed > for_stored:
        return True, "the grades agree", for_fixed, for_stored, n
    if for_stored >= for_fixed:
        return (False, "the grades say the stored year (an old season "
                "uploaded late?)", for_fixed, for_stored, n)
    return False, "the grades are split", for_fixed, for_stored, n


def seasonOf(day):
    return academicYear(datetime.date.fromisoformat(day))


# ------------------------------------------------------------------ #
#  THE DATABASE
# ------------------------------------------------------------------ #

def meetsSql(table):
    """One row per anet meet: its first and last day and its rows."""
    return f"""
        SELECT meet_id, min(substr(date, 1, 10)), max(substr(date, 1, 10)),
               count(*)
        FROM   {table}
        WHERE  source = 'anet' AND meet_id IS NOT NULL
          AND  date ~ '{_DAY}'
        GROUP  BY meet_id"""


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def _colType(cur, table, col):
    cur.execute("""SELECT data_type FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND table_name = %s AND column_name = %s""", (table, col))
    r = cur.fetchone()
    return r[0] if r else None


def _names(cur, sport, ids):
    """{meet_id: name} from the sport's meet table, where it has one."""
    if not ids:
        return {}
    table = "meets" if sport == "XC" else "meets_tf_meta"
    if not _hasTable(cur, table):
        return {}
    cur.execute(f"SELECT meet_id, min(meet_name) FROM {table} "
                f"WHERE meet_id = ANY(%s) GROUP BY meet_id", (list(ids),))
    return dict(cur.fetchall())


def votes(cur, sport, meet_ids, exclude):
    """{meet_id: [season the grades put it in]} -- one vote per graded high
    schooler at the meet who has other anet rows. exclude: {sport: [meet
    ids]} whose rows are not evidence (the meets under suspicion)."""
    if not meet_ids:
        return {}
    table = TABLES[sport]
    cur.execute("DROP TABLE IF EXISTS md_at")
    cur.execute(f"""
        CREATE TEMP TABLE md_at AS
        SELECT DISTINCT r.meet_id, r.person_id, btrim(r.grade)::int AS g
        FROM   {table} r
        WHERE  r.source = 'anet' AND r.meet_id = ANY(%s)
          AND  r.person_id IS NOT NULL
          AND  btrim(r.grade) ~ '{_HS_GRADE}'""", (list(meet_ids),))
    cur.execute("CREATE INDEX ON md_at (person_id)")
    cur.execute("ANALYZE md_at")
    acad = seasonYearSqlInt(None, "o.date")
    parts, params = [], []
    for sp, tb in TABLES.items():
        parts.append(f"""
            SELECT o.person_id, {acad} + 13 - btrim(o.grade)::int AS class_of
            FROM   {tb} o
            JOIN   (SELECT DISTINCT person_id FROM md_at) p
                   ON p.person_id = o.person_id
            WHERE  o.source = 'anet' AND btrim(o.grade) ~ '{_HS_GRADE}'
              AND  o.date ~ '{_DAY}'
              AND  NOT (o.meet_id = ANY(%s))""")
        params.append(list(exclude.get(sp, [])))
    cur.execute("DROP TABLE IF EXISTS md_class")
    cur.execute(f"""
        CREATE TEMP TABLE md_class AS
        SELECT person_id, mode() WITHIN GROUP (ORDER BY class_of) AS class_of
        FROM ({' UNION ALL '.join(parts)}) x
        GROUP  BY person_id""", params)
    cur.execute("""SELECT a.meet_id, c.class_of - 13 + a.g
                   FROM md_at a JOIN md_class c USING (person_id)""")
    out = defaultdict(list)
    for mid, season in cur.fetchall():
        out[int(mid)].append(int(season))
    return out


def examine(cur, sports=tuple(TABLES)):
    """{sport: (shape, rows)} -- the survey, every candidate voted on and
    named, and a verdict: row['apply'] with row['why']."""
    found = {}
    for sport in sports:
        t0 = time.time()
        cur.execute(meetsSql(TABLES[sport]))
        shape, rows = survey(cur.fetchall())
        found[sport] = (shape, rows)
        print(f"[dates] {sport}: {shape.get('n', 0):,} anet meets surveyed, "
              f"{len(rows):,} past the cut ({time.time() - t0:.0f}s)",
              flush=True)
    exclude = {sp: [r["meet_id"] for r in rows]
               for sp, (_s, rows) in found.items()}
    for sport, (shape, rows) in found.items():
        fixable = [r["meet_id"] for r in rows if r["fixed"]]
        by = votes(cur, sport, fixable, exclude)
        names = _names(cur, sport, [r["meet_id"] for r in rows])
        for r in rows:
            r["name"] = names.get(r["meet_id"]) or ""
            if not r["fixed"]:
                r.update(apply=False, why=r["reason"], for_fixed=0,
                         for_stored=0, voters=0)
                continue
            ok, why, ff, fs, n = vote(by.get(r["meet_id"], []),
                                      seasonOf(r["first"]), seasonOf(r["fixed"]))
            r.update(apply=ok, why=why, for_fixed=ff, for_stored=fs, voters=n)
    return found


def printReport(found, show=40, meet=None):
    for sport, (shape, rows) in found.items():
        print(f"\n[dates] {sport}")
        if not shape.get("n"):
            print("    no dated anet meets")
            continue
        print(f"    |date - id neighbourhood| over {shape['n']:,} meets: "
              f"p50 {shape['p50']:.0f}d  p90 {shape['p90']:.0f}d  "
              f"p99 {shape['p99']:.0f}d (the normal spread)  "
              f"p99.9 {shape['p999']:.0f}d")
        print(f"    cut = (spread + {YEAR}) / 2 = {shape['cut']:.0f} days"
              + ("" if shape["separable"] else
                 "   ! the spread reaches half a year: REPORT ONLY"))
        fix = [r for r in rows if r.get("apply")]
        print(f"    {len(rows):,} meets past the cut; {len(fix):,} to fix, "
              f"{sum(r['n'] for r in fix):,} rows")
        pick = [r for r in rows if meet is None or r["meet_id"] == int(meet)]
        pick.sort(key=lambda r: (not r.get("apply"), -r["n"]))
        if not pick:
            continue
        print(f"    {'meet':>9}  {'name':<34} {'stored':<10}  {'neighbours':<10}  "
              f"{'fixed':<10}  {'rows':>6}  {'grades fix/stored/n':<19}  verdict")
        for r in pick[:show]:
            g = (f"{r['for_fixed']}/{r['for_stored']}/{r['voters']}"
                 if r.get("voters") else "-")
            print(f"    {r['meet_id']:>9}  {r['name'][:34]:<34} {r['first']:<10}  "
                  f"{r['neighbour']:<10}  {r['fixed'] or '-':<10}  {r['n']:>6}  "
                  f"{g:<19}  {'FIX' if r.get('apply') else 'report'}: {r['why']}")
        if len(pick) > show:
            print(f"    ... and {len(pick) - show:,} more (--show)")


# ------------------------------------------------------------------ #
#  RECORD, APPLY, UNDO
# ------------------------------------------------------------------ #

def ensureAudit(cur):
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {AUDIT} (
            sport         text        NOT NULL,
            meet_id       bigint      NOT NULL,
            meet_name     text,
            stored_first  text        NOT NULL,
            stored_last   text        NOT NULL,
            fixed_first   text        NOT NULL,
            fixed_last    text        NOT NULL,
            years         int         NOT NULL,   -- added to the stored date
            neighbour_day text,
            voters        int,
            for_fixed     int,
            for_stored    int,
            n_rows        int,
            -- 'applied' (re-applied every run) or 'reverted' (--undo: the
            -- stored date stands and no run re-applies it)
            status        text        NOT NULL DEFAULT 'applied',
            found_at      timestamptz NOT NULL DEFAULT now(),
            changed_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (sport, meet_id))""")


def record(cur, found):
    """Add this survey's fixes to the audit table. A meet already there
    keeps its row: an 'applied' one is the same fix, a 'reverted' one was
    put back by hand and stays back."""
    ensureAudit(cur)
    n = 0
    for sport, (_shape, rows) in found.items():
        for r in rows:
            if not r.get("apply"):
                continue
            cur.execute(f"""
                INSERT INTO {AUDIT} (sport, meet_id, meet_name, stored_first,
                    stored_last, fixed_first, fixed_last, years, neighbour_day,
                    voters, for_fixed, for_stored, n_rows)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sport, meet_id) DO NOTHING""",
                (sport, r["meet_id"], r["name"], r["first"], r["last"],
                 r["fixed"], r["fixed_last"], -r["shift"], r["neighbour"],
                 r["voters"], r["for_fixed"], r["for_stored"], r["n"]))
            n += cur.rowcount
    cur.execute(f"SELECT sport, meet_id FROM {AUDIT} WHERE status = 'reverted'")
    kept = cur.fetchall()
    print(f"[dates] {n:,} new meets recorded in {AUDIT}"
          + (f"; {len(kept):,} reverted by hand stay as stored" if kept else ""))
    return n


def _moveSql(target, col, sport_filter, lo, hi, years, where_extra="",
             is_date=False):
    """UPDATE `target`.`col` by `years` for rows inside [lo, hi] of an audit
    row -- the one statement apply and undo both use. A text column keeps
    whatever follows the day (a time, if a feed wrote one); a date column
    (an older meets table) takes the date."""
    day = f"substr(t.{col}::text, 1, 10)::date + make_interval(years => {years})"
    value = (f"({day})::date" if is_date else
             f"to_char({day}, 'YYYY-MM-DD') || substr(t.{col}::text, 11)")
    return f"""
        UPDATE {target} t
        SET    {col} = {value}
        FROM   {AUDIT} f
        WHERE  f.sport = %s {sport_filter}
          AND  t.meet_id = f.meet_id {where_extra}
          AND  t.{col}::text ~ '{_DAY}'
          AND  substr(t.{col}::text, 1, 10) BETWEEN f.{lo} AND f.{hi}"""


def _meetTables(cur, sport):
    """[(table, extra WHERE, is_date)] holding the meet's own date, where
    the table and the column exist."""
    table, extra = (("meets", "") if sport == "XC"
                    else ("meets_tf_meta", "AND t.source = 'anet'"))
    if not _hasTable(cur, table):
        return []
    kind = _colType(cur, table, "meet_date")
    return [(table, extra, kind == "date")] if kind else []


def move(cur, sport, back=False, meet_id=None):
    """Apply every 'applied' audit row of the sport (or, back=True, undo one
    meet): its results rows and the meet's own date. Returns rows moved."""
    lo, hi, years = (("fixed_first", "fixed_last", "-f.years") if back
                     else ("stored_first", "stored_last", "f.years"))
    filt = "AND f.meet_id = %s" if back else "AND f.status = 'applied'"
    params = [sport] + ([meet_id] if back else [])
    cur.execute(_moveSql(TABLES[sport], "date", filt, lo, hi, years,
                         "AND t.source = 'anet'"), params)
    moved = cur.rowcount
    for table, extra, is_date in _meetTables(cur, sport):
        cur.execute(_moveSql(table, "meet_date", filt, lo, hi, years, extra,
                             is_date), params)
    return moved


def apply(conn):
    """Re-apply every recorded fix (idempotent), one transaction."""
    with conn.cursor() as cur:
        ensureAudit(cur)
        for sport in TABLES:
            t0 = time.time()
            moved = move(cur, sport)
            cur.execute(f"SELECT count(*) FROM {AUDIT} "
                        f"WHERE sport = %s AND status = 'applied'", (sport,))
            held = cur.fetchone()[0]
            print(f"[dates] {sport}: {held:,} meets on record, {moved:,} rows "
                  f"moved this run ({time.time() - t0:.0f}s)"
                  + ("" if moved else " -- already in their own year"),
                  flush=True)
    conn.commit()


def undo(conn, sport, meet_id):
    with conn.cursor() as cur:
        ensureAudit(cur)
        cur.execute(f"SELECT status, stored_first, fixed_first FROM {AUDIT} "
                    f"WHERE sport = %s AND meet_id = %s", (sport, meet_id))
        r = cur.fetchone()
        if not r:
            sys.exit(f"[dates] {sport} meet {meet_id} is not in {AUDIT}")
        moved = move(cur, sport, back=True, meet_id=meet_id)
        cur.execute(f"UPDATE {AUDIT} SET status = 'reverted', changed_at = now() "
                    f"WHERE sport = %s AND meet_id = %s", (sport, meet_id))
    conn.commit()
    print(f"[dates] {sport} meet {meet_id}: {moved:,} rows back to "
          f"{r[1]} (from {r[2]}); marked reverted, no run re-applies it")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", choices=tuple(TABLES))
    ap.add_argument("--show", type=int, default=40, help="meets to list per sport")
    ap.add_argument("--meet", type=int, help="list only this meet id")
    ap.add_argument("--write", action="store_true",
                    help="record this survey's fixes and apply every recorded one")
    ap.add_argument("--undo", type=int, metavar="MEET_ID",
                    help="put one fixed meet back (needs --sport)")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn:
        if a.undo is not None:
            if not a.sport:
                sys.exit("[dates] --undo needs --sport")
            undo(conn, a.sport, a.undo)
            return
        with conn.cursor() as cur:
            cur.execute("SET work_mem = '512MB'")
            sports = (a.sport,) if a.sport else tuple(TABLES)
            found = examine(cur, sports)
            printReport(found, a.show, a.meet)
            if a.write:
                record(cur, found)
        if a.write:
            conn.commit()
            apply(conn)
        else:
            conn.rollback()
            print("\n[dates] report only; --write records the FIX lines in "
                  f"{AUDIT} and moves their rows")


if __name__ == "__main__":
    main()
