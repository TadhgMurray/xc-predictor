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
  Their grades there are their 2023 grades: juniors and seniors in the
  autumn of 2023, collegians by 2025. It is their OWN 2023 high school race
  under a wrong year (its neighbours in id, meet 228191 of 2023-08-26 and
  229092 of 2023-09-02, and its rows' 2023-era native ids say the same). The
  person was right; the DATE was wrong. level_conflict flagged the race off
  four real careers, and every reader that asks "which season is this" --
  season_year, grade_sanity, the pack, the boards, the athlete page -- asked
  it of 2025.

⚠ THE MEET ID IS NOT A CLOCK (the owner's dry run of the first cut,
  2026-09-29). The first cut read a meet's year off the median date of its
  200 nearest meet ids. Over the whole corpus |date - id neighbourhood| was
      XC  p50 13d   p90 385d   p99 10,560d
      TF                       p99 15,292d
  -- every meet REPORT ONLY, nothing fixed. anet ids are not chronological:
  historic results are uploaded in bulk years later (the Sunfair
  Invitational of 1999 carries id 244560, among 2024 meets) and current
  meets are listed next to historic batches (First to the Finish 2023 has
  neighbours from 2004). Those stored dates are RIGHT. So the neighbourhood
  is gone, and the athletes are the clock.

★ THE CLOCK: THE ATHLETES' GRADES. Every athlete at an anet meet with a
  numeric grade (1-12) and other anet rows is a VOTER. Their other rows --
  every other meet of either sport -- say which class they are in:
  class_of = season + 13 - grade, the mode over those meets, the meet being
  voted on left out (a voter cannot vouch for their own meet). Their grade
  AT this meet then says which season it was: class_of - 13 + grade. A
  voter says "k years from the stored season", k = that season - the stored
  one. For meet 227716 the Middlesex League's own juniors and seniors, whose
  other races are 2023's, say k = -2. Every anet meet in both sports is
  voted on; one set-based pass (below) builds every vote at once.
  ! EVERY NUMERIC GRADE, NOT ONLY 9-12 (the owner's dry run, 2026-09-29).
    Middle and elementary schoolers move up a grade a year exactly as high
    schoolers do, so class_of = season + 13 - grade holds for them too. With
    9-12 only, an MS championship's voters were its two stray "grade 9"
    rows (Indiana MS XC Championships 232721, West TN ES-MS 234131, KLAA MS
    Conference 149615 -- each 2/0/2 or 3/0/3, a year forward); with 1-12
    the meet's own sixth to eighth graders vote, and outvote them.

★ THE GATE: THE VOTE MUST SPEAK FOR THE MEET (the owner's dry run,
  2026-09-29). A meet is moved only when MOST OF THE PEOPLE IN IT say it was
  another season: the winning voters must be a strict majority of the
  meet's DISTINCT anet athletes -- people, not rows (a track meet has many
  rows per athlete), graded or not. The dry run's FIX list held college
  meets moved by the two or three high schoolers in them: Texas A&M Arturo
  Barrios Invitational 219204 (2022 -> 2018, 2/0/2 over 707 rows), Texas
  Tech Red Raider Open 265212, SFA Lumberjack Opener 263014, NAIA Cascade
  Conference 10747, Parkside Invitational 22054 (3/0/3 over 1,554 rows);
  in track Messiah Invitational 653761 (2/0/2 over 1,950 rows), Larry
  Ellis 658623, MAAC Outdoor 665557. Two misgraded or misattributed people
  are not two independent witnesses to a year -- the minimum count below
  assumed they were. The gate needs no such model: 227716 is 528 of ~530
  athletes, Carlisle 64771 1,601, IHSAA Sectional 251830 177 of 177; a
  college meet with two high school voters is 2 of 700, and stays. A meet
  that fails it is listed, held, with its count of athletes.

★ THE VERDICT. A meet is a FIX when
    - its winning voters are a strict majority of its athletes (the gate),
    - it has at least MIN VOTERS (measured, next note),
    - a STRICT MAJORITY of them name the same season, and at least THE BAR
      of them (measured, two notes down),
    - that season is not the stored one (k != 0: a whole number of years,
      because seasons are), and
    - that majority outnumbers the voters for the stored season (implied by
      the strict majority, and checked anyway: it is the rule).
  A meet everyone agrees with keeps its date however far its id is from its
  neighbours: an old season uploaded late is voted into its own year by its
  own athletes. Too few voters, or a split, is no verdict, and the meet stays.

★ THE MINIMUM VOTER COUNT IS MEASURED, NOT CHOSEN (owner: no arbitrary
  numbers). A voter can be wrong on their own -- a grade typed wrong, a row
  on the wrong person -- and a meet is falsely moved when MOST of its voters
  are wrong the same way. So:
    1. THE NOISE: over every meet with two or more voters and one clear
       winner, the share of voters who disagree with their own meet's
       winner, by how many years (d). A meet-wide error -- a wrong year, the
       thing hunted -- moves every voter together and is not noise; this
       measures the voters who go their own way.
    2. THE CHANCE OF A FALSE FIX at a meet of n voters is the chance that a
       strict majority of them err by the same d: sum over d of
       P(Binomial(n, rate_d) > n/2) -- the d are exclusive, as only one
       season can hold a strict majority.
    3. THE MINIMUM is the smallest n at which the sport's own meets -- every
       meet with n or more voters, each at its own n -- EXPECT FEWER THAN
       ONE false FIX between them (EXPECTED_FALSE). The corpus sets it:
       noisier grades or more small meets raise it.
  It is printed with the noise and the expected count, so it can be read.
  It is derived at the weakest bar a verdict can have, a strict majority;
  the measured bar (next note) only ever asks for more, so the true
  expectation is lower still.
  ! THE MODEL TAKES VOTERS AS INDEPENDENT, AND THE DRY RUN SHOWED THEY ARE
    NOT ALWAYS: it put the minimum at 2, and two strays at a college meet
    err together. The gate above is what stops those; the minimum stays as
    the floor for small meets where the gate is easy (a dual meet of three
    runners, two of them voting), and a whole meet graded one way is the
    seam note.

★ THE BAR IS MEASURED TOO: A WRONG YEAR IS AS UNANIMOUS AS A RIGHT ONE.
  A strict majority is the floor -- the least share at which one season is
  the answer, and no other, the stored one included, can outvote it. But a
  meet stored under a wrong year is still ONE meet: its voters' grades are
  all from its true season, so they should agree with that season as well
  as a right-year meet's voters agree with theirs. A 52% / 48% meet is not
  that; it is a mix (two meets under one id, one school graded its own
  way), and moving it would put half its rows in the wrong year. So the bar
  is the 1st percentile of the share backing the stored season, over the
  meets that back it with a strict majority and have MIN VOTERS: 99
  right-year meets in 100 agree with themselves at least this well (the
  99-in-100 edge of normal the first cut used for its spread). It is never
  below a strict majority, and it is printed with the agreement quantiles.

⚠ THE SEASON SEAM: A ONE-YEAR VOTE CAN BE A CONVENTION, NOT A WRONG YEAR.
  Our season opens in August (season_year.ACADEMIC_START_MONTH). A feed
  whose season opens elsewhere -- a summer race graded for the coming
  school year -- has EVERY runner at the meet one grade "ahead", and the
  whole meet votes k = +1 (or -1 on the other side of the seam) as one. The
  voters agree, so neither the majority nor the minimum count can see it;
  but a convention belongs to the MONTH and a wrong year does not. So for
  each sport and stored month, k = -1 and k = +1 majorities are counted
  against the meets there (with at least MIN VOTERS), and a month is a
  CONVENTION for that k when its count is more than a typical month's rate
  can explain: P(Binomial(meets, typical rate) >= count) under
  EXPECTED_FALSE / 24 -- fewer than one of the sport's 24 month-and-
  direction cells so flagged by chance. The typical rate is the median over
  the months that hold an even share of the sport's meets or more (1/12:
  the quiet months are too small to say what is typical), each month's
  rate counted with one more meet voting k than it had (a month that saw
  none cannot make every one-year vote look strange). A one-year FIX in a
  CONVENTION cell is reported, not applied. A vote of two years or more is
  no season-seam artifact and is never held by this.

! AND TWO THINGS NO VOTE CAN MAKE RIGHT, reported, never applied: rows whose
  own dates already span two seasons (one shift cannot suit both), and a
  corrected date after today (a meet cannot have been run after it was
  scraped).

★ ONLY THE YEAR IS CORRECTED. The fix keeps the month and day and moves the
  year by k: 2025-10-26 -> 2023-10-26. The grades know the season, not the
  day; the day was never in doubt.

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
  vote), and --undo puts a meet back and marks it 'reverted' so no later run
  re-applies it.
  meets.meet_date (cross country) and meets_tf_meta.meet_date (track) are
  moved with the rows, so the meet's own date agrees with its results.

! IDEMPOTENT, AND IT SURVIVES A RE-SCRAPE. The fix moves only the rows still
  inside the stored [first, last] days; once moved they are outside it, so a
  second run moves nothing. A re-scrape that writes the wrong year back is
  found by the next --write: every 'applied' row of meet_date_fix is
  re-applied on every run, whether or not today's vote still sees the meet
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
import math
import os
import sys
import time
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from season_year import academicYear, seasonYearSqlInt       # noqa: E402

TABLES = {"XC": "results", "TF": "results_tf"}
AUDIT = "meet_date_fix"
EXPECTED_FALSE = 1.0        # false FIXes a sport may expect: fewer than one
CELLS = 24                  # a sport's month-and-direction cells (12 x +-1)
BAR_Q = 0.01                # the bar: 99 right-year meets in 100 agree this well
_DAY = "^[0-9]{4}-[0-9]{2}-[0-9]{2}"
_GRADES = "(" + ", ".join(f"'{g}'" for g in range(1, 13)) + ")"   # every numeric grade
_MONTHS = ("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec").split()


# ------------------------------------------------------------------ #
#  THE ARITHMETIC -- pure, tested without a database
# ------------------------------------------------------------------ #

def binomTail(n, p, x):
    """P(Binomial(n, p) >= x), summed in logs so a meet of thousands of
    voters neither overflows nor takes long: the terms past the mean fall
    geometrically, and the sum stops when they no longer move it."""
    if x <= 0:
        return 1.0
    if x > n or p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lp, lq, ln = math.log(p), math.log1p(-p), math.lgamma(n + 1)
    total = 0.0
    for i in range(x, n + 1):
        t = math.exp(ln - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                     + i * lp + (n - i) * lq)
        total += t
        if i > n * p and t <= total * 1e-17:
            break
    return min(total, 1.0)


def majority(n):
    """The fewest voters that are a strict majority of n."""
    return n // 2 + 1


def noiseRates(tallies):
    """({d: share}, voters measured): over meets with two or more voters and
    one clear winner, the share of voters d years off their meet's winner."""
    off, seen = Counter(), 0
    for t in tallies:
        n = sum(t.values())
        if n < 2:
            continue
        best = max(t.values())
        winners = [k for k, c in t.items() if c == best]
        if len(winners) != 1:
            continue                        # no winner to be off from
        seen += n
        for k, c in t.items():
            if k != winners[0]:
                off[k - winners[0]] += c
    return ({d: c / seen for d, c in off.items()} if seen else {}), seen


def falseFix(n, noise):
    """The chance that a strict majority of n independent voters err by the
    same number of years."""
    return sum(binomTail(n, r, majority(n)) for r in noise.values())


def minVoters(hist, noise):
    """(n_min, expected): the fewest voters at which the sport's meets --
    hist {voters: meets}, each at its own count -- expect fewer than
    EXPECTED_FALSE false FIXes between them, and that expectation."""
    total = 0.0
    for n in sorted(hist, reverse=True):
        step = hist[n] * falseFix(n, noise)
        if total + step >= EXPECTED_FALSE:
            return n + 1, total
        total += step
    return 1, total


def measuredBar(tallies, n_min):
    """The share a wrong-year majority must reach: the BAR_Q quantile of the
    share backing the stored season, over meets with n_min voters or more
    whose stored season holds a strict majority -- never below a strict
    majority itself (None with no such meet: then the floor stands)."""
    shares = sorted(t.get(0, 0) / n for t in tallies
                    for n in [sum(t.values())]
                    if n >= n_min and t.get(0, 0) >= majority(n))
    return quantile(shares, BAR_Q) if shares else None


def vote(tally, n_min, bar=None):
    """One meet's verdict from its tally {k: voters}: a dict with k (the
    majority's shift, or None), for_fixed, for_stored, voters, fix, why.
    bar: the share the majority must also reach (None: a strict majority)."""
    n = sum(tally.values())
    for_stored = tally.get(0, 0)
    out = dict(k=None, for_fixed=0, for_stored=for_stored, voters=n, fix=False)
    if n == 0:
        return dict(out, why="no graded athlete with a history: no evidence")
    k, top = max(tally.items(), key=lambda kv: kv[1])
    if top < majority(n):
        return dict(out, why="the grades are split")
    out.update(k=k, for_fixed=top)
    if k == 0:
        return dict(out, why="the grades say the stored year")
    if n < n_min:
        return dict(out, why=f"too few graded voters ({n} < {n_min})")
    if bar is not None and top < bar * n:
        return dict(out, why=f"a {100 * top / n:.0f}% majority, under a "
                             f"right-year meet's {100 * bar:.0f}%: a mix, "
                             f"not one wrong year")
    if top <= for_stored:                  # a strict majority cannot, but it is the rule
        return dict(out, why="the stored year holds its own")
    return dict(out, fix=True, why="the grades agree")


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    h = len(xs) // 2
    return xs[h] if len(xs) % 2 else (xs[h - 1] + xs[h]) / 2.0


def conventions(meets_by_month, one_year):
    """{(month, k): (count, meets, typical rate)} for the month-and-direction
    cells whose one-year majorities (one_year {(month, k): count}, k = +-1)
    are more than a typical month's rate can explain (the header says why)."""
    total = sum(meets_by_month.values())
    typical = [m for m, n in meets_by_month.items() if n * 12 >= total]
    out = {}
    for k in (-1, 1):
        rate = median((one_year.get((m, k), 0) + 1) / (meets_by_month[m] + 1)
                      for m in typical)
        if rate is None:
            continue
        for m, n in meets_by_month.items():
            x = one_year.get((m, k), 0)
            if x and binomTail(n, rate, x) < EXPECTED_FALSE / CELLS:
                out[(m, k)] = (x, n, rate)
    return out


def shiftDay(day, years):
    """'2025-10-26' moved by `years` (negative: back). Feb 29 into a year
    without one is Feb 28 -- what Postgres's date + interval does too."""
    d = datetime.date.fromisoformat(day)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:
        return d.replace(year=d.year + years, day=28).isoformat()


def seasonOf(day):
    return academicYear(datetime.date.fromisoformat(day))


def monthOf(day):
    return int(str(day)[5:7])


def quantile(xs, p):
    """xs sorted; the nearest-rank quantile -- a value some meet has, no
    interpolation (and no numpy)."""
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, max(0, int(math.ceil(p * len(xs))) - 1))]


def speaksForTheMeet(for_fixed, athletes):
    """The gate: the winning voters are a strict majority of the meet's
    distinct anet athletes."""
    return for_fixed >= majority(athletes)


def judge(meets, athletes_of=lambda ids: {}, today=None):
    """meets {meet_id: {'tally': {k: n}, 'first': day}} for one sport ->
    (shape, verdicts). Every meet is voted on; verdicts {meet_id: vote dict
    + 'athletes' + 'held' (a guard's reason, or None)} is the whole sport.
    athletes_of(ids) -> {meet_id: distinct anet athletes}, asked only for
    the meets the vote would move (the gate). The guards needing the meet's
    own rows (its dates, today) run later, in `finish`; the gate and the
    season seam run here, the gate first, so the seam counts only meets
    whose own people moved them."""
    tallies = {m: v["tally"] for m, v in meets.items()}
    noise, measured = noiseRates(tallies.values())
    hist = Counter(sum(t.values()) for t in tallies.values())
    n_min, expected = minVoters(hist, noise)
    bar = measuredBar(tallies.values(), n_min)
    verdicts = {m: vote(t, n_min, bar) for m, t in tallies.items()}
    for v in verdicts.values():
        v["held"], v["athletes"] = None, None
    # the gate: the vote must speak for the meet
    athletes = athletes_of([m for m, v in verdicts.items() if v["fix"]])
    for m, v in verdicts.items():
        if not v["fix"]:
            continue
        v["athletes"] = n = athletes.get(m, v["voters"])
        if not speaksForTheMeet(v["for_fixed"], n):
            v["held"] = (f"{v['for_fixed']} of {n:,} athletes: the vote is "
                         f"not the meet's")
    gated = sum(1 for v in verdicts.values() if v["fix"] and v["held"])
    # the season seam: one-year majorities by stored month
    by_month, one_year = Counter(), Counter()
    for m, v in verdicts.items():
        if v["voters"] < n_min:
            continue
        mon = monthOf(meets[m]["first"])
        by_month[mon] += 1
        if v["fix"] and not v["held"] and abs(v["k"]) == 1:
            one_year[(mon, v["k"])] += 1
    conv = conventions(by_month, one_year)
    for m, v in verdicts.items():
        if (v["fix"] and not v["held"]
                and (monthOf(meets[m]["first"]), v["k"]) in conv):
            v["held"] = (f"one-year votes are {_MONTHS[monthOf(meets[m]['first']) - 1]}'s "
                         f"convention, not a wrong year")
    enough = [v for v in verdicts.values() if v["voters"] >= n_min]
    shape = dict(
        meets=len(meets), voters=sum(hist[n] * n for n in hist),
        per_meet=sorted(n for n in hist.elements()),
        noise=noise, measured=measured, n_min=n_min, expected=expected,
        bar=bar, gated=gated,
        enough=len(enough),
        agree=sorted(v["for_stored"] / v["voters"] for v in enough),
        stored=sum(1 for v in enough if v["k"] == 0),
        other=sum(1 for v in enough if v["k"] not in (None, 0)),
        split=sum(1 for v in enough if v["k"] is None),
        # a majority elsewhere, held back only by the minimum
        few=sum(1 for v in verdicts.values()
                if v["k"] not in (None, 0) and v["voters"] < n_min),
        by_month=dict(by_month), one_year=dict(one_year), conv=conv,
        today=today or datetime.date.today().isoformat())
    return shape, verdicts


def finish(row, today):
    """The corrected days, and the guards a vote cannot answer."""
    row["fixed"] = shiftDay(row["first"], row["k"])
    row["fixed_last"] = shiftDay(row["last"], row["k"])
    if row["held"]:
        return row
    if seasonOf(row["first"]) != seasonOf(row["last"]):
        row["held"] = "its rows' dates span two seasons"
    elif row["fixed_last"] > today:
        row["held"] = "the corrected date is in the future"
    return row


# ------------------------------------------------------------------ #
#  THE DATABASE
# ------------------------------------------------------------------ #

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


def _timed(cur, label, sql, params=None):
    t0 = time.time()
    cur.execute(sql, params)
    print(f"[dates]   {label} ({time.time() - t0:.0f}s)", flush=True)


def buildVotes(cur, sports=tuple(TABLES)):
    """{sport: {meet_id: {'tally': {k: voters}, 'first'}}} for every
    anet meet of `sports` with a voter. Set-based: two temp tables and one
    grouped join, each timed.

      md_pm   one row per (sport, meet, person) for every anet athlete with
              a numeric grade (1-12), with the class year that meet's grade and date imply
              (a person graded two ways at one meet is no voter there). Both
              sports always: a runner's track rows vouch for their cross
              country class and the reverse.
      md_top  per person, the three classes most of their meets imply, with
              their counts -- enough to take the mode with any one meet left
              out: only that meet's own class loses one.
      tally   md_pm joined to md_top: each voter's class from their OTHER
              meets (a unique mode, or no vote), minus the class this meet
              implies = k; counted per (sport, meet, k).
    """
    acad = seasonYearSqlInt(None, "date")
    parts = [f"""
        SELECT '{sp}'::text AS sport, meet_id, person_id,
               {acad} + 13 - btrim(grade)::int AS class_of,
               (substr(date, 1, 4) || substr(date, 6, 2)
                || substr(date, 9, 2))::int AS day
        FROM   {tb}
        WHERE  source = 'anet' AND meet_id IS NOT NULL
          AND  person_id IS NOT NULL
          AND  btrim(grade) IN {_GRADES} AND date ~ '{_DAY}'"""
             for sp, tb in TABLES.items()]
    # ! THE DAY IS AN INTEGER, YYYYMMDD, and only the first is kept: min()
    #   over text in the database's collation cost a sixth of this step on
    #   a 17.6M-row test corpus, and only the meet's month is read from it
    #   (the meets a fix moves get their real first and last day from all
    #   their rows, in meetRows).
    cur.execute("DROP TABLE IF EXISTS md_pm, md_top")
    _timed(cur, "md_pm: every graded anet athlete per meet", f"""
        CREATE TEMP TABLE md_pm AS
        SELECT sport, meet_id, person_id, min(class_of) AS class_of,
               min(day) AS d_first
        FROM   ({' UNION ALL '.join(parts)}) x
        GROUP  BY sport, meet_id, person_id
        HAVING min(class_of) = max(class_of)""")
    _timed(cur, "md_top: each runner's three likeliest classes", """
        CREATE TEMP TABLE md_top AS
        SELECT person_id,
               max(class_of) FILTER (WHERE rk = 1) AS c1,
               max(n)        FILTER (WHERE rk = 1) AS n1,
               max(class_of) FILTER (WHERE rk = 2) AS c2,
               coalesce(max(n) FILTER (WHERE rk = 2), 0) AS n2,
               coalesce(max(n) FILTER (WHERE rk = 3), 0) AS n3
        FROM  (SELECT person_id, class_of, n,
                      row_number() OVER (PARTITION BY person_id
                                         ORDER BY n DESC, class_of) AS rk
               FROM  (SELECT person_id, class_of, count(*) AS n
                      FROM md_pm GROUP BY person_id, class_of) c) r
        WHERE  rk <= 3
        GROUP  BY person_id""")
    cur.execute("ANALYZE md_pm")
    cur.execute("ANALYZE md_top")
    # The mode of the person's OTHER meets. Leaving this meet out takes one
    # from its own class only, so:
    #   its class is the top one:    top minus one still beats the second,
    #                                or the second now beats it and the
    #                                third; a tie is no vote
    #   its class is the second:     the top wins if it beats the third
    #   its class is lower:          the top wins if it beats the second
    # No other meets at all leaves every count at zero: no vote.
    other = """
        CASE WHEN m.class_of = t.c1 THEN
                  CASE WHEN t.n1 - 1 > t.n2 THEN t.c1
                       WHEN t.n2 > t.n1 - 1 AND t.n2 > t.n3 THEN t.c2 END
             WHEN m.class_of = t.c2 THEN
                  CASE WHEN t.n1 > t.n3 THEN t.c1 END
             ELSE CASE WHEN t.n1 > t.n2 THEN t.c1 END
        END"""
    t0 = time.time()
    cur.execute(f"""
        SELECT m.sport, m.meet_id, v.k, count(*), min(m.d_first)
        FROM   md_pm m
        JOIN   md_top t USING (person_id)
        CROSS  JOIN LATERAL (SELECT {other} - m.class_of AS k) v
        WHERE  v.k IS NOT NULL AND m.sport = ANY(%s)
        GROUP  BY m.sport, m.meet_id, v.k""", (list(sports),))
    out = {sp: {} for sp in sports}
    for sport, mid, k, c, first in cur.fetchall():
        m = out[sport].setdefault(int(mid), {"tally": {}, "first": first})
        m["tally"][int(k)] = int(c)
        m["first"] = min(m["first"], first)
    for meets in out.values():
        for m in meets.values():
            d = m["first"]
            m["first"] = f"{d // 10000:04d}-{d // 100 % 100:02d}-{d % 100:02d}"
    print("[dates]   the vote: "
          + ", ".join(f"{sp} {len(v):,} meets" for sp, v in out.items())
          + f" ({time.time() - t0:.0f}s)", flush=True)
    return out


def meetRows(cur, sport, ids):
    """{meet_id: (first, last, n, athletes)} over ALL the meet's anet rows --
    graded or not, the rows a fix moves -- for the meets the vote would move
    (by meet_id, on its index). athletes is PEOPLE, not rows: distinct
    person_id, and a row with none counts by its athlete_id where the table
    has one, else as one athlete of its own (the gate's error then leans to
    holding a meet, never to moving it)."""
    if not ids:
        return {}
    table = TABLES[sport]
    unlinked = ("count(DISTINCT athlete_id) FILTER (WHERE person_id IS NULL)"
                if _colType(cur, table, "athlete_id") else
                "count(*) FILTER (WHERE person_id IS NULL)")
    t0 = time.time()
    cur.execute(f"""
        SELECT meet_id, min(substr(date, 1, 10)), max(substr(date, 1, 10)),
               count(*), count(DISTINCT person_id) + {unlinked}
        FROM   {table}
        WHERE  source = 'anet' AND meet_id = ANY(%s) AND date ~ '{_DAY}'
        GROUP  BY meet_id""", (list(ids),))
    out = {int(m): (a, b, int(n), int(p)) for m, a, b, n, p in cur.fetchall()}
    print(f"[dates]   {sport}: {len(out):,} meets' own rows and athletes "
          f"({time.time() - t0:.0f}s)", flush=True)
    return out


def examine(cur, sports=tuple(TABLES), meet=None):
    """{sport: (shape, rows)} -- every anet meet voted on; rows are the ones
    a majority moves (FIX, or held by a guard), plus `meet` if asked for,
    each with its verdict: row['apply'] with row['why']."""
    t0 = time.time()
    print("[dates] the grade vote, both sports' rows:", flush=True)
    everything = buildVotes(cur, sports)
    found = {}
    for sport in sports:
        meets = everything[sport]
        facts = {}

        def athletes_of(ids, sport=sport, facts=facts):
            facts.update(meetRows(cur, sport, ids))
            return {m: f[3] for m, f in facts.items()}

        shape, verdicts = judge(meets, athletes_of)
        pick = [m for m, v in verdicts.items() if v["fix"]]
        if meet is not None and int(meet) in verdicts and int(meet) not in pick:
            pick.append(int(meet))
            facts.update(meetRows(cur, sport, [int(meet)]))
        names = _names(cur, sport, pick)
        rows = []
        for m in pick:
            v = verdicts[m]
            first, last, n, people = facts.get(m, (meets[m]["first"],) * 2 + (0, 0))
            r = dict(v, meet_id=m, name=names.get(m) or "", first=first,
                     last=last, n=n, fixed=None, fixed_last=None)
            r["athletes"] = r["athletes"] or people
            if r["fix"]:
                finish(r, shape["today"])
            r["apply"] = bool(r["fix"] and not r["held"])
            if r["held"]:
                r["why"] = r["held"]
            rows.append(r)
        if meet is not None and int(meet) not in verdicts:
            shape["missing"] = int(meet)
        found[sport] = (shape, rows)
    print(f"[dates] voted ({time.time() - t0:.0f}s)", flush=True)
    return found


def _pct(x):
    return f"{100 * x:.0f}%"


def printReport(found, show=40, meet=None):
    for sport, (shape, rows) in found.items():
        print(f"\n[dates] {sport}")
        if not shape.get("meets"):
            print("    no anet meet with a graded voter")
            continue
        pm = shape["per_meet"]
        print(f"    {shape['voters']:,} votes over {shape['meets']:,} meets; "
              f"voters per meet p50 {quantile(pm, .5)}  p90 {quantile(pm, .9)}  "
              f"p99 {quantile(pm, .99)}")
        top = sorted(shape["noise"].items(), key=lambda kv: -kv[1])[:6]
        print(f"    the noise, voters off their meet's winner by d years "
              f"({shape['measured']:,} voters measured): "
              + ("  ".join(f"d={d:+d} {100 * r:.2f}%" for d, r in top)
                 or "none"))
        print(f"    minimum voters = {shape['n_min']}: the fewest at which the "
              f"sport's meets expect under {EXPECTED_FALSE:g} false FIX "
              f"({shape['expected']:.2f} expected over {shape['enough']:,} meets)")
        ag = shape["agree"]
        if ag:
            print(f"    share agreeing with the stored season, meets with "
                  f">= {shape['n_min']} voters: p1 {_pct(quantile(ag, .01))}  "
                  f"p5 {_pct(quantile(ag, .05))}  p10 {_pct(quantile(ag, .1))}  "
                  f"p50 {_pct(quantile(ag, .5))}")
            print("    the bar = "
                  + (f"{_pct(shape['bar'])}: p1 of the share backing the stored "
                     f"season where it holds a majority -- 99 right-year meets "
                     f"in 100 agree at least this well" if shape["bar"] is not None
                     else "a strict majority (no meet backs its stored season)"))
            print(f"    majority for the stored season {shape['stored']:,} | "
                  f"for another season {shape['other']:,} | split "
                  f"{shape['split']:,}   (and {shape['few']:,} meets under "
                  f"{shape['n_min']} voters with a majority for another season)")
        if shape["by_month"]:
            print("    one-year majorities by stored month (k=-1 / k=+1 of meets "
                  f"with >= {shape['n_min']} voters):")
            for mon in sorted(shape["by_month"]):
                n = shape["by_month"][mon]
                lo = shape["one_year"].get((mon, -1), 0)
                hi = shape["one_year"].get((mon, 1), 0)
                flag = [f"{k:+d} is a convention (typical {100 * c[2]:.3f}%)"
                        for k in (-1, 1) for c in [shape["conv"].get((mon, k))]
                        if c]
                print(f"      {_MONTHS[mon - 1]}  {lo:>6,} / {hi:>6,}  of {n:>9,}"
                      + ("   ⚠ " + "; ".join(flag) if flag else ""))
        fix = [r for r in rows if r.get("apply")]
        moved = [r for r in rows if r.get("fix")]
        shares = sorted(r["for_fixed"] / r["voters"] for r in moved)
        print(f"    {len(moved):,} meets voted into another season past the bar; "
              f"{len(fix):,} to fix, {sum(r['n'] for r in fix):,} rows; "
              f"{len(moved) - len(fix):,} held ({shape['gated']:,} by the gate: "
              f"not a majority of the meet's athletes)"
              + (f"; the winners' share p1 {_pct(quantile(shares, .01))}  "
                 f"p10 {_pct(quantile(shares, .1))}  p50 {_pct(quantile(shares, .5))}"
                 if shares else ""))
        if meet is not None and shape.get("missing") == int(meet):
            print(f"    meet {meet}: no graded voter")
        pick = [r for r in rows if meet is None or r["meet_id"] == int(meet)]
        # FIX first, then what another guard held, then what the gate held
        pick.sort(key=lambda r: (not r.get("apply"), not r.get("fix"),
                                 not speaksForTheMeet(r["for_fixed"], r["athletes"]),
                                 -r["n"]))
        if not pick:
            continue
        print(f"    {'meet':>9}  {'name':<34} {'stored':<10}  {'k':>3}  "
              f"{'fixed':<10}  {'rows':>6}  {'athletes':>8}  "
              f"{'grades fix/stored/n':<19}  verdict")
        for r in pick[:show]:
            g = f"{r['for_fixed']}/{r['for_stored']}/{r['voters']}"
            k = f"{r['k']:+d}" if r["k"] else "-"
            print(f"    {r['meet_id']:>9}  {r['name'][:34]:<34} {r['first']:<10}  "
                  f"{k:>3}  {r['fixed'] or '-':<10}  {r['n']:>6}  "
                  f"{r['athletes']:>8,}  "
                  f"{g:<19}  {'FIX' if r.get('apply') else 'report'}: {r['why']}")
        if len(pick) > show:
            print(f"    ... and {len(pick) - show:,} more (--show)")


# ------------------------------------------------------------------ #
#  RECORD, APPLY, UNDO
# ------------------------------------------------------------------ #

def ensureAudit(cur):
    # ! A table made by the first cut also has neighbour_day (the id
    #   neighbourhood's median). Nothing writes or reads it now; every
    #   statement here names its columns, so the old table serves, with
    #   n_athletes (the gate, 2026-09-29) added to it below.
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
    # the gate's denominator: distinct anet athletes at the meet
    cur.execute(f"ALTER TABLE {AUDIT} ADD COLUMN IF NOT EXISTS n_athletes int")


def record(cur, found):
    """Add this vote's fixes to the audit table. A meet already there
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
                    stored_last, fixed_first, fixed_last, years,
                    voters, for_fixed, for_stored, n_rows, n_athletes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sport, meet_id) DO NOTHING""",
                (sport, r["meet_id"], r["name"], r["first"], r["last"],
                 r["fixed"], r["fixed_last"], r["k"],
                 r["voters"], r["for_fixed"], r["for_stored"], r["n"],
                 r["athletes"]))
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
                    help="record this vote's fixes and apply every recorded one")
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
            found = examine(cur, sports, a.meet)
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
