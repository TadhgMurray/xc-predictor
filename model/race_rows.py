#!/usr/bin/env python3
"""
race_rows.py -- the training table for the race model: one row per runner
per cross country race, every input known the day before. READ ONLY on
the database; writes one .npz.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P -u model/race_rows.py --out /srv/pred/rows.npz                 # ~1 h
    # a smoke run: every runner, one month of races, a year of history (~10 min)
    $P -u model/race_rows.py --out /srv/pred/smoke.npz --hist-from 2023-08-01 \
        --from 2024-09-01 --to 2024-09-30 --val-from 2024-09-16 --train-frac 1

★ WHAT IT IS FOR (docs/PLAN-MODEL-2026-10.md, 2026-10-25; owner: "you're
  guessing what each runner in an entire race will run"). The race model
  predicts every runner's time in a race as two parts that add up:
    the race's level   how fast the whole field runs: course, distance,
                       race type, week of the season
    each runner        their time relative to the field
  starting from the ratings and learning corrections to each. So each row
  carries:
    y, base          ln(time) and the served rating form's ln(5K time),
                     each less the race's mean -- the runner part
    y_abs, base_abs  the same, not centred -- their race means give the
                     level part (y_abs - base_abs, averaged over the race)
    X                what might correct them: the form read several ways,
                     this season, last season, track, the break, grade and
                     level, the race and its course, and the runner's place
                     in this field and among their teammates
  model/race_trees.py learns both parts from X.

★ NOTHING FROM THE DAY OR AFTER. Every runner input reads rated races
  dated strictly before the race; the field and teammate inputs are built
  from the OTHER runners' same prior-only inputs, never from the race's
  results. tests/test_race_rows.py appends a later race to a history and
  asserts no input moves.

⚠ RATINGS ARE TODAY'S SOLVE (the plan's Tier 1): every season's ratings
  were fitted with every season in view. That flatters the ratings and the
  model alike; the comparison between them holds.

★ SAMPLED BY RACE, NEVER BY ROW. A race is kept or dropped whole (crc32 of
  its key), because y and base are relative to the race. Every race from
  --val-from on is kept; earlier ones at --train-frac.
"""
import argparse
import datetime
import heapq
import math
import os
import statistics
import sys
import zlib
from array import array

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

MIN_FIELD = 8            # rated runners a race needs to say anything about order
SEASON_START_MONTH = 8   # season_year.ACADEMIC_START_MONTH
_LEVEL = {"elem": 0.0, "ms": 1.0, "hs": 2.0, "college": 3.0, "pro": 4.0}
_GRADE = {"FR": 13.0, "SO": 14.0, "JR": 15.0, "SR": 16.0}

# The runner inputs, in column order. Everything rated is a log ratio to the
# served form (so 0 = "same as the form"); days are days; NaN = unknown.
RUNNER_FEATURES = (
    "form_ln", "form_sigma", "form_n", "stale_years",
    "last_minus_form", "last_days", "last_xc_days", "last_is_tf",
    "ewma30_minus_form", "ewma120_minus_form", "best2_minus_form",
    "season_n", "season_mean_minus_form", "season_max_minus_form",
    "season_slope", "season_days", "opener",
    "prev_n", "prev_final_minus_form", "prev_best_minus_form",
    "prev_mean_minus_form", "prev_late_minus_early", "prev_final_days",
    "tf_last_minus_form", "tf_last_days", "tf_best365_minus_form", "tf_n365",
    "career_n", "career_years", "xc_seasons",
    "grade", "level", "female", "form_level", "level_change",
)
RACE_FEATURES = (
    "field_n", "rank_pct", "gap_to_median", "gap_ahead", "gap_behind",
    "team_n", "team_slope", "team_summer", "team_openers",
    "distance", "season_week", "meet_type", "is_tfrrs",
    "course_cell", "has_cell", "page_base",
    "temp_c", "dew_point_c", "precip_mm", "wind_kmh",
    "h2h_n", "h2h_share", "h2h_vs_base",
)
H2H_DAYS = 400           # a meeting older than this says little about Saturday
H2H_LAST = 15            # the runner's last N cross country races, at most
FEATURES = RUNNER_FEATURES + RACE_FEATURES

# ★ HOW MUCH A RACE IS RUN FOR, from its name (0 = unknown). Ordered, so
#   one tree split can say "championship or above".
_MEET_TYPES = (
    (6, ("nxn", "nike cross nationals", "foot locker", "eastbay", "champs sports",
         "ncaa division", "ncaa championship", "naia national", "njcaa national")),
    (5, ("state champ", "state meet", "state final", "state cross country champ",
         "ncaa regional", "region champ", "regional champ")),
    (4, ("section", "sectional", "district", "conference", "league champ",
         "county champ", "city champ", "championship", "champs")),
    (2, ("invit", "classic", "jamboree", "preview", "festival", "opener")),
    (1, ("dual", "tri-meet", "tri meet", "quad", " vs ", " vs.")),
    (0, ("jv", "junior varsity", "frosh", "freshman", "novice", "reserve")),
)


# ------------------------------------------------------------------ #
#  pure parts
# ------------------------------------------------------------------ #

def _num(v):
    try:
        return float(v) if v is not None else math.nan
    except (TypeError, ValueError):
        return math.nan


def meetType(name):
    """0..6 from a meet's name; 3 = an ordinary meet. Pure."""
    n = f" {(name or '').lower()} "
    for score, words in reversed(_MEET_TYPES):           # sub-varsity first
        if score == 0 and any(w in n for w in words):
            return 0.0
    for score, words in _MEET_TYPES:
        if score and any(w in n for w in words):
            return float(score)
    return 3.0


def seasonOf(d):
    """The academic year a date (date) falls in."""
    return d.year if d.month >= SEASON_START_MONTH else d.year - 1


def gradeNum(g):
    g = (g or "").strip().upper()
    if g[:2] in _GRADE:
        return _GRADE[g[:2]]
    try:
        v = float(g)
        return v if 1 <= v <= 12 else math.nan
    except ValueError:
        return math.nan


def levelOf(pool):
    p = (pool or "").split("|")[0]
    for k, v in _LEVEL.items():
        if p.startswith(k):
            return v
    return math.nan


def _slope(pts):
    """ln-rating per 30 days by least squares over (day, ln rating); 0.0
    with fewer than three races or under three weeks of span."""
    if len(pts) < 3:
        return 0.0
    xs = [x for x, _y in pts]
    if max(xs) - min(xs) < 21:
        return 0.0
    mx = statistics.fmean(xs)
    my = statistics.fmean(y for _x, y in pts)
    sxx = sum((x - mx) ** 2 for x in xs)
    return 30.0 * sum((x - mx) * (y - my) for x, y in pts) / sxx


def _ewma(rows, d0, half_life):
    """Recency-weighted geometric mean of ratings. rows: (date, rating)."""
    lam = math.log(2.0) / half_life
    w = [math.exp(-lam * (d0 - d).days) for d, _r in rows]
    return math.exp(sum(wi * math.log(r) for wi, (_d, r) in zip(w, rows)) / sum(w))


def runnerFeatures(hist, day, target):
    """The runner inputs for one race. Pure.

    hist: the runner's rated, non-outlier races, oldest first, each
          (date, rating, pool, sport) with date a datetime.date. Rows on or
          after `day` are ignored, so the caller can pass the whole career.
    day:  the race's date.
    target: {"pool", "grade"} of the runner's row in this race.
    Returns (form_rating, form_pool, {feature: value}) or None when the
    runner has no rated race before the day."""
    import predict as P
    prior = [h for h in hist if h[0] < day]
    if not prior:
        return None
    newest = [(h[0].isoformat(), h[1], h[2], h[3], None, None) for h in reversed(prior)]
    lo = (day - datetime.timedelta(days=365)).isoformat()
    recent = [r for r in newest if r[0] >= lo]
    form = P._formRating(recent or newest, "XC")
    if form is None:
        return None
    rating, sig, pool, n = form
    stale = 0.0 if recent else max(0.0, (day - prior[-1][0]).days / 365.25 - 1.0)
    lf = math.log(rating)
    nan = math.nan

    def rel(r):
        return math.log(r) - lf if r and r > 0 else nan

    xc = [h for h in prior if h[3] == "XC"]
    xc_pool = [h for h in xc if h[2] == pool] or xc
    tf = [h for h in prior if h[3] == "TF"]
    season, prev_season = seasonOf(day), seasonOf(day) - 1
    this = [h for h in xc_pool if seasonOf(h[0]) == season]
    prev = [h for h in xc if seasonOf(h[0]) == prev_season]
    last = prior[-1]
    yr = [h for h in xc_pool if (day - h[0]).days <= 365] or xc_pool
    best2 = sorted((h[1] for h in yr), reverse=True)[:2]
    tf365 = [h for h in tf if (day - h[0]).days <= 365]
    f = {
        "form_ln": lf, "form_sigma": sig, "form_n": float(n), "stale_years": stale,
        "last_minus_form": rel(last[1]), "last_days": float((day - last[0]).days),
        "last_xc_days": float((day - xc[-1][0]).days) if xc else nan,
        "last_is_tf": 1.0 if last[3] == "TF" else 0.0,
        "ewma30_minus_form": rel(_ewma([(h[0], h[1]) for h in xc_pool], day, 30.0))
        if xc_pool else nan,
        "ewma120_minus_form": rel(_ewma([(h[0], h[1]) for h in xc_pool], day, 120.0))
        if xc_pool else nan,
        "best2_minus_form": (rel(math.exp(statistics.fmean(math.log(b) for b in best2)))
                             if best2 else nan),
        "season_n": float(len(this)),
        "season_mean_minus_form": rel(statistics.fmean(h[1] for h in this)) if this else nan,
        "season_max_minus_form": rel(max(h[1] for h in this)) if this else nan,
        "season_slope": _slope([(-(day - h[0]).days, math.log(h[1])) for h in this]),
        "season_days": float((day - this[0][0]).days) if this else nan,
        "opener": 0.0 if this else 1.0,
        "prev_n": float(len(prev)),
        "prev_final_minus_form": rel(prev[-1][1]) if prev else nan,
        "prev_best_minus_form": rel(max(h[1] for h in prev)) if prev else nan,
        "prev_mean_minus_form": rel(statistics.fmean(h[1] for h in prev)) if prev else nan,
        "prev_late_minus_early": (math.log(statistics.fmean(h[1] for h in prev[-(len(prev) // 3 or 1):]))
                                  - math.log(statistics.fmean(h[1] for h in prev[:len(prev) // 3 or 1])))
        if len(prev) >= 3 else nan,
        "prev_final_days": float((day - prev[-1][0]).days) if prev else nan,
        "tf_last_minus_form": rel(tf[-1][1]) if tf else nan,
        "tf_last_days": float((day - tf[-1][0]).days) if tf else nan,
        "tf_best365_minus_form": rel(max(h[1] for h in tf365)) if tf365 else nan,
        "tf_n365": float(len(tf365)),
        "career_n": float(len(prior)),
        "career_years": (day - prior[0][0]).days / 365.25,
        "xc_seasons": float(len({seasonOf(h[0]) for h in xc})),
        "grade": gradeNum(target.get("grade")),
        "level": levelOf(target.get("pool") or pool),
        "female": 1.0 if (target.get("pool") or pool or "").split("|")[0].endswith("_f") else 0.0,
        "form_level": levelOf(pool),
    }
    f["level_change"] = (f["level"] - f["form_level"]
                         if not (math.isnan(f["level"]) or math.isnan(f["form_level"])) else nan)
    return rating, pool, f


def raceFeatures(runners, race):
    """The field and teammate inputs, filled into each runner's dict in
    place, and y / base centred on the race. Pure.

    runners: [{"secs", "base_ln", "school", "f": {...}}] -- every rated
             runner of one race, base_ln = ln of the form's time
    race:    {"distance", "day", "meet_name", "source", "is_champ"}"""
    n = len(runners)
    ys = [math.log(r["secs"]) for r in runners]
    bs = [r["base_ln"] for r in runners]
    my, mb = statistics.fmean(ys), statistics.fmean(bs)
    order = sorted(range(n), key=lambda i: bs[i])
    rank = {i: k for k, i in enumerate(order)}
    med = statistics.median(bs)
    h2h = headToHead(runners)
    wx = race.get("weather") or {}
    teams = {}
    for i, r in enumerate(runners):
        teams.setdefault(r.get("school") or f"_none{i}", []).append(i)
    day = race["day"]
    week = (day - datetime.date(seasonOf(day), SEASON_START_MONTH, 1)).days / 7.0
    mt = 5.0 if race.get("is_champ") and meetType(race.get("meet_name")) < 4 else \
        meetType(race.get("meet_name"))
    for i, r in enumerate(runners):
        k = rank[i]
        mates = [j for j in teams[r.get("school") or f"_none{i}"] if j != i]

        def med_of(key):
            v = [runners[j]["f"][key] for j in mates if not math.isnan(runners[j]["f"][key])]
            return statistics.median(v) if v else math.nan
        r["f"].update({
            "field_n": float(n), "rank_pct": k / max(n - 1, 1),
            "gap_to_median": bs[i] - med,
            "gap_ahead": bs[i] - bs[order[k - 1]] if k > 0 else math.nan,
            "gap_behind": bs[order[k + 1]] - bs[i] if k + 1 < n else math.nan,
            "team_n": float(len(mates)),
            "team_slope": med_of("season_slope"),
            "team_summer": med_of("prev_final_minus_form"),
            "team_openers": (statistics.fmean(runners[j]["f"]["opener"] for j in mates)
                             if mates else math.nan),
            "distance": float(race.get("distance") or math.nan),
            "season_week": week, "meet_type": mt,
            "is_tfrrs": 1.0 if race.get("source") == "tfrrs" else 0.0,
            "course_cell": (float(race["cell"]) if race.get("cell") is not None
                            else math.nan),
            "has_cell": 1.0 if race.get("cell") is not None else 0.0,
            "page_base": 1.0 if race.get("page_base") else 0.0,
            "temp_c": _num(wx.get("temp_c")), "dew_point_c": _num(wx.get("dew_point_c")),
            "precip_mm": _num(wx.get("precipitation_mm")),
            "wind_kmh": _num(wx.get("wind_speed_kmh", wx.get("wind_speed_km"))),
            "h2h_n": float(h2h[i][0]) if i in h2h else 0.0,
            "h2h_share": h2h[i][1] if i in h2h else math.nan,
            "h2h_vs_base": h2h[i][2] if i in h2h else math.nan,
        })
        r["y"] = ys[i] - my
        r["base"] = bs[i] - mb
        r["y_abs"], r["base_abs"] = ys[i], bs[i]
    return runners


# ★ THE PAGE'S OWN CLOCK (2026-10-25, the first run's times were the weak
#   part). base is what the site would PRINT for this runner at this race:
#   their form through predict._ratingToSeconds on the race's distance and
#   course cell, under the production venue flag. So "ratings" in every
#   comparison is the page, and the level part corrects the page's clock.
#   A race where any runner cannot be converted (no distance) keeps the
#   5K-equivalent for all of them, and page_base says which it got.
_SERVED = {}


def servedLn(rating, pool, race):
    """ln(seconds) the page would print, or None. Cached on a rating
    rounded to 0.02 (0.02% of a time)."""
    import predict as P
    dist = race.get("distance")
    if not dist:
        return None
    key = (round(rating, 2), pool, float(dist), race.get("cell"), race["day"].year)
    if key not in _SERVED:
        spec = {"distance_meters": float(dist), "sport": "XC", "course_name": None,
                "venue_cell": race.get("cell"),
                "course_difficulty": race.get("cell") or 0.0, "canonical_id": None}
        try:
            secs = P._ratingToSeconds(key[0], pool, spec, key[4])
        except Exception:                                # noqa: BLE001
            secs = None
        _SERVED[key] = math.log(secs) if secs and secs > 0 else None
    return _SERVED[key]


def pageBase(field, race):
    """base_ln on the page's clock for the whole field, else left as the
    5K-equivalent; race["page_base"] records which. In place."""
    got = [servedLn(r["rating"], r["pool"], race) for r in field]
    race["page_base"] = all(g is not None for g in got)
    if race["page_base"]:
        for r, g in zip(field, got):
            r["base_ln"] = g
    return field


def raceHash(meet_id, div_id, source, day_s):
    """One past race, as an int both the table and the page compute alike."""
    return zlib.crc32(repr((int(meet_id), int(div_id), str(source), str(day_s)[:10])).encode())


def headToHead(runners):
    """{index: (meetings, share beaten, share beaten less what the ratings
    expect)} from the races this field's runners met in before. Pure.

    ★ PRIOR RACES ONLY, AND BEYOND THE RATINGS. Each runner carries "past":
      [(raceHash, ln time)] of their own earlier races, so nothing here sees
      the race being predicted. In each past race two or more of them ran,
      every co-runner is a meeting: beaten or not, against what today's
      base (the ratings' order now) says should have happened. A runner who
      keeps beating people the ratings rank above them shows it here."""
    by = {}
    for i, r in enumerate(runners):
        for h, t in r.get("past") or ():
            by.setdefault(h, []).append((i, t))
    from bisect import bisect_left, bisect_right
    wins, games, exp = {}, {}, {}
    # ! BY SORTING, NOT BY PAIRS: a league that meets every week puts dozens
    #   of this field in each past race, and pairs would be m^2 a race
    for met in by.values():
        m = len(met)
        if m < 2:
            continue
        ts = sorted(t for _i, t in met)
        bs = sorted(runners[i]["base_ln"] for i, _t in met)
        for i, ti in met:
            bi = runners[i]["base_ln"]
            same_t = bisect_right(ts, ti) - bisect_left(ts, ti)
            g = m - same_t                          # equal times are no evidence
            if not g:
                continue
            games[i] = games.get(i, 0) + g
            wins[i] = wins.get(i, 0) + (m - bisect_right(ts, ti))
            same_b = bisect_right(bs, bi) - bisect_left(bs, bi) - 1
            exp[i] = exp.get(i, 0.0) + (m - bisect_right(bs, bi)) + 0.5 * same_b
    return {i: (g, wins[i] / g, (wins[i] - exp[i]) / g) for i, g in games.items()}


def raceDistance(dists):
    """The race's distance from its rows' corrected distances: the median,
    or None when nothing sane is there (DIST_OK). Pure."""
    ok = sorted(float(d) for d in dists or [] if d and DIST_OK[0] <= float(d) <= DIST_OK[1])
    return ok[len(ok) // 2] if ok else None


def keepRace(key, day, val_from, train_frac):
    """Every race from val_from on; earlier ones by crc32 of their key."""
    if day >= val_from:
        return True
    return (zlib.crc32(repr(key).encode()) % 10000) < train_frac * 10000


# ------------------------------------------------------------------ #
#  the database side
# ------------------------------------------------------------------ #

def _stream(conn, sql, params, name):
    with conn.cursor(name=name) as cur:
        cur.itersize = 50_000
        cur.execute(sql, params)
        for row in cur:
            yield row


# ★ THE CORRECTED DISTANCE, THE CORPUS'S OWN EXPRESSION (2026-10-26: the
#   first page-clock run's worst races were stored at 1, 2 and 3 m and read
#   8x fast). feature_extraction._XC_DIST is the hand-verified override, then
#   the meet's distance, then a tfrrs division's own -- the distance every
#   normalized time and every course cell was computed at. Imported, not
#   copied, and only here: it needs the server's corrections.py.
def _xcDistance():
    from feature_extraction import _XC_DIST, _OV_JOIN_XC
    joins = f"""
        LEFT JOIN meets m ON r.div_id = m.div_id AND r.meet_id = m.meet_id
                         AND r.source = m.source
        LEFT JOIN meets_tfrrs mt ON r.source = 'tfrrs' AND mt.meet_id = r.meet_id
                                AND mt.sport = 'XC'
{_OV_JOIN_XC}"""
    return _XC_DIST, joins


# A race distance outside this is a unit or a typo (miles, kilometres, a
# relay leg), not a cross country race: the race keeps no distance.
DIST_OK = (1500.0, 15000.0)


def _sql(has_outliers):
    from feature_extraction import escapeLiteralPercent
    dist, joins = _xcDistance()
    skip = ("AND NOT EXISTS (SELECT 1 FROM rating_outlier ro WHERE ro.sport = '{s}' "
            "AND ro.result_id = r.result_id)")
    xc = escapeLiteralPercent(f"""
        SELECT r.person_id, left(r.date, 10), r.speed_rating,
               split_part(r.rating_pool, '|', 1), r.time_seconds,
               r.meet_id, r.div_id, r.source, r.school, r.grade, 0, {dist}
        FROM   results r
{joins}
        WHERE  r.person_id IS NOT NULL AND r.date >= %(lo)s AND r.date < %(hi)s
          AND  r.time_seconds > 0 AND r.time_seconds < 19999
          AND  NOT EXISTS (SELECT 1 FROM result_twin x
                           WHERE x.sport = 'XC' AND x.result_id = r.result_id)
          {skip.format(s='XC') if has_outliers else ''}
        ORDER  BY r.person_id, r.date""")
    tf = f"""
        SELECT r.person_id, left(r.date, 10), r.speed_rating,
               split_part(r.rating_pool, '|', 1), NULL, NULL, NULL, NULL, NULL, NULL, 1,
               NULL
        FROM   results_tf r
        WHERE  r.person_id IS NOT NULL AND r.date >= %(lo)s AND r.date < %(hi)s
          AND  r.speed_rating > 0 AND r.rating_pool IS NOT NULL
          AND  COALESCE(r.is_field, 0) = 0 AND COALESCE(r.is_relay, 0) = 0
          {skip.format(s='TF') if has_outliers else ''}
        ORDER  BY r.person_id, r.date"""
    return xc, tf


def _people(conn, lo, hi, has_outliers):
    """Each person's rows, both sports, oldest first: heap-merged streams."""
    xc_sql, tf_sql = _sql(has_outliers)
    p = {"lo": lo, "hi": hi}
    merged = heapq.merge(_stream(conn, xc_sql, p, "rr_xc"),
                         _stream(conn, tf_sql, p, "rr_tf"),
                         key=lambda r: (r[0], r[1], r[10]))
    pid, rows = None, []
    for r in merged:
        if r[0] != pid:
            if rows:
                yield pid, rows
            pid, rows = r[0], []
        rows.append(r)
    if rows:
        yield pid, rows


def _raceMeta(cur, keys, dist_of=None):
    """{(meet_id, div_id, source): {distance, meet_name, is_champ}}."""
    anet = sorted({k[1] for k in keys if k[2] != "tfrrs"})
    tfr = sorted({k[0] for k in keys if k[2] == "tfrrs"})
    out = {}
    # ★ THE COURSE BY THE SAME JOIN THE PREDICTOR USES (name + gps rounded to
    #   5 places -> course_canonical), then its cell at the race's distance
    #   (rounded to 100 m) below. ⚠ Today's cells: they saw these races.
    canon_join = ("LEFT JOIN course_canonical cc ON cc.course_name = {name} "
                  "AND round(cc.gps_lat::numeric, 5) = round({lat}::numeric, 5) "
                  "AND round(cc.gps_long::numeric, 5) = round({lon}::numeric, 5)")
    for i in range(0, len(anet), 5000):
        cur.execute("SELECT m.div_id, m.source, m.meet_name, m.distance, cc.canonical_id "
                    "FROM meets m " + canon_join.format(name="m.course_name", lat="m.gps_lat",
                                                        lon="m.gps_long")
                    + " WHERE m.div_id = ANY(%s)", (anet[i:i + 5000],))
        for div, src, name, dist, cid in cur.fetchall():
            out[(src, div)] = {"meet_name": name, "distance": dist, "canonical_id": cid}
    for i in range(0, len(tfr), 5000):
        cur.execute("SELECT mt.meet_id, mt.meet_name, COALESCE(mt.is_championship, 0), "
                    "mt.division_distances, mt.distance, cc.canonical_id "
                    "FROM meets_tfrrs mt " + canon_join.format(name="mt.venue_name",
                                                               lat="mt.gps_lat",
                                                               lon="mt.gps_long")
                    + " WHERE mt.sport = 'XC' AND mt.meet_id = ANY(%s)", (tfr[i:i + 5000],))
        for meet, name, champ, dd, dist, cid in cur.fetchall():
            out[("tfrrs", meet)] = {"meet_name": name, "is_champ": bool(champ),
                                    "divs": dd if isinstance(dd, dict) else {},
                                    "distance": dist, "canonical_id": cid}
    # ★ THE DAY'S WEATHER at the cross country hour (feature_extraction's
    #   XC_DEFAULT_HOUR); the page passes the forecast, or the normal for the
    #   place and date (race_model.predictRace)
    wx = {}
    cur.execute("SELECT to_regclass('public.weather')")
    if (cur.fetchone() or [None])[0]:
        meets = sorted({k[0] for k in keys})
        for i in range(0, len(meets), 5000):
            cur.execute("SELECT meet_id, source, temp_c, dew_point_c, precipitation_mm, "
                        "wind_speed_kmh FROM weather WHERE hour = 9 AND meet_id = ANY(%s)",
                        (meets[i:i + 5000],))
            for meet, src, t, dp, pr, wd in cur.fetchall():
                wx[(src, meet)] = {"temp_c": t, "dew_point_c": dp, "precipitation_mm": pr,
                                   "wind_speed_kmh": wd}
    cids = sorted({m["canonical_id"] for m in out.values() if m.get("canonical_id") is not None})
    cells = {}
    for i in range(0, len(cids), 5000):
        cur.execute("SELECT canonical_id, distance_m, difficulty FROM course_difficulties "
                    "WHERE canonical_id = ANY(%s)", (cids[i:i + 5000],))
        for cid, dm, diff in cur.fetchall():
            cells[(cid, int(dm))] = diff
    res = {}
    for k in keys:
        if k[2] == "tfrrs":
            m = out.get(("tfrrs", k[0])) or {}
            d = ((m.get("divs") or {}).get(str(k[1])) or {}).get("distance") or m.get("distance")
            res[k] = {"meet_name": m.get("meet_name"), "is_champ": m.get("is_champ"),
                      "distance": d, "canonical_id": m.get("canonical_id")}
        else:
            res[k] = dict(out.get((k[2], k[1])) or {})
        # the rows' corrected distance wins; a stored one only if it is sane
        d = (dist_of or {}).get(k) or raceDistance([res[k].get("distance")])
        res[k]["distance"] = d
        res[k]["weather"] = wx.get((k[2], k[0]))
        cid = res[k].get("canonical_id")
        try:
            res[k]["cell"] = (cells.get((cid, int(round(float(d) / 100.0) * 100)))
                              if cid is not None and d else None)
        except (TypeError, ValueError):
            res[k]["cell"] = None
    return res


def build(conn, a):
    import conversions
    from predict import _outlierFilter
    cur = conn.cursor()
    has_out = bool(_outlierFilter(cur, "XC"))
    conn.rollback()
    lo_t = datetime.date.fromisoformat(a.lo)
    hi_t = datetime.date.fromisoformat(a.hi)
    val_from = datetime.date.fromisoformat(a.val_from)
    pm = {}
    races = {}            # key -> [runner tuple]
    race_dist = {}        # key -> the rows' corrected distances
    n_people = 0
    for pid, rows in _people(conn, a.hist_from, (hi_t + datetime.timedelta(days=1)).isoformat(),
                             has_out):
        n_people += 1
        hist = []
        past = []             # (date, raceHash, ln time) of their XC races
        i = 0
        while i < len(rows):
            day_s = rows[i][1]
            j = i
            while j < len(rows) and rows[j][1] == day_s:
                j += 1
            try:
                day = datetime.date.fromisoformat(day_s)
            except ValueError:
                i = j
                continue
            if lo_t <= day <= hi_t:
                for r in rows[i:j]:
                    if r[10] != 0 or not r[4]:
                        continue
                    key = (r[5], r[6], r[7], day_s)
                    if not keepRace(key, day, val_from, a.train_frac):
                        continue
                    got = runnerFeatures(hist, day, {"pool": r[3], "grade": r[9]})
                    if got is None:
                        continue
                    rating, pool, f = got
                    if pool not in pm:
                        pm[pool] = conversions.pool_mean(pool, "XC") or 0.0
                    if not pm[pool]:
                        continue
                    # ! COMPACT UNTIL THE END: millions of rows wait here for
                    #   their races to fill, and a dict per row is ~3 KB
                    recent = [x for x in past if (day - x[0]).days <= H2H_DAYS][-H2H_LAST:]
                    races.setdefault(key, []).append((
                        pid, float(r[4]), r[8], math.log(100.0 * pm[pool] / rating),
                        array("f", [f[k] for k in RUNNER_FEATURES]), rating, pool,
                        array("q", [x[1] for x in recent]), array("f", [x[2] for x in recent])))
                    if r[11]:
                        race_dist.setdefault(key, []).append(float(r[11]))
            for r in rows[i:j]:
                if r[2] and r[2] > 0 and r[3]:
                    hist.append((day, float(r[2]), r[3], "TF" if r[10] else "XC"))
                if r[10] == 0 and r[4] and r[5] is not None and r[6] is not None:
                    past.append((day, raceHash(r[5], r[6], r[7], day_s), math.log(float(r[4]))))
            i = j
        if n_people % 100_000 == 0:
            print(f"  {n_people:,} people, {len(races):,} races so far", flush=True)
        if a.max_persons and n_people >= a.max_persons:
            break
    conn.rollback()
    races = {k: v for k, v in races.items() if len(v) >= MIN_FIELD}
    print(f"{n_people:,} people read; {len(races):,} races with >= {MIN_FIELD} rated runners",
          flush=True)
    dist_of = {k: raceDistance(race_dist.get(k)) for k in races}
    meta = _raceMeta(cur, list(races), dist_of)
    conn.rollback()
    cols = {k: array("f") for k in FEATURES}
    y, base, race_ix, day_ix, pid_col = array("f"), array("f"), array("i"), array("i"), array("q")
    y_abs, base_abs = array("f"), array("f")
    src_col = array("b")
    keys = sorted(races)
    # ! THE PRODUCTION VENUE FLAG, so a course with no cell converts as the
    #   site converts it (the median race) and nothing asks the database
    os.environ.setdefault("XCP_PREDICT_VENUE_DIFFICULTY", "xc")
    names, dists, n_page = [], [], 0
    for ix, key in enumerate(keys):
        if ix % 50_000 == 0:
            print(f"  race inputs: {ix:,} of {len(keys):,}", flush=True)
        day = datetime.date.fromisoformat(key[3])
        rmeta = dict(meta.get(key) or {}, day=day, source=key[2])
        field = [{"pid": p, "secs": secs, "school": school, "base_ln": b,
                  "f": dict(zip(RUNNER_FEATURES, vec)), "rating": rating, "pool": pool,
                  "past": list(zip(ph, pt))}
                 for p, secs, school, b, vec, rating, pool, ph, pt in races.pop(key)]
        pageBase(field, rmeta)
        n_page += rmeta["page_base"]
        names.append(str(rmeta.get("meet_name") or ""))
        dists.append(float(rmeta.get("distance") or 0.0))
        for r in raceFeatures(field, rmeta):
            for k in FEATURES:
                cols[k].append(r["f"][k])
            y.append(r["y"])
            base.append(r["base"])
            y_abs.append(r["y_abs"])
            base_abs.append(r["base_abs"])
            race_ix.append(ix)
            day_ix.append(day.toordinal())
            pid_col.append(int(r["pid"]))
            src_col.append(1 if key[2] == "tfrrs" else 0)
    print(f"  {n_page:,} of {len(keys):,} races on the page's clock; the rest on the "
          "5K-equivalent", flush=True)
    return (cols, y, base, y_abs, base_abs, race_ix, day_ix, pid_col, src_col, keys,
            names, dists)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hist-from", default="2014-08-01",
                    help="history read from here (inputs only)")
    ap.add_argument("--from", dest="lo", default="2018-08-01", help="first race day")
    ap.add_argument("--to", dest="hi", default="2025-07-31",
                    help="last race day (default: before the sealed 2025-26 season)")
    ap.add_argument("--val-from", default="2024-08-01",
                    help="races from here on are all kept: the validation season")
    ap.add_argument("--train-frac", type=float, default=0.25,
                    help="share of earlier races kept, whole races")
    # ! NOT A SMOKE RUN: a race's runners are spread over every person id,
    #   so the first N people complete almost no race (2026-10-25: 0 rows).
    #   Narrow the dates instead (the docstring's smoke line).
    ap.add_argument("--max-persons", type=int, default=None,
                    help="stop after N people (a code check only; completes few races)")
    a = ap.parse_args()

    import numpy as np
    from database import getConn
    t0 = datetime.datetime.now()
    with getConn() as conn:
        (cols, y, base, y_abs, base_abs, race_ix, day_ix, pid, src, keys,
         names, dists) = build(conn, a)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    np.savez_compressed(
        a.out, X=np.stack([np.frombuffer(cols[k], dtype=np.float32) for k in FEATURES], axis=1),
        y=np.frombuffer(y, dtype=np.float32), base=np.frombuffer(base, dtype=np.float32),
        y_abs=np.frombuffer(y_abs, dtype=np.float32),
        base_abs=np.frombuffer(base_abs, dtype=np.float32),
        race=np.frombuffer(race_ix, dtype=np.int32), day=np.frombuffer(day_ix, dtype=np.int32),
        pid=np.frombuffer(pid, dtype=np.int64), tfrrs=np.frombuffer(src, dtype=np.int8),
        features=np.array(FEATURES), race_keys=np.array([repr(k) for k in keys]),
        race_names=np.array(names), race_distance=np.array(dists, dtype=np.float32),
        args=np.array([repr(vars(a))]))
    if not len(y):
        print("⚠ NO RACES: nothing in the window had enough rated runners")
    print(f"wrote {a.out}: {len(y):,} runner-races in {len(keys):,} races, "
          f"{len(FEATURES)} inputs ({datetime.datetime.now() - t0})")


if __name__ == "__main__":
    main()
