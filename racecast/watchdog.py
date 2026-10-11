#!/usr/bin/env python3
"""
watchdog.py -- the night's data, checked once it is on the site. REPORT ONLY.

    /srv/venv/bin/python racecast/watchdog.py --log-dir logs/nightly_<ts> --kind nightly --send
    /srv/venv/bin/python racecast/watchdog.py --dry-run                 # print, write nothing
    /srv/venv/bin/python racecast/watchdog.py --classify-10a logs/<run> # one 10a log's cause

★ WHY (owner, 2026-10-10: a nightly data-quality watchdog, approved). The
  nightly update scrapes, links, prices and publishes without a human
  reading anything; the full pipeline's own guards (06c impossible races,
  09d outliers, 10a board sanity) do not run at night at all. This reads
  WHAT CHANGED SINCE ITS LAST RUN -- rows scraped since then, rows a linker
  moved since then, and the items it already has open -- and writes a short
  report with a link to every page involved:

    1 performances  faster than the world record allows (record_pace, the
                    one definition, judged exactly as impossible_race.judge
                    judges it), or outside the engine's pace band for the
                    row's pool (normalize_distance.poolBandFor), and the
                    breakouts page's own "check" PRs (breakouts.CHECK_GAIN)
    2 jumps         a race above the athlete's neighbouring races by more
                    than rating_outliers' measured fast cut (its last
                    calibration: K, floor, cut), or above the median of the
                    earlier races this season by breakouts.CHECK_JUMP
    3 duplicates    two persons with one normalised name at one school in
                    one season that link_profile_school.decideGroup would
                    join; one finish (place and time to the tenth, the
                    result_twin rule) under two ids that result_twin missed
    4 careers       a career a linker moved rows into tonight that fails
                    link_profile_school's own checks on itself: grades two
                    generations apart (CLASS_SLACK / COLLEGE_DRIFT), more
                    seasons at one school than one athlete can have
                    (MAX_SEASONS_AT_ONE_SCHOOL), two cross country races
                    on one day
    5 meets         a race with no distance (or -1), every finisher on one
                    time, places running past the rows that landed, both
                    sexes in one division
    6 boards        the top of each board against the previous run's top:
                    churn past what this kind of run measured before
    7 pipeline      failed, not-run and missing steps; table sizes and the
                    night's row counts against their own history; and
                    10a_board_sanity's CAUSE CLASS when it fails (the open
                    item of run 20261006_120609)

! REPORT ONLY -- NOTHING IS FIXED (owner's rule: wrong-person detection is
  report-only; nothing auto-fixes data). The only writes are this module's
  own tables (watchdog_*), and the BRIN indexes --ensure-indexes builds.

★ EACH NIGHT'S FINDINGS ARE KEPT. watchdog_finding has one row per item:
  first_seen, last_seen, still_open, resolved, nights. An open item is
  checked again every night (its result ids, race or persons join the
  scope), so it resolves when the data is fixed, not when it scrolls out.
  The report leads with the NEW items and says how long the old ones have
  been open.

★ NO THRESHOLD HERE IS PICKED. Each one is imported from the module that
  owns it, or measured from this table's own history -- see the constants
  below; the only literal is the owner's own "5 sigma" (rating_outliers.
  OWNER_RANGE[0]).

★ CHEAP: what changed tonight is found through BRIN indexes on
  results(scraped_at), results_tf(scraped_at) and person_link_log
  (linked_at) -- a few kilobytes each, built once (--ensure-indexes, and
  scripts/add_page_indexes.py's list). Everything else is a primary-key,
  person, school or meet index lookup on the rows that scope found.
  Without the index the row checks are SKIPPED and the report says so; a
  60M-row scan is never the fallback.

Mail: the summary goes into the pipeline's failure email
(scripts/notify_owner.py reads $LOGDIR/WATCHDOG.txt). On a night with no
failed step, --send mails the admins (XCP_ADMIN_EMAILS) itself, only when
there are NEW items. XCP_NOTIFY=0 / XCP_WATCHDOG_MAIL=0 turn it off.
"""
import argparse
import datetime
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict, namedtuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import breakouts as BO                                           # noqa: E402
import impossible_race as IR                                     # noqa: E402
import link_profile_school as LPS                                # noqa: E402
import rating_outliers as RO                                     # noqa: E402
from link_freshmen import normName                               # noqa: E402
from link_teamless import CLASS_SLACK                            # noqa: E402
from season_floor import DEFAULT_FLOOR, floorSql                 # noqa: E402

LOG_ROOT = os.environ.get("XCP_LOG_ROOT") or os.path.join(_ROOT, "logs")
TABLES = {"XC": "results", "TF": "results_tf"}

# ---------------------------------------------------------------------- #
# THE THRESHOLDS, AND WHERE EACH ONE COMES FROM (2026-10-10)
# ---------------------------------------------------------------------- #
# ★ "UNUSUAL" FOR A NUMBER WITH A HISTORY (board churn, row counts): the
#   rating_outliers rule -- median and MAD of the history (neighbourZ), on
#   at least MIN_NEIGHBOURS past values ("three is the fewest a median can
#   survive one bad value in"), past the owner's own lowest sigma for "not
#   a performance" (OWNER_RANGE[0] = 5, 2026-09-18). Fewer past values: the
#   number is recorded and nothing is judged.
UNUSUAL_SIGMA = RO.OWNER_RANGE[0]
MIN_HISTORY = RO.MIN_NEIGHBOURS
# ★ BOARD TOP-N: the rows 10a_board_sanity reads per board (its --top, the
#   pipeline's XCP_SANITY_TOP, default 60) -- the part of a board a person
#   looks at, and the part the pipeline already vouches for.
BOARD_TOP = int(os.environ.get("XCP_SANITY_TOP") or 60)
# the boards: breakouts.LEVELS' six pools, both sports, the current season
BOARD_POOLS = tuple(p for p, _w in BO.LEVELS.values())
# ★ THE SEASON JUMP: breakouts' own rule and bar (MIN_PRIOR earlier races
#   this season; CHECK_JUMP = build_ranking_results._SEASON_OUTLIER_PTS, the
#   jump the breakouts page marks "check"); and its PR bar CHECK_GAIN.
SEASON_MIN_PRIOR = BO.MIN_PRIOR
SEASON_CHECK_JUMP = BO.CHECK_JUMP
# ★ TWO CAREERS' YEARS AND GENERATIONS: link_profile_school's (derived there).
MAX_SEASONS = LPS.MAX_SEASONS_AT_ONE_SCHOOL
COLLEGE_DRIFT = LPS.COLLEGE_DRIFT
# ! IDENTICAL TIMES: a field of MIN_NEIGHBOURS or more all on one time to
#   the tenth (the twin rule's resolution). Two runners can tie; three on
#   one clock reading with nobody else in the race is a placeholder time.
SAME_TIME_MIN_FIELD = RO.MIN_NEIGHBOURS
# persons per IN-list: a bound on one statement's size, not a judgement
CHUNK = 2000

Finding = namedtuple("Finding", "check key severity title detail links subject",
                     defaults=("info", "", "", (), None))

CHECKS = (
    ("performance", "Impossible or implausible performances"),
    ("jump", "Sudden rating jumps"),
    ("duplicate", "Likely duplicate athletes"),
    ("career", "Likely wrong-person merges"),
    ("meet", "Broken meets"),
    ("board", "Board churn"),
    ("pipeline", "Pipeline health"),
)
CHECK_TITLES = dict(CHECKS)


# ---------------------------------------------------------------------- #
# links
# ---------------------------------------------------------------------- #

def athleteHref(pid):
    return f"/athlete/{int(pid)}"


def raceHref(sport, source, meet_id, div_id, event_id=None):
    """The race page, or the meet page when the division is not known."""
    q = "?src=tfrrs" if source == "tfrrs" else ""
    if meet_id is None:
        return None
    if sport == "XC":
        return (f"/race/xc/{int(meet_id)}/{int(div_id)}{q}" if div_id is not None
                else f"/meet/xc/{int(meet_id)}{q}")
    if div_id is not None and event_id is not None:
        return f"/race/tf/{int(meet_id)}/{int(event_id)}/{int(div_id)}{q}"
    return f"/meet/tf/{int(meet_id)}{q}"


def boardHref(sport, pool):
    return f"/rankings/{sport.lower()}/{pool}"


# ---------------------------------------------------------------------- #
# pure: the arithmetic every check shares
# ---------------------------------------------------------------------- #

def unusual(value, history, floor, two_sided=False):
    """(z, flagged) of one number against its own history -- the
    rating_outliers rule (median, MAD x 1.4826, spread floor). z is None
    with fewer than MIN_HISTORY past values: recorded, not judged. Pure."""
    hist = [float(h) for h in history if h is not None]
    if value is None or len(hist) < MIN_HISTORY:
        return None, False
    _med, _sl, _sig, z = RO.neighbourZ(float(value), hist, floor)
    flagged = (abs(z) if two_sided else z) >= UNUSUAL_SIGMA
    return z, flagged


def churn(prev_ids, cur_ids):
    """Share of tonight's top-N that was not in the previous top-N (0..1);
    None when either board is empty. Pure."""
    prev, cur = list(prev_ids or ()), list(cur_ids or ())
    if not prev or not cur:
        return None
    n = max(len(prev), len(cur))
    return 1.0 - len(set(prev) & set(cur)) / float(n)


def season(date):
    """The academic year (August seam), as athlete_season stores it."""
    return LPS._season(date)


def reconcile(open_items, tonight, ran_checks):
    """(new, still_open, resolved) keys. open_items: {(check, key): row}
    of findings open before tonight; tonight: {(check, key): Finding};
    ran_checks: the checks that completed tonight -- an open item of a
    check that did not run stays open, untouched. Pure."""
    new = [k for k in tonight if k not in open_items]
    still = [k for k in tonight if k in open_items]
    resolved = [k for k in open_items
                if k not in tonight and k[0] in ran_checks]
    return new, still, resolved


# ---------------------------------------------------------------------- #
# pure: check 1 -- performances
# ---------------------------------------------------------------------- #

def judgePerformances(sport, rows, already=frozenset()):
    """[Finding] for the rows faster than the record allows, judged by
    impossible_race.judge itself (the record condemns the race; the floor
    flags the row). rows: candidateSql's dicts. already: race keys and
    result ids the last 06c already condemned. Pure."""
    races, floor_rows = IR.judge(sport, [r for r in rows if r["result_id"] not in already])
    out = []
    for key, hits in races.items():
        if key in already:
            continue
        source, meet_id, div_id, event_id = key
        rid, t, d, pace, rec = min(hits, key=lambda h: h[3])
        out.append(Finding(
            "performance", f"record:{sport}:{source}:{meet_id}:{div_id}:{event_id}", "high",
            f"{sport} race faster than the world record ({len(hits)} row{'s' if len(hits) > 1 else ''})",
            f"fastest {pace:.0f} s/km over {d:.0f} m against a record pace of {rec:.0f} s/km: "
            f"a wrong distance or a wrong clock (impossible_race condemns the race at the next full run)",
            (("race", raceHref(sport, source, meet_id, div_id, event_id)),),
            {"sport": sport, "result_ids": [h[0] for h in hits]}))
    for rid, key, t, d, pace, floor in floor_rows:
        source, meet_id, div_id, event_id = key
        out.append(Finding(
            "performance", f"floor:{sport}:{rid}", "high",
            f"{sport} result faster than the high-school floor",
            f"{pace:.0f} s/km over {d:.0f} m, floor {floor:.0f} s/km (record_pace.poolFactor): "
            f"a mis-pooled athlete or a wrong time",
            (("race", raceHref(sport, source, meet_id, div_id, event_id)),),
            {"sport": sport, "result_ids": [rid]}))
    return out


def judgeBand(sport, rows, band_for):
    """[Finding] per race for rows whose normalized_time is outside the
    engine's pace band for their pool (normalize_distance.poolBandFor:
    PACE_FLOOR / PACE_CEIL times the pool's anchor) -- rated by the fill,
    never ranked. band_for(pool, sport) -> (lo, hi). Pure."""
    by = defaultdict(list)
    for r in rows:
        nt, pool = r.get("normalized_time"), (r.get("rating_pool") or "").split("|", 1)[0]
        if nt is None or not pool:
            continue
        lo, hi = band_for(pool, sport)
        if not lo <= float(nt) <= hi:
            by[(r.get("source"), r.get("meet_id"), r.get("div_id"), r.get("event_id"))].append(
                (r["result_id"], float(nt), lo, hi, pool))
    out = []
    for (source, meet_id, div_id, event_id), hits in by.items():
        fast = sum(1 for _r, nt, lo, _h, _p in hits if nt < lo)
        out.append(Finding(
            "performance", f"band:{sport}:{source}:{meet_id}:{div_id}:{event_id}", "info",
            f"{sport} race with {len(hits)} row{'s' if len(hits) > 1 else ''} outside the engine's pace band",
            f"{fast} faster than PACE_FLOOR, {len(hits) - fast} slower than PACE_CEIL of the "
            f"{hits[0][4]} anchor: rated, never ranked (build_ranking_results' band gate)",
            (("race", raceHref(sport, source, meet_id, div_id, event_id)),),
            {"sport": sport, "result_ids": [h[0] for h in hits]}))
    return out


def judgeBreakoutChecks(rows):
    """[Finding] for the breakouts page's own "check" rows: a jump of
    CHECK_JUMP over the season median, or a PR CHECK_GAIN under the old
    one ("usually a distance error"). rows: breakout_rows dicts. Pure.

    Jumps are judged per RACE, not per runner: two or more runners of one
    race all jumping is the course or distance, one item for that race.
    A runner jumping alone is usually a kid improving -- the breakouts
    page already lists them, and the career check (judgeJumps) judges
    tonight's ones against the runner's own spread -- so those fold into
    one count line instead of one item each."""
    out = []
    by_race = defaultdict(list)
    for r in rows:
        if r["kind"] == "pr":
            href = athleteHref(r["person_id"])
            race = raceHref(r["sport"], None, r.get("meet_id"), r.get("div_id"), r.get("event_id"))
            links = (("athlete", href),) + ((("race", race),) if race else ())
            out.append(Finding(
                "performance", f"pr:{r['sport']}:{r['result_id']}", "high",
                f"{r.get('name') or 'Athlete'}: PR {float(r['gain']) * 100:.0f}% under the old one",
                f"breakouts marks a PR {BO.CHECK_GAIN * 100:.0f}% or more under the previous best "
                f"'check' -- usually a distance error ({r.get('meet_name') or 'meet'}, {r.get('race_date')})",
                links, {"sport": r["sport"], "result_ids": [r["result_id"]]}))
        else:
            by_race[(r["sport"], r.get("meet_id"), r.get("div_id"), r.get("event_id"))].append(r)
    singles = []
    for (sport, meet_id, div_id, event_id), rs in sorted(
            by_race.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        if len(rs) < 2:
            singles += rs
            continue
        jumps = sorted(float(r["jump"]) for r in rs)
        race = raceHref(sport, None, meet_id, div_id, event_id)
        out.append(Finding(
            "jump", f"race:{sport}:{meet_id}:{div_id}:{event_id}", "high",
            f"{rs[0].get('meet_name') or 'Meet'} ({rs[0].get('race_date')}): "
            f"{len(rs)} runners {jumps[0]:+.1f} to {jumps[-1]:+.1f} over their season median",
            f"several runners of one race all jumping by breakouts' check bar (CHECK_JUMP "
            f"{BO.CHECK_JUMP:g}) is usually the course's distance or difficulty, not the runners",
            (("race", race),) if race else (),
            {"sport": sport, "result_ids": sorted(r["result_id"] for r in rs)}))
    if singles:
        out.append(Finding(
            "jump", "breakouts:singles", "info",
            f"{len(singles)} runner{'s' if len(singles) != 1 else ''} jumping alone by breakouts' check bar",
            f"one runner per race, CHECK_JUMP {BO.CHECK_JUMP:g} or more over the season median; "
            f"listed with a 'check' mark on the breakouts page",
            (("breakouts", "/breakouts"),),
            {"result_ids": sorted((r["sport"], r["result_id"]) for r in singles)}))
    return out


# ---------------------------------------------------------------------- #
# pure: check 2 -- jumps
# ---------------------------------------------------------------------- #

def nearest(rows, i, k):
    """The k rows nearest rows[i] by date, rows[i] excluded (rows: dicts
    with 'd' a date). Ties go to the earlier row. Pure."""
    d0 = rows[i]["d"]
    others = [r for j, r in enumerate(rows) if j != i]
    others.sort(key=lambda r: (abs((r["d"] - d0).days), r["d"]))
    return others[:k]


def judgeJumps(career, judge_ids, calib):
    """[Finding] for the judged races of ONE athlete. career: [{result_id,
    sport, d (date), rating, fast_flag}] every rated race of the person,
    both sports; judge_ids: {(sport, result_id)} to judge (tonight's and
    open ones); calib: rating_outliers.latestCalib() or None.

    Two rules, both the engine's:
      neighbours  rating_outliers: the K nearest rated races by date (last
                  run's fast outliers are no one's neighbour), median and
                  MAD with the measured floor; z at or past the fast cut.
      season      breakouts: the median of the earlier races this season
                  in this sport, MIN_PRIOR of them at least; a jump of
                  CHECK_JUMP or more. Pure."""
    rows = sorted(career, key=lambda r: (r["d"], r["result_id"]))
    pool = [r for r in rows if not r.get("fast_flag")]
    out = []
    for r in rows:
        if (r["sport"], r["result_id"]) not in judge_ids:
            continue
        notes = []
        if calib and calib.get("k") and calib.get("fast_cut") is not None:
            others = [x for x in pool if x["result_id"] != r["result_id"] or x["sport"] != r["sport"]]
            if len(others) >= RO.MIN_NEIGHBOURS:
                tmp = others + [r]
                tmp.sort(key=lambda x: (x["d"], x["result_id"]))
                near = nearest(tmp, tmp.index(r), int(calib["k"]))
                if len(near) >= RO.MIN_NEIGHBOURS:
                    med, _sl, sigma, z = RO.neighbourZ(float(r["rating"]),
                                                       [float(x["rating"]) for x in near],
                                                       float(calib["floor"] or 0.0))
                    if z >= float(calib["fast_cut"]):
                        notes.append(f"{z:.1f} sigma above its {len(near)} nearest races "
                                     f"(median {med:.1f}, spread {sigma:.1f}; "
                                     f"the outlier cut is {float(calib['fast_cut']):.2f})")
        yr = season(r["d"])
        prior = [float(x["rating"]) for x in pool
                 if x["sport"] == r["sport"] and season(x["d"]) == yr and x["d"] < r["d"]]
        if len(prior) >= SEASON_MIN_PRIOR:
            base = statistics.median(prior)
            if float(r["rating"]) - base >= SEASON_CHECK_JUMP:
                notes.append(f"{float(r['rating']) - base:+.1f} over the median of {len(prior)} "
                             f"earlier races this season (breakouts' check bar {SEASON_CHECK_JUMP:g})")
        if notes:
            out.append(Finding(
                "jump", f"{r['sport']}:{r['result_id']}", "high",
                f"rating {float(r['rating']):.1f} on {r['d']}: a jump the outlier rules call implausible",
                "; ".join(notes) + ". A wrong distance, a wrong time or two people in one career.",
                (("athlete", athleteHref(r["person_id"])),) if r.get("person_id") else (),
                {"sport": r["sport"], "result_ids": [r["result_id"]]}))
    return out


# ---------------------------------------------------------------------- #
# pure: check 3 -- duplicates
# ---------------------------------------------------------------------- #

def nameGroups(season_rows, names, touched):
    """{(school, sport, year, name): [pids]} with two persons or more, at
    least one touched tonight. season_rows: [(pid, school, sport, year)];
    names: {pid: name}. Pure."""
    by = defaultdict(set)
    for pid, school, sport, year in season_rows:
        nm = normName(names.get(pid) or "")
        sk = (school or "").strip().lower()
        if not nm or not sk or not LPS.identifyingSchool(school):
            continue
        by[(sk, sport, year, nm)].add(pid)
    return {k: sorted(v) for k, v in by.items()
            if len(v) > 1 and v & set(touched)}


def judgeDuplicate(group_key, members):
    """Finding or None: decideGroup's verdict on the persons as careers
    (allow_careers -- every check still applies: sexes, a number in the
    name, two cross country races on one day, one track race twice, the
    generation test, the years at one school). Pure."""
    school, sport, year, nm = group_key
    v = LPS.decideGroup(members, allow_careers=True, keys=[(nm, school)])
    if v.reason != LPS.MATCH:
        return None
    pids = sorted(m.pid for m in members)
    name = next((m.name for m in members if m.name), nm)
    return Finding(
        "duplicate", "people:" + "-".join(str(p) for p in pids), "high",
        f"{name}: {len(pids)} athlete pages, one runner?",
        f"same name at {school} in {sport} {year}, no race shared, nothing in "
        f"link_profile_school's checks against joining them (scripts/link_profile_school.py "
        f"--careers --person {v.target} prints the case; joining is a human's call)",
        tuple(("athlete", athleteHref(p)) for p in pids),
        {"pids": pids, "school": school, "name": nm, "sport": sport, "year": year})


def sameRaceTwins(sport, race_rows, twins):
    """[Finding] for one finish stored under two ids in one race: same place
    and time to the tenth (twin_flag's twin_race rule), neither row in
    result_twin. race_rows: [{result_id, person_id, place, time_seconds,
    source, meet_id, div_id, event_id}] of one race. Pure."""
    by = defaultdict(list)
    for r in race_rows:
        if (r.get("place") or 0) > 0 and (r.get("time_seconds") or 0) > 0 \
                and (sport, r["result_id"]) not in twins:
            by[(r["place"], round(float(r["time_seconds"]), 1))].append(r)
    out = []
    for (place, t), rs in by.items():
        pids = sorted({r.get("person_id") for r in rs if r.get("person_id") is not None})
        if len(rs) < 2 or len(pids) < 2:
            continue
        r0 = rs[0]
        out.append(Finding(
            "duplicate", f"twin:{sport}:" + "-".join(str(r["result_id"]) for r in
                                                     sorted(rs, key=lambda r: r["result_id"])), "high",
            f"one {sport} finish under {len(pids)} athletes",
            f"place {place}, {t:.1f} s on {len(rs)} rows of one race, not in result_twin",
            (("race", raceHref(sport, r0.get("source"), r0.get("meet_id"), r0.get("div_id"),
                               r0.get("event_id"))),)
            + tuple(("athlete", athleteHref(p)) for p in pids),
            # ! the race's meet, so an open twin item is judged again
            #   (loadRaces reads its meet) instead of resolving unseen
            {"sport": sport, "result_ids": sorted(r["result_id"] for r in rs),
             "meet": [sport, r0.get("source"), r0.get("meet_id")]}))
    return out


# ---------------------------------------------------------------------- #
# pure: check 4 -- careers
# ---------------------------------------------------------------------- #

def careerProblems(pid, rows, rules=()):
    """Finding or None for one person's career. rows: [LPS.Row-like with
    extra .race (a race key) and .twin] -- link_profile_school's own
    checks, run on ONE career against itself:
      generation  rows whose class (numeric or FR-n..SR-n) sits more than
                  CLASS_SLACK (+ COLLEGE_DRIFT with a college reading) from
                  the career's median class
      years       more seasons at one identifying school than
                  MAX_SEASONS_AT_ONE_SCHOOL
      same day    two cross country races on one day (two runners), twins
                  left out. Pure."""
    rows = [r for r in rows if not getattr(r, "twin", False)]
    notes = []
    reads = [LPS._rowClass(r) for r in rows]
    cls = [c for c, _col in reads if c is not None]
    if cls:
        med = statistics.median(cls)
        college = any(col for c, col in reads if c is not None)
        slack = CLASS_SLACK + (COLLEGE_DRIFT if college else 0)
        off = sorted({int(c) for c in cls if abs(c - med) > slack})
        if off:
            notes.append(f"grades imply high-school classes {', '.join(map(str, off))} against the "
                         f"career's {med:g} (more than {slack} apart: two generations)")
    by_school = defaultdict(set)
    for r in rows:
        if r.school and LPS.identifyingSchool(r.school):
            s = season(r.date)
            if s is not None:
                by_school[r.school.strip()].add(s)
    for school, ss in sorted(by_school.items()):
        if max(ss) - min(ss) + 1 > MAX_SEASONS:
            notes.append(f"{max(ss) - min(ss) + 1} seasons at {school} ({min(ss)}-{max(ss)}); "
                         f"one athlete has at most {MAX_SEASONS}")
    xc_days = defaultdict(set)
    for r in rows:
        if r.sport == "XC" and r.date:
            xc_days[str(r.date)[:10]].add(getattr(r, "race", None))
    two = sorted(d for d, races in xc_days.items() if len(races) > 1)
    if two:
        notes.append(f"two cross country races on {', '.join(two[:3])}"
                     + (f" and {len(two) - 3} more days" if len(two) > 3 else ""))
    if not notes:
        return None
    return Finding(
        "career", f"person:{pid}", "high",
        f"athlete {pid}: a career that may be two people",
        "; ".join(notes) + (f". Rows moved in by: {', '.join(sorted(set(rules)))}" if rules else "")
        + ". Report only: person_link_log names every move, and each linker has its --undo.",
        (("athlete", athleteHref(pid)),),
        {"pids": [pid]})


# ---------------------------------------------------------------------- #
# pure: check 5 -- meets
# ---------------------------------------------------------------------- #

def judgeRace(sport, key, rows, event_meters=None):
    """[Finding] for one race. rows: dicts with time_seconds, place, gender,
    distance (XC: the loader's distance; TF: the stored distance_meters),
    event_short, is_relay, is_field. event_meters(ev) -> metres or None
    (event_parse.distanceFromEventShort). Pure."""
    source, meet_id, div_id, event_id = key
    href = raceHref(sport, source, meet_id, div_id, event_id)
    kid = f"{sport}:{source}:{meet_id}:{div_id}:{event_id}"
    fin = [r for r in rows if (r.get("time_seconds") or 0) > 0]
    out = []
    if not fin:
        return out
    r0 = rows[0]
    field = bool(r0.get("is_relay")) or bool(r0.get("is_field"))
    if sport == "XC":
        d = r0.get("distance")
        if d is None or float(d) <= 0:
            out.append(Finding("meet", f"distance:{kid}", "high",
                               "cross country race with no distance",
                               f"{len(fin)} finishers and no distance from dist_override, the meet "
                               f"or the tfrrs division: unrated", (("race", href),),
                               {"meet": [sport, source, meet_id]}))
    elif not field and event_meters is not None:
        d = r0.get("distance")
        m = event_meters(r0.get("event_short"))
        if m and d is not None and float(d) <= 0:
            out.append(Finding("meet", f"distance:{kid}", "high",
                               f"{r0.get('event_short')}: stored distance {d:g}",
                               f"the event name reads {m:g} m but meets_tf carries {d:g}, which the "
                               f"loader prefers to the name", (("race", href),),
                               {"meet": [sport, source, meet_id]}))
    if not field and len(fin) >= SAME_TIME_MIN_FIELD \
            and len({round(float(r["time_seconds"]), 1) for r in fin}) == 1:
        out.append(Finding("meet", f"sametime:{kid}", "high",
                           f"every finisher on one time ({len(fin)} rows)",
                           f"all {len(fin)} finishers at {float(fin[0]['time_seconds']):.1f} s: "
                           f"a placeholder clock, not a race", (("race", href),),
                           {"meet": [sport, source, meet_id]}))
    places = [int(r["place"]) for r in rows if (r.get("place") or 0) > 0]
    if places and max(places) > len(rows):
        out.append(Finding("meet", f"places:{kid}", "info",
                           f"places run to {max(places)}, {len(rows)} rows landed",
                           "part of the field is missing from the scrape (or the places are "
                           "the whole meet's)", (("race", href),),
                           {"meet": [sport, source, meet_id]}))
    sexes = Counter(r.get("gender") for r in rows if r.get("gender") in ("M", "F"))
    if len(sexes) == 2 and not field:
        out.append(Finding("meet", f"sexes:{kid}", "info",
                           f"both sexes in one division (M {sexes['M']}, F {sexes['F']})",
                           "a mixed race, a division filed under the wrong sex, or athletes with "
                           "the wrong sex on their profile", (("race", href),),
                           {"meet": [sport, source, meet_id]}))
    return out


# ---------------------------------------------------------------------- #
# pure: check 7 -- the pipeline's own logs
# ---------------------------------------------------------------------- #

_STAMP = re.compile(r"^\d\d:\d\d:\d\d ")
_STEP_OK = re.compile(r"^\s+(\S+) ok \((\d+)s")
_STEP_FAIL = re.compile(r"^\s+(\S+) FAILED")
_STEP_NOTRUN = re.compile(r"^\s+(\S+) NOT RUN")
_STEP_SKIP = re.compile(r"^\s+(\S+) skipped")
# ! the nightly scrape's two quiet endings are not failures (nightly_update.sh
#   scrape(): an empty queue, and the night's time bound)
_STEP_QUIET = re.compile(r"^\s+(\S+): nothing new")
_STEP_STOPPED = re.compile(r"^\s+(\S+) stopped at the")


def parseSteps(text):
    """{step: 'ok' | 'failed' | 'not run' | 'skipped' | 'quiet' | 'stopped'}
    from a summary.log (full run) or SUMMARY.txt (nightly) -- both step()
    formats, and the nightly scrape's own lines. Pure."""
    out = {}
    for line in (text or "").splitlines():
        line = _STAMP.sub("", line)
        for rx, state in ((_STEP_FAIL, "failed"), (_STEP_NOTRUN, "not run"),
                          (_STEP_SKIP, "skipped"), (_STEP_QUIET, "quiet"),
                          (_STEP_STOPPED, "stopped"), (_STEP_OK, "ok")):
            m = rx.match(line)
            if m:
                out[m.group(1)] = state
                break
    return out


def runKind(dirname):
    return "nightly" if os.path.basename(os.path.normpath(dirname)).startswith("nightly_") else "full"


def summaryText(logdir):
    for nm in ("summary.log", "SUMMARY.txt"):
        try:
            with open(os.path.join(logdir, nm), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            continue
    return ""


def previousRun(logdir, log_root=None):
    """The run of the same kind before this one, or None."""
    root = log_root or os.path.dirname(os.path.normpath(logdir))
    pat = "nightly_*" if runKind(logdir) == "nightly" else "2*"
    dirs = sorted(d for d in glob.glob(os.path.join(root, pat)) if os.path.isdir(d))
    me = os.path.normpath(logdir)
    older = [d for d in dirs if os.path.basename(d) < os.path.basename(me)]
    return older[-1] if older else None


def pipelineFindings(run, steps, prev_steps, self_step=None):
    """[Finding] for one run's steps against the previous run's of the same
    kind: failed, not run, and steps that ran last time and are missing
    now. Pure."""
    out = []
    for s, state in sorted(steps.items()):
        if state in ("failed", "not run"):
            out.append(Finding("pipeline", f"step:{s}:{state}", "high",
                               f"{s} {state.upper() if state == 'failed' else state}",
                               f"run {run}: see {s}.log", (("status", "/account/status#pipeline"),),
                               None))
    if prev_steps:
        # ! ONLY THE STEPS BEFORE THIS ONE: the watchdog runs mid-run in the
        #   full pipeline (18_vacuum follows it), so a step after it has not
        #   run YET. Cut the previous run's order at the watchdog's own line,
        #   or, before its first run, after the last step that ran tonight.
        order = list(prev_steps)
        if self_step in order:
            cut = order.index(self_step)
        else:
            cut = max((order.index(s) + 1 for s in steps if s in order), default=0)
        gone = sorted(s for s in order[:cut]
                      if prev_steps[s] == "ok" and s not in steps and s != self_step)
        if gone:
            out.append(Finding("pipeline", "missing:" + ",".join(gone)[:200], "high",
                               f"{len(gone)} step{'s' if len(gone) > 1 else ''} that ran last time did not run",
                               f"run {run}: {', '.join(gone)}", (("status", "/account/status#pipeline"),),
                               None))
    return out


# ★ 10a_board_sanity'S CAUSE CLASS (open item, run 20261006_120609; owner:
#   "come back to 10a later"). The step prints one line per check -- a
#   "<kind> N finding(s) HARD:" header for pool/anchor/pace, "HARD: off by
#   more than" in the level and tilt tables, "[sanity] N hard finding(s)"
#   last -- so a failed run's cause is readable from its log alone. Past
#   failures, by class: 2026-10-01 STALE BOARDS (10_rankings_finish failed,
#   10a read the previous boards: 25 findings), 2026-10-02 TILT (5 XC bands
#   on the all-course table) and LEVEL (3 levels off target). The classes:
_10A_KINDS = ("pool", "anchor", "pace", "club", "margin")


def classify10a(text, failed_steps=()):
    """(cause class, detail) for one 10a_board_sanity log. Classes:
    'stale boards' (10_rankings_finish failed in the same run: 10a judged
    the previous boards), 'crash' (a traceback, or no closing line),
    'rows: <kinds>' (published rows broke the pool/anchor/pace rails),
    'level', 'tilt' (a measured constant did not hold), 'strict' (soft
    findings under --strict), 'ok'. Several hard kinds join with '+'.
    Pure."""
    lines = [_STAMP.sub("", ln) for ln in (text or "").splitlines()]
    body = "\n".join(lines)
    if any(s.startswith("10_rankings_finish") or s == "10_rankings_stream" for s in failed_steps):
        return "stale boards", ("10_rankings_finish failed in the same run, so 10a read the "
                                "previous run's boards; rerun 10a after the boards swap")
    if "Traceback (most recent call last)" in body or "[sanity] ok" not in body \
            and "[sanity] FAILED" not in body:
        last = next((ln.strip() for ln in reversed(lines) if ln.strip()), "")
        return "crash", f"the step did not finish its report: {last[:160]}"
    if "[sanity] ok" in body:
        return "ok", ""
    kinds, section = Counter(), None
    for ln in lines:
        if ln.startswith("== the sport level"):
            section = "level"
        elif ln.startswith("== the course scale"):
            section = "tilt"
        elif ln.startswith("== "):
            section = None
        m = re.match(r"^\s+(\w+)\s+(\d+) finding\(s\) HARD:", ln)
        if m and m.group(1) in _10A_KINDS:
            kinds[m.group(1)] += int(m.group(2))
        elif section and "HARD: off by more than" in ln:
            kinds[section] += 1
    if not kinds:
        m = re.search(r"\[sanity\] (\d+) hard finding\(s\), (\d+) soft", body)
        if m and int(m.group(1)) == 0 and int(m.group(2)) > 0:
            return "strict", f"{m.group(2)} soft findings failed the step under --strict"
        return "unclassified", "FAILED with no HARD line the classifier knows; read the log"
    rows = [k for k in ("pool", "anchor", "pace") if kinds.get(k)]
    parts = (["rows: " + "+".join(rows)] if rows else []) + \
        [k for k in ("level", "tilt") if kinds.get(k)]
    detail = ", ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
    return " + ".join(parts), detail


# ---------------------------------------------------------------------- #
# the report
# ---------------------------------------------------------------------- #

def openFor(first_seen, now):
    """'3 nights' / 'since tonight'."""
    try:
        days = (now.date() - first_seen.date()).days
    except AttributeError:
        return ""
    return "new tonight" if days <= 0 else f"open {days} day{'s' if days != 1 else ''}"


def renderText(run, items, new_keys, open_meta, resolved, now, origin="", notes=()):
    """The plain-text report: counts, NEW items by check with links, then
    the older open items with how long each has been open. Pure."""
    by = defaultdict(list)
    for k, f in items.items():
        by[f.check].append((k, f))
    n_new = len(new_keys)
    head = (f"Data watchdog, {run}: {n_new} new, {len(items) - n_new} still open, "
            f"{len(resolved)} resolved")
    out = [head, "=" * min(len(head), 78), ""]
    for note in notes:
        out.append(f"! {note}")
    if notes:
        out.append("")
    for check, title in CHECKS:
        got = sorted(by.get(check, []), key=lambda kf: (kf[0] not in new_keys, kf[1].severity != "high",
                                                        kf[1].title))
        if not got:
            continue
        n_n = sum(1 for k, _f in got if k in new_keys)
        out.append(f"{title}: {len(got)} ({n_n} new)")
        for k, f in got:
            tag = "NEW " if k in new_keys else ""
            age = "" if k in new_keys else f"  [{openFor(open_meta.get(k), now)}]"
            out.append(f"  {tag}{f.title}{age}")
            if f.detail:
                out.append(f"      {f.detail}")
            for label, href in f.links:
                if href:
                    out.append(f"      {label}: {origin}{href}")
        out.append("")
    if not items:
        out.append("Nothing found.")
    return "\n".join(out).rstrip() + "\n"


def renderEmail(run, items, new_keys, now, origin):
    """(subject, text, html) for the admins: the NEW items only, in
    alert_email's look (tables, inline styles, black on white, the navy
    accent; no images). Pure."""
    import alert_email as AE
    new = [items[k] for k in new_keys]
    subject = f"[racecast] watchdog {run}: {len(new)} new item{'s' if len(new) != 1 else ''}"
    page = f"{origin}/account/status/watchdog"
    text = [f"The data watchdog found {len(new)} new item{'s' if len(new) != 1 else ''} in run {run}.",
            "Nothing was changed: every item is for a human to look at.", ""]
    blocks = []
    for check, title in CHECKS:
        fs = [f for f in new if f.check == check]
        if not fs:
            continue
        text.append(title.upper())
        rows = []
        for f in fs:
            text.append(f"- {f.title}")
            if f.detail:
                text.append(f"  {f.detail}")
            for label, href in f.links:
                if href:
                    text.append(f"  {label}: {origin}{href}")
            links = " &middot; ".join(
                f'<a href="{AE._e(origin + href)}" style="color:{AE.ACCENT};text-decoration:none">'
                f'{AE._e(label)}</a>' for label, href in f.links if href)
            rows.append(
                f'<p style="margin:8px 0 0;font-size:15px;line-height:20px;color:{AE.INK}">{AE._e(f.title)}</p>'
                + (f'<p style="margin:2px 0 0;font-size:13px;line-height:18px;color:{AE.MUTED}">'
                   f'{AE._e(f.detail)}</p>' if f.detail else "")
                + (f'<p style="margin:2px 0 0;font-size:13px;line-height:18px">{links}</p>' if links else ""))
        text.append("")
        blocks.append(
            f'<tr><td style="padding:14px 0 12px;border-top:1px solid {AE.LINE}">'
            f'<p style="margin:0;font-size:17px;line-height:22px;font-weight:700;color:{AE.INK}">'
            f'{AE._e(title)}</p>{"".join(rows)}</td></tr>')
    text += [f"Every open item: {page}", ""]
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only"><title>{AE._e(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid {AE.LINE};border-radius:10px;font-family:{font}">
<tr><td style="padding:22px 24px 0">
<p style="margin:0;font-size:21px;line-height:24px;font-weight:900;font-style:italic;letter-spacing:-0.5px;color:{AE.INK}">RACECAST</p>
</td></tr>
<tr><td style="padding:16px 24px 4px">
<p style="margin:0 0 14px;font-size:16px;line-height:24px;color:{AE.INK}">{AE._e(text[0])} {AE._e(text[1])}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{''.join(blocks)}
</table>
</td></tr>
<tr><td style="padding:4px 24px 20px;border-top:1px solid {AE.LINE}">
<p style="margin:14px 0 0">{AE._link(page, "Every open item")}</p>
</td></tr>
</table>
</td></tr></table>
</body></html>
"""
    return subject, "\n".join(text), html


# ---------------------------------------------------------------------- #
# database: tables
# ---------------------------------------------------------------------- #

DDL = """
CREATE TABLE IF NOT EXISTS watchdog_run (
    run_id     bigserial PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    kind       text NOT NULL,
    log_dir    text,
    since      timestamptz,
    n_new      int,
    n_open     int,
    n_resolved int,
    counts     jsonb,
    report     text);
CREATE TABLE IF NOT EXISTS watchdog_finding (
    check_name text NOT NULL,
    key        text NOT NULL,
    severity   text,
    title      text,
    detail     text,
    links      jsonb,
    subject    jsonb,
    first_seen timestamptz NOT NULL,
    last_seen  timestamptz NOT NULL,
    still_open boolean NOT NULL DEFAULT true,
    resolved   timestamptz,
    nights     int NOT NULL DEFAULT 1,
    PRIMARY KEY (check_name, key));
CREATE INDEX IF NOT EXISTS watchdog_finding_open_idx ON watchdog_finding (still_open, check_name);
CREATE TABLE IF NOT EXISTS watchdog_board_top (
    run_id     bigint NOT NULL,
    kind       text NOT NULL,
    sport      text NOT NULL,
    pool       text NOT NULL,
    year       int  NOT NULL,
    person_ids bigint[] NOT NULL,
    churn      real,
    PRIMARY KEY (run_id, sport, pool));
"""

# ★ THE SCOPE INDEXES: BRIN, because both columns grow with the table's
#   physical order (rows are appended; person_link_log is append-only) --
#   a block-range summary of a few kilobytes answers "since last night"
#   without a B-tree's write cost on 60M rows. Also in add_page_indexes.
SCOPE_INDEXES = (
    ("results", "scraped_at", "idx_results_scraped_brin"),
    ("results_tf", "scraped_at", "idx_results_tf_scraped_brin"),
    ("person_link_log", "linked_at", "idx_person_link_log_at_brin"),
)


def _has(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def _cols(cur, table):
    cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""", (table,))
    return dict(cur.fetchall())


def _indexed(cur, table, col):
    cur.execute(r"""SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid
                    WHERE t.relname = %s AND i.indisvalid
                      AND pg_get_indexdef(i.indexrelid) ~* ('\(\s*' || %s || '\s*[,)]')
                    LIMIT 1""", (table, col))
    return cur.fetchone() is not None


def ensureIndexes(conn, log=print):
    """Build the scope indexes that are missing (CONCURRENTLY, once)."""
    with conn.cursor() as cur:
        todo = [(t, c, n) for t, c, n in SCOPE_INDEXES
                if _has(cur, t) and c in _cols(cur, t) and not _indexed(cur, t, c)]
    conn.rollback()
    if not todo:
        return
    old = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # ! no statement timeout on a one-time build (quiet mode may set one)
            cur.execute("SET statement_timeout = 0")
            for t, c, n in todo:
                log(f"[watchdog] building {n} ON {t} USING brin ({c}) -- once")
                cur.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {n} ON {t} USING brin ({c})")
    finally:
        conn.autocommit = old


# ---------------------------------------------------------------------- #
# database: what changed tonight
# ---------------------------------------------------------------------- #

def _sinceParam(cols, since):
    """since as the column's own type: a timestamp, or ISO text."""
    typ = (cols.get("scraped_at") or "").lower()
    return since if "timestamp" in typ else since.isoformat(sep=" ")


def scopeRows(cur, sport, since, ids):
    """Tonight's rows of one sport (scraped since `since`) plus the open
    findings' result ids: [dict]. Read through the BRIN index and the
    primary key."""
    table = TABLES[sport]
    cols = _cols(cur, table)
    opt = lambda c, typ="text": f"r.{c}" if c in cols else f"NULL::{typ} AS {c}"   # noqa: E731
    cur.execute(f"""
        SELECT r.result_id, r.person_id, {opt('source')}, r.meet_id, r.div_id,
               {opt('event_id', 'bigint') if sport == 'TF' else 'NULL::bigint AS event_id'},
               r.date, r.time_seconds, r.normalized_time, r.speed_rating,
               {opt('rating_pool')}, {opt('school')}, {opt('place', 'int')}
        FROM   {table} r
        WHERE  r.scraped_at >= %(since)s OR r.result_id = ANY(%(ids)s)""",
                {"since": _sinceParam(cols, since), "ids": list(ids)})
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def movedPersons(cur, since):
    """{to_person: {rules}} for rows a linker moved since `since`."""
    if not _has(cur, "person_link_log"):
        return {}
    cur.execute("""SELECT to_person, rule FROM person_link_log
                   WHERE linked_at >= %s""", (since,))
    out = defaultdict(set)
    for p, rule in cur.fetchall():
        out[p].add(rule)
    return out


def _excluded(cur, sport, ids):
    """Result ids of `sport` already off the boards: result_twin,
    impossible_result, and last run's fast outliers."""
    out = set()
    ids = list(ids)
    for tbl, extra in (("result_twin", ""), ("impossible_result", ""),
                       ("rating_outlier", "AND side = 'fast'")):
        if ids and _has(cur, tbl):
            cur.execute(f"SELECT result_id FROM {tbl} WHERE sport = %s "
                        f"AND result_id = ANY(%s) {extra}", (sport, ids))
            out |= {r[0] for r in cur.fetchall()}
    return out


# ---------------------------------------------------------------------- #
# database: the checks
# ---------------------------------------------------------------------- #

def runPerformance(cur, scope, open_ids):
    out = []
    for sport in ("XC", "TF"):
        ids = sorted({r["result_id"] for r in scope[sport]} | open_ids.get(sport, set()))
        if not ids:
            continue
        # ! the loader's SQL carries LIKE '%mile%': escaped before a parameter joins it
        sql = IR.candidateSql(cur, sport).replace("%", "%%") + " AND r.result_id = ANY(%(ids)s)"
        cands = []
        for i in range(0, len(ids), CHUNK * 10):
            cur.execute(sql, {"ids": ids[i:i + CHUNK * 10]})
            names = [d[0] for d in cur.description]
            cands += [dict(zip(names, r)) for r in cur.fetchall()]
        already = set()
        if _has(cur, "impossible_result"):
            cur.execute("SELECT result_id FROM impossible_result WHERE sport = %s AND result_id = ANY(%s)",
                        (sport, [c["result_id"] for c in cands]))
            already = {r[0] for r in cur.fetchall()}
        out += judgePerformances(sport, cands, frozenset(already))
        try:
            from normalize_distance import poolBandFor
        except Exception:                                        # noqa: BLE001
            poolBandFor = None
        if poolBandFor is not None:
            out += judgeBand(sport, [r for r in scope[sport] if r["result_id"] not in already],
                             poolBandFor)
    if _has(cur, "breakout_rows"):
        cur.execute("""SELECT kind, sport, result_id, person_id, name, race_date, meet_id, div_id,
                              event_id, meet_name, jump, gain
                       FROM breakout_rows WHERE "check" """)
        names = [d[0] for d in cur.description]
        out += judgeBreakoutChecks([dict(zip(names, r)) for r in cur.fetchall()])
    return out


def _careers(cur, pids, with_rows=False):
    """{pid: [row dict]} every rated race (with_rows: every row) of the
    persons, both sports, through the person indexes."""
    out = defaultdict(list)
    pids = sorted(pids)
    for sport, table in TABLES.items():
        cols = _cols(cur, table)
        ev = "r.event_id" if sport == "TF" and "event_id" in cols else "NULL::bigint"
        evs = "lower(btrim(r.event_short))" if sport == "TF" and "event_short" in cols else "NULL::text"
        sch = "r.school" if "school" in cols else "NULL::text"
        src = "r.source" if "source" in cols else "NULL::text"
        rated = "" if with_rows else "AND r.speed_rating IS NOT NULL"
        for i in range(0, len(pids), CHUNK):
            cur.execute(f"""
                SELECT r.person_id, r.result_id, r.date, r.speed_rating, r.grade, {sch},
                       r.meet_id, r.div_id, {ev}, {evs}, {src}, r.time_seconds
                FROM   {table} r
                WHERE  r.person_id = ANY(%s) {rated}""", (pids[i:i + CHUNK],))
            for (pid, rid, d, rating, grade, school, meet, div, ev_id, evn, source,
                 t) in cur.fetchall():
                out[pid].append({"sport": sport, "result_id": rid, "date": d, "rating": rating,
                                 "grade": grade, "school": school, "meet_id": meet, "div_id": div,
                                 "event_id": ev_id, "event": evn, "source": source,
                                 "time_seconds": t, "person_id": pid})
    return out


def _day(v):
    return LPS._day(v)


def runJumps(cur, scope, open_ids, calib):
    judge = set()
    for sport in ("XC", "TF"):
        judge |= {(sport, r["result_id"]) for r in scope[sport] if r.get("speed_rating") is not None}
        judge |= {(sport, rid) for rid in open_ids.get(sport, ())}
    pid_of = {(s, r["result_id"]): r["person_id"] for s in ("XC", "TF") for r in scope[s]}
    pids = {p for p in pid_of.values() if p is not None}
    out = []
    plist = sorted(pids)
    for i in range(0, len(plist), CHUNK):
        careers = _careers(cur, plist[i:i + CHUNK])
        flags = {}
        for sport in ("XC", "TF"):
            ids = [r["result_id"] for c in careers.values() for r in c if r["sport"] == sport]
            flags[sport] = _excluded(cur, sport, ids) if ids else set()
        for pid, rows in careers.items():
            career = []
            for r in rows:
                d = _day(r["date"])
                if d is None or r["rating"] is None:
                    continue
                excl = r["result_id"] in flags[r["sport"]]
                if excl and (r["sport"], r["result_id"]) in judge:
                    continue                       # already off the boards: handled
                career.append({"result_id": r["result_id"], "sport": r["sport"], "d": d,
                               "rating": r["rating"], "fast_flag": excl, "person_id": pid})
            out += judgeJumps(career, judge, calib)
    return out


def _names(cur, pids):
    """{pid: name}, {pid: [sexes]} from athletes (PK)."""
    names, sexes = {}, defaultdict(set)
    pids = sorted(pids)
    for i in range(0, len(pids), CHUNK):
        cur.execute("""SELECT athlete_id, gender,
                              NULLIF(btrim(concat_ws(' ', btrim(first_name), btrim(last_name))), '')
                       FROM athletes WHERE athlete_id = ANY(%s)""", (pids[i:i + CHUNK],))
        for aid, g, nm in cur.fetchall():
            if nm and aid not in names:
                names[aid] = nm
            if g in ("M", "F"):
                sexes[aid].add(g)
    return names, sexes


def _members(cur, pids):
    careers = _careers(cur, pids, with_rows=True)
    names, sexes = _names(cur, pids)
    return [LPS.Member(p, names.get(p), sorted(sexes.get(p, ())), False,
                       sorted((LPS.Row(r["date"], r["sport"], r["grade"],
                                       r["meet_id"] if r["sport"] == "TF" else None,
                                       r["event"], r["school"]) for r in careers.get(p, [])),
                              key=lambda r: str(r.date)))
            for p in pids]


def runDuplicates(cur, scope, open_subjects, races):
    out = []
    touched = {r["person_id"] for s in ("XC", "TF") for r in scope[s] if r.get("person_id")}
    want = defaultdict(set)                     # (sport, year) -> schools
    for sport in ("XC", "TF"):
        for r in scope[sport]:
            y = season(r.get("date"))
            if r.get("school") and y is not None and r.get("person_id"):
                want[(sport, y)].add(r["school"])
    season_rows = []
    if _has(cur, "athlete_season"):
        for (sport, y), schools in want.items():
            sl = sorted(schools)
            for i in range(0, len(sl), CHUNK):
                cur.execute("""SELECT person_id, school, sport, year FROM athlete_season
                               WHERE school = ANY(%s) AND sport = %s AND year = %s""",
                            (sl[i:i + CHUNK], sport, y))
                season_rows += cur.fetchall()
    veto = set()
    if _has(cur, "profile_school_veto"):
        cur.execute("SELECT old_id FROM profile_school_veto")
        veto = {r[0] for r in cur.fetchall()}
    names, _sx = _names(cur, {r[0] for r in season_rows})
    groups = nameGroups(season_rows, names, touched)
    for subj in open_subjects:                  # re-judge what is open
        if subj and subj.get("pids") and subj.get("school"):
            groups.setdefault((subj["school"], subj.get("sport"), subj.get("year"), subj["name"]),
                              sorted(subj["pids"]))
    for key, pids in groups.items():
        if veto & set(pids):
            continue
        members = [m for m in _members(cur, pids) if m.rows]
        if len(members) < 2:
            continue                            # merged since: nothing to join
        f = judgeDuplicate(key, members)
        if f:
            out.append(f)
    for sport, by_race in races.items():
        ids = [r["result_id"] for rows in by_race.values() for r in rows]
        twins = set()
        if ids and _has(cur, "result_twin"):
            for i in range(0, len(ids), CHUNK * 10):
                cur.execute("SELECT result_id FROM result_twin WHERE sport = %s AND result_id = ANY(%s)",
                            (sport, ids[i:i + CHUNK * 10]))
                twins |= {(sport, r[0]) for r in cur.fetchall()}
        for rows in by_race.values():
            out += sameRaceTwins(sport, rows, twins)
    return out


def runCareers(cur, moved, open_subjects):
    pids = set(moved) | {p for s in open_subjects if s for p in s.get("pids", ())}
    out = []
    plist = sorted(pids)
    for i in range(0, len(plist), CHUNK):
        careers = _careers(cur, plist[i:i + CHUNK], with_rows=True)
        twins = {}
        for sport in ("XC", "TF"):
            ids = [r["result_id"] for c in careers.values() for r in c if r["sport"] == sport]
            twins[sport] = set()
            if ids and _has(cur, "result_twin"):
                cur.execute("SELECT result_id FROM result_twin WHERE sport = %s AND result_id = ANY(%s)",
                            (sport, ids))
                twins[sport] = {r[0] for r in cur.fetchall()}
        for pid, rows in careers.items():
            crows = []
            for r in rows:
                row = LPS.Row(r["date"], r["sport"], r["grade"],
                              r["meet_id"] if r["sport"] == "TF" else None, r["event"], r["school"])
                crows.append(_tag(row, (r["source"], r["meet_id"], r["div_id"]),
                                  r["result_id"] in twins[r["sport"]]))
            f = careerProblems(pid, crows, moved.get(pid, ()))
            if f:
                out.append(f)
    return out


CareerRow = namedtuple("CareerRow", LPS.Row._fields + ("race", "twin"))


def _tag(row, race, twin):
    return CareerRow(*row, race, twin)


def loadRaces(cur, scope, open_subjects):
    """{sport: {race key: [row dict]}} for every race tonight's rows (and
    the open meet findings) touch, through the meet indexes."""
    meets = defaultdict(set)
    for sport in ("XC", "TF"):
        for r in scope[sport]:
            if r.get("meet_id") is not None:
                meets[sport].add(r["meet_id"])
    for s in open_subjects:
        if s and s.get("meet"):
            meets[s["meet"][0]].add(s["meet"][2])
    gender = """(SELECT a.gender FROM athletes a
                 WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id) AND a.gender IN ('M', 'F')
                 LIMIT 1)"""
    out = {"XC": defaultdict(list), "TF": defaultdict(list)}
    for sport, ms in meets.items():
        cols = _cols(cur, TABLES[sport])
        place = "r.place" if "place" in cols else "NULL::int"
        if sport == "XC":
            dov = _has(cur, "dist_override")
            sql = f"""
                SELECT r.result_id, r.source, r.meet_id, r.div_id, NULL::bigint AS event_id,
                       r.person_id, r.time_seconds, {place} AS place, {gender} AS gender,
                       COALESCE({'dov.distance, ' if dov else ''}m.distance,
                                (mt.division_distances -> r.div_id::text ->> 'distance')::real,
                                mt.distance)::real AS distance,
                       NULL::text AS event_short, 0 AS is_relay, 0 AS is_field
                FROM   results r
                LEFT JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
                LEFT JOIN meets_tfrrs mt ON r.source = 'tfrrs' AND mt.meet_id = r.meet_id AND mt.sport = 'XC'
                {'LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id' if dov else ''}
                WHERE  r.meet_id = ANY(%s)"""
        else:
            relay = "COALESCE(r.is_relay, 0)" if "is_relay" in cols else "0"
            fieldc = "COALESCE(r.is_field, 0)" if "is_field" in cols else "0"
            sql = f"""
                SELECT r.result_id, r.source, r.meet_id, r.div_id, r.event_id, r.person_id,
                       r.time_seconds, {place} AS place, {gender} AS gender,
                       m.distance_meters::real AS distance, r.event_short,
                       {relay} AS is_relay, {fieldc} AS is_field
                FROM   results_tf r
                LEFT JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                                    AND m.event_id = r.event_id AND m.source = r.source
                WHERE  r.meet_id = ANY(%s)"""
        ml = sorted(ms)
        for i in range(0, len(ml), CHUNK // 4 or 1):
            cur.execute(sql, (ml[i:i + (CHUNK // 4 or 1)],))
            names = [d[0] for d in cur.description]
            for row in cur.fetchall():
                r = dict(zip(names, row))
                out[sport][(r["source"], r["meet_id"], r["div_id"], r["event_id"])].append(r)
    return out


def runMeets(races):
    try:
        from event_parse import distanceFromEventShort
        meters = lambda ev: distanceFromEventShort(ev)[0]           # noqa: E731
    except Exception:                                                # noqa: BLE001
        meters = None
    out = []
    for sport, by in races.items():
        for key, rows in by.items():
            out += judgeRace(sport, key, rows, meters)
    return out


def currentYear(today=None):
    today = today or datetime.date.today()
    return today.year if today.month >= 8 else today.year - 1


def boardTop(cur, sport, pool, year, n=BOARD_TOP):
    """The board's top-n person ids: athlete_season by mean_rating, the
    board's own floor (season_floor.floorSql, DEFAULT_FLOOR) -- through
    as_board_mean_idx (pool, sport, year, mean_rating DESC)."""
    cur.execute(f"""SELECT s.person_id FROM athlete_season s
                    WHERE s.pool = %(pool)s AND s.sport = %(sport)s AND s.year = %(year)s
                      AND {floorSql(False)}
                    ORDER BY s.mean_rating DESC NULLS LAST, s.person_id
                    LIMIT %(n)s""",
                {"pool": pool, "sport": sport, "year": year, "n": n, "min_races": DEFAULT_FLOOR})
    return [r[0] for r in cur.fetchall()]


def runBoards(cur, kind, run_id):
    """[Finding] and the rows to store: tonight's top-N per board, its
    churn against the previous stored top, judged against the history of
    churn on runs of the SAME kind (a full run re-rates everything; its
    churn is its own population). Floor: one place of N (1/N), the
    measure's own resolution."""
    out, store = [], []
    if not _has(cur, "athlete_season"):
        return out, store
    year = currentYear()
    for sport in ("XC", "TF"):
        for pool in BOARD_POOLS:
            top = boardTop(cur, sport, pool, year)
            cur.execute("""SELECT person_ids FROM watchdog_board_top
                           WHERE sport = %s AND pool = %s AND year = %s AND run_id <> %s
                             AND cardinality(person_ids) > 0
                           ORDER BY run_id DESC LIMIT 1""", (sport, pool, year, run_id))
            prev = cur.fetchone()
            c = churn(prev[0] if prev else None, top)
            cur.execute("""SELECT churn FROM watchdog_board_top
                           WHERE sport = %s AND pool = %s AND kind = %s AND churn IS NOT NULL
                             AND run_id <> %s
                           ORDER BY run_id DESC LIMIT 60""", (sport, pool, kind, run_id))
            hist = [r[0] for r in cur.fetchall()]
            store.append((sport, pool, year, top, c))
            z, bad = unusual(c, hist, 1.0 / max(1, BOARD_TOP))
            if bad:
                out.append(Finding(
                    "board", f"churn:{sport}:{pool}", "high",
                    f"{sport} {pool} board: {c * 100:.0f}% of the top {len(top)} changed",
                    f"past {kind} runs moved a median of {statistics.median(hist) * 100:.0f}% "
                    f"({len(hist)} runs); tonight is {z:.1f} sigma above that "
                    f"(the owner's {UNUSUAL_SIGMA:g})", (("board", boardHref(sport, pool)),),
                    None))
    return out, store


COUNT_TABLES = ("results", "results_tf", "ranking_results", "athlete_season",
                "season_rank", "breakout_rows", "search_index")


def runCounts(cur, kind, run_id, scope, have_scope=True):
    """Table sizes (pg_class.reltuples, no scan) and tonight's new rows,
    each against its own history on runs of the same kind, both sides.
    ! Without a scope there is no count of new rows (not zero of them)."""
    counts = ({f"new_{s}": len([r for r in scope[s] if r.get("_new")]) for s in ("XC", "TF")}
              if have_scope else {})
    for t in COUNT_TABLES:
        cur.execute("SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(%s)",
                    (f"public.{t}",))
        got = cur.fetchone()
        if got and got[0] is not None and got[0] >= 0:
            counts[t] = int(got[0])
    cur.execute("""SELECT counts FROM watchdog_run WHERE kind = %s AND counts IS NOT NULL
                     AND run_id <> %s ORDER BY run_id DESC LIMIT 60""", (kind, run_id))
    hist = [r[0] for r in cur.fetchall()]
    return counts, countFindings(counts, hist, kind)


def countFindings(counts, hist, kind):
    """[Finding] for counts unusual against their history (newest first).
    Table sizes are judged as the change since the previous run, relative;
    the night's new rows as counts.
    ★ THE SPREAD FLOOR IS THE COUNT'S OWN COUNTING ERROR, sqrt(n) (the
      Poisson spread rating_outliers' tail fit reasons with: "1/sqrt(100) =
      10% counting error"), so a steady history with a MAD of zero cannot
      make ten rows' difference look like a collapse. Pure."""
    out = []
    for name, v in sorted(counts.items()):
        series = [h.get(name) for h in hist if isinstance(h, dict) and h.get(name) is not None]
        if not series:
            continue
        if name.startswith("new_"):
            z, bad = unusual(v, series, max(1.0, statistics.median(series)) ** 0.5,
                             two_sided=True)
            what = f"{v:,} new rows against a median of {statistics.median(series):,.0f}" \
                if len(series) >= MIN_HISTORY else ""
        else:
            prev = series[0]
            rel = (v - prev) / float(prev) if prev else None
            deltas = [(a - b) / float(b) for a, b in zip(series, series[1:]) if b]
            z, bad = unusual(rel, deltas, 1.0 / max(1.0, float(prev)) ** 0.5, two_sided=True)
            what = f"{prev:,} -> {v:,} ({(rel or 0) * 100:+.1f}%)"
        if bad:
            out.append(Finding("pipeline", f"count:{name}", "high",
                               f"{name}: {what}",
                               f"{z:+.1f} sigma from the last {len(series)} {kind} runs "
                               f"(the owner's {UNUSUAL_SIGMA:g})",
                               (("status", "/account/status#pipeline"),), None))
    return out


def runPipeline(logdir, failed_steps, log_root=None, self_step=None):
    """[Finding] from the run's own summary, and 10a's cause class from
    the newest full run that ran it."""
    out = []
    if logdir and os.path.isdir(logdir):
        steps = parseSteps(summaryText(logdir))
        for s in failed_steps:
            steps.setdefault(s, "failed")
        prev = previousRun(logdir, log_root)
        out += pipelineFindings(os.path.basename(os.path.normpath(logdir)), steps,
                                parseSteps(summaryText(prev)) if prev else {},
                                self_step=self_step)
    root = log_root or (os.path.dirname(os.path.normpath(logdir)) if logdir else LOG_ROOT)
    for d in sorted(glob.glob(os.path.join(root, "2*")), reverse=True):
        log = os.path.join(d, "10a_board_sanity.log")
        if not os.path.isfile(log):
            continue
        steps = parseSteps(summaryText(d))
        if steps.get("10a_board_sanity") != "failed":
            break                               # the newest 10a passed: nothing open
        with open(log, encoding="utf-8", errors="replace") as f:
            cls, detail = classify10a(f.read(), [s for s, st in steps.items() if st == "failed"])
        out.append(Finding("pipeline", f"10a:{cls}", "high",
                           f"10a_board_sanity failed: {cls}",
                           f"run {os.path.basename(d)}: {detail}",
                           (("status", "/account/status#pipeline"),), None))
        break
    return out


# ---------------------------------------------------------------------- #
# database: persistence
# ---------------------------------------------------------------------- #

def loadOpen(cur):
    cur.execute("""SELECT check_name, key, first_seen, nights, subject FROM watchdog_finding
                   WHERE still_open""")
    return {(c, k): {"first_seen": fs, "nights": n, "subject": s}
            for c, k, fs, n, s in cur.fetchall()}


def saveFindings(cur, tonight, new, still, resolved, now):
    for k in new + still:
        f = tonight[k]
        cur.execute("""
            INSERT INTO watchdog_finding (check_name, key, severity, title, detail, links, subject,
                                          first_seen, last_seen, still_open, resolved, nights)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, NULL, 1)
            ON CONFLICT (check_name, key) DO UPDATE SET
                severity = EXCLUDED.severity, title = EXCLUDED.title, detail = EXCLUDED.detail,
                links = EXCLUDED.links, subject = EXCLUDED.subject, last_seen = EXCLUDED.last_seen,
                -- a resolved item that comes back is new again
                first_seen = CASE WHEN watchdog_finding.still_open
                                  THEN watchdog_finding.first_seen ELSE EXCLUDED.first_seen END,
                nights = CASE WHEN watchdog_finding.still_open
                              THEN watchdog_finding.nights + 1 ELSE 1 END,
                still_open = true, resolved = NULL""",
                    (f.check, f.key, f.severity, f.title, f.detail, json.dumps(list(f.links)),
                     json.dumps(f.subject) if f.subject is not None else None, now, now))
    for c, k in resolved:
        cur.execute("""UPDATE watchdog_finding SET still_open = false, resolved = %s
                       WHERE check_name = %s AND key = %s""", (now, c, k))


def recent(cur, limit=400):
    """For the status page: open items, newest first, and the last
    resolved; and the last runs."""
    if not _has(cur, "watchdog_finding"):
        return None
    cur.execute("""SELECT check_name, key, severity, title, detail, links, first_seen, last_seen,
                          still_open, resolved, nights
                   FROM watchdog_finding
                   WHERE still_open OR resolved > now() - interval '14 days'
                   ORDER BY still_open DESC, first_seen DESC LIMIT %s""", (limit,))
    cols = [d[0] for d in cur.description]
    items = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.execute("""SELECT run_id, started_at, finished_at, kind, log_dir, n_new, n_open, n_resolved
                   FROM watchdog_run ORDER BY run_id DESC LIMIT 14""")
    cols = [d[0] for d in cur.description]
    runs = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {"items": items, "runs": runs, "titles": CHECK_TITLES, "order": [c for c, _t in CHECKS]}


def summaryCounts(cur):
    """(open, new on the last run) for the status page's headline."""
    if not _has(cur, "watchdog_finding"):
        return None
    cur.execute("SELECT count(*) FROM watchdog_finding WHERE still_open")
    n_open = cur.fetchone()[0]
    cur.execute("SELECT n_new, started_at FROM watchdog_run WHERE finished_at IS NOT NULL "
                "ORDER BY run_id DESC LIMIT 1")
    last = cur.fetchone()
    return {"open": n_open, "new": last[0] if last else 0, "at": last[1] if last else None}


# ---------------------------------------------------------------------- #
# main
# ---------------------------------------------------------------------- #

def _send(subject, text, html, log=print):
    if os.environ.get("XCP_NOTIFY", "1") in ("0", "false", "no") or \
            os.environ.get("XCP_WATCHDOG_MAIL", "1") in ("0", "false", "no"):
        log("[watchdog] mail off (XCP_NOTIFY / XCP_WATCHDOG_MAIL)")
        return
    try:
        import accounts
    except Exception as exc:                                      # noqa: BLE001
        log(f"[watchdog] cannot load the mail code ({type(exc).__name__}: {exc})")
        return
    to = sorted(accounts.adminEmails())
    if not to or not accounts.mailEnabled():
        log("[watchdog] no admins or no mail provider -- not sent")
        return
    for addr in to:
        ok = accounts.sendMail(addr, subject, text, html=html, log_as="admin")
        log(f"[watchdog] mail {'sent' if ok else 'FAILED'}")


def run(conn, args, log=print):
    """The whole pass. Returns the report text."""
    now = datetime.datetime.now(datetime.timezone.utc)
    kind = args.kind or (runKind(args.log_dir) if args.log_dir else "nightly")
    failed_steps = (args.failed or "").split()
    findings, notes, ran = [], [], set()
    with conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
    if args.ensure_indexes and not args.dry_run:
        try:
            ensureIndexes(conn, log)
        except Exception as exc:                                  # noqa: BLE001
            conn.rollback()
            notes.append(f"could not build the scope indexes: {type(exc).__name__}: {exc}"[:200])
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")
        cur.execute("""SELECT max(started_at) FROM watchdog_run WHERE finished_at IS NOT NULL""")
        last = cur.fetchone()[0]
        since = args.since or last
        if since is None and args.log_dir:
            m = re.search(r"(\d{8})_(\d{6})", os.path.basename(os.path.normpath(args.log_dir)))
            if m:
                since = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").astimezone()
        if since is None:
            since = now - datetime.timedelta(days=1)
            notes.append("first run with no log dir: the scope is the last day")
        cur.execute("INSERT INTO watchdog_run (kind, log_dir, since) VALUES (%s, %s, %s) RETURNING run_id",
                    (kind, args.log_dir, since))
        run_id = cur.fetchone()[0]
        open_items = loadOpen(cur)
    conn.commit()

    subj = defaultdict(list)
    open_ids = {"performance": defaultdict(set), "jump": defaultdict(set)}
    for (c, _k), meta in open_items.items():
        s = meta.get("subject") or {}
        subj[c].append(s)
        if c in open_ids and s.get("sport"):
            open_ids[c][s["sport"]] |= set(s.get("result_ids", ()))

    def step(name, fn):
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = 0")
                got = fn(cur)
            conn.commit()
            ran.add(name)
            return got
        except Exception as exc:                                  # noqa: BLE001
            conn.rollback()
            notes.append(f"the {name} check did not run: {type(exc).__name__}: "
                         f"{str(exc).strip().splitlines()[0] if str(exc).strip() else ''}"[:220])
            return None

    scope = {"XC": [], "TF": []}

    def loadScope(cur):
        missing = [f"{t}.{c}" for t, c, _n in SCOPE_INDEXES[:2]
                   if not (_has(cur, t) and _indexed(cur, t, c))]
        if missing:
            raise RuntimeError(f"no index on {', '.join(missing)}: run with --ensure-indexes "
                               f"(or scripts/add_page_indexes.py); not scanning 60M rows instead")
        ids = defaultdict(set)
        for c in ("performance", "jump"):
            for s, v in open_ids[c].items():
                ids[s] |= v
        for sport in ("XC", "TF"):
            rows = scopeRows(cur, sport, since, ids[sport])
            for r in rows:
                r["_new"] = r["result_id"] not in ids[sport]
            scope[sport] = rows
        return True

    have_scope = step("scope", loadScope)
    if have_scope:
        log(f"[watchdog] since {since}: {len(scope['XC']):,} XC and {len(scope['TF']):,} TF rows")
        calib = step("calibration", RO.latestCalib)
        if not calib:
            notes.append("no rating_outlier_calib: the neighbour rule is off; the season rule runs")
        findings += step("performance", lambda cur: runPerformance(cur, scope, {
            s: open_ids["performance"][s] for s in ("XC", "TF")})) or []
        findings += step("jump", lambda cur: runJumps(cur, scope, open_ids["jump"], calib)) or []
        races = step("races", lambda cur: loadRaces(cur, scope, subj["meet"] + subj["duplicate"])) or {}
        findings += step("duplicate", lambda cur: runDuplicates(cur, scope, subj["duplicate"], races)) or []
        if "races" in ran:
            findings += runMeets(races)
            ran.add("meet")
        if "duplicate" in ran and "races" not in ran:
            ran.discard("duplicate")            # the twin half did not run
    else:
        notes.append("the row checks were skipped: tonight's rows could not be found cheaply")
    def moves(cur):
        if _has(cur, "person_link_log") and not _indexed(cur, "person_link_log", "linked_at"):
            raise RuntimeError("no index on person_link_log.linked_at: run with --ensure-indexes")
        return movedPersons(cur, since)
    moved = step("moves", moves) or {}
    if "moves" in ran:
        findings += step("career", lambda cur: runCareers(cur, moved, subj["career"])) or []
    board = step("board", lambda cur: runBoards(cur, kind, run_id))
    if board:
        findings += board[0]
    counts = step("counts", lambda cur: runCounts(cur, kind, run_id, scope,
                                                  bool(have_scope))) or ({}, [])
    findings += counts[1]
    findings += runPipeline(args.log_dir, failed_steps, args.log_root, args.step_name)
    ran.add("pipeline")

    tonight = {}
    for f in findings:
        tonight.setdefault((f.check, f.key), f)
    new, still, resolved = reconcile(open_items, tonight, ran)
    meta = {k: v["first_seen"] for k, v in open_items.items()}
    report = renderText(os.path.basename(os.path.normpath(args.log_dir)) if args.log_dir else f"run {run_id}",
                        tonight, set(new), meta, resolved, now, notes=notes)
    if args.dry_run:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM watchdog_run WHERE run_id = %s", (run_id,))
        conn.commit()
        return report
    with conn.cursor() as cur:
        saveFindings(cur, tonight, new, still, resolved, now)
        if board:
            for sport, pool, year, top, c in board[1]:
                cur.execute("""INSERT INTO watchdog_board_top (run_id, kind, sport, pool, year, person_ids, churn)
                               VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                            (run_id, kind, sport, pool, year, top, c))
            # the churn history stays; the ids only for tonight's board (the
            # next run's "previous")
            cur.execute("""UPDATE watchdog_board_top SET person_ids = '{}'
                           WHERE run_id <> %s AND cardinality(person_ids) > 0""", (run_id,))
        cur.execute("""UPDATE watchdog_run SET finished_at = now(), n_new = %s, n_open = %s,
                              n_resolved = %s, counts = %s, report = %s WHERE run_id = %s""",
                    (len(new), len(tonight), len(resolved), json.dumps(counts[0]), report, run_id))
    conn.commit()
    if args.log_dir and os.path.isdir(args.log_dir):
        with open(os.path.join(args.log_dir, "WATCHDOG.txt"), "w", encoding="utf-8") as f:
            f.write(report)
    if args.send and new:
        if failed_steps:
            log("[watchdog] failed steps tonight: the failure email carries this summary")
        else:
            import accounts
            subject, text, html = renderEmail(
                os.path.basename(os.path.normpath(args.log_dir)) if args.log_dir else f"run {run_id}",
                tonight, new, now, accounts.siteOrigin())
            _send(subject, text, html, log)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", help="this run's log directory (summary, WATCHDOG.txt)")
    ap.add_argument("--log-root", help="where run directories live (default: the log dir's parent)")
    ap.add_argument("--kind", choices=("nightly", "full"), help="default: from the log dir's name")
    ap.add_argument("--failed", default="", help="the run's FAILED list so far")
    ap.add_argument("--step-name", default=None,
                    help="this step's name in the summary (steps after it have not run yet)")
    ap.add_argument("--since", type=lambda s: datetime.datetime.fromisoformat(s).astimezone(),
                    help="scope start (default: the last watchdog run)")
    ap.add_argument("--send", action="store_true", help="mail the admins when there are new items")
    ap.add_argument("--ensure-indexes", action="store_true", help="build the BRIN scope indexes if missing")
    ap.add_argument("--dry-run", action="store_true", help="print the report; write nothing, mail nothing")
    ap.add_argument("--classify-10a", metavar="LOGDIR", help="print one run's 10a cause class and exit")
    args = ap.parse_args(argv)
    if args.classify_10a:
        d = args.classify_10a
        try:
            with open(os.path.join(d, "10a_board_sanity.log"), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            print(f"[watchdog] {exc}")
            return 1
        steps = parseSteps(summaryText(d))
        cls, detail = classify10a(text, [s for s, st in steps.items() if st == "failed"])
        print(f"10a_board_sanity in {os.path.basename(os.path.normpath(d))}: {cls}"
              + (f" -- {detail}" if detail else ""))
        return 0
    from database import getConn
    with getConn() as conn:
        report = run(conn, args)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
