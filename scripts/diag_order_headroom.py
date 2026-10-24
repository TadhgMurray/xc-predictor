#!/usr/bin/env python3
"""
diag_order_headroom.py -- E0 of docs/PLAN-MODEL-2026-10.md: how much better
could ANY model order a cross country race than the ratings already do?
READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/diag_order_headroom.py --from 2024-08-01 --to 2025-07-31 --divs 300
    $P scripts/diag_order_headroom.py --from 2025-08-01 --to 2026-07-31 --divs 300

★ A CEILING, NOT A RESULT. Four ways to order the same runners in the
  same races:

    last      their last rated race before the day
    prior     the served rating form (predict._ratingTimes, history cut the
              day before): what the page does today
    season    HINDSIGHT. The mean rating of every OTHER race of theirs that
              season, earlier AND later, in the same pool. It uses races
              after the one being predicted, so no forecast can reach it.
    nearest   HINDSIGHT. The two nearest other races on either side of the
              day, weighted by closeness: form on the day, as well as the
              season's results can say it.

  prior -> nearest is the most that better FORM estimation could buy:
  trend, rust, peaking, anything that is a fact about the runner that day.
  What nearest still gets wrong is mostly the day itself, which nothing
  before the gun knows. If the gap is small, the network cannot win much on
  order and the money is better spent on the race level; if it is large,
  the plan's order work is worth its GPU time.

  Per race, on the runners all four can price:
    pairs     share of runner pairs in the finishing order (ties in a
              prediction count half; equal times are left out), the same
              statistic as kailo_compare's and forward_holdout's
    rho       Spearman
    spread    median |within-race miss| %: ln(pred / actual) less the
              race's own median. Its square splits into form error plus
              day noise, so  form error ~ sqrt(prior^2 - nearest^2)
  then the means over races, the paired differences with a 90% interval
  by resampling races, and the same by level and by month.

★ --with-model ADDS THE NETWORK (E1a, 2026-10-24): model.pt through the
  page's own path, history cut the day before. model.pt trained on every
  season, so its numbers here are in-sample unless --val-only keeps only
  the runners it never trained on -- and even then the venue and the
  races were seen through other runners. Read it as the network's best
  case: if it cannot beat prior here, it does not beat it.

⚠ TODAY'S SOLVE FOR EVERY RATING. Every basis reads ratings that saw these
  races, the hindsight bases on purpose and prior mildly (sweep A9).
  Compare the bases; read the absolute numbers as optimistic.
"""
import argparse
import datetime
import math
import os
import random
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

MIN_MATCHED = 8
NEAREST_K = 2            # races on EACH side of the day
OUTLIER_PTS = 20.0       # predict._RATING_OUTLIER_PTS: a fall or a jog is not form
BASES = ("last", "prior", "season", "nearest")


# ------------------------------------------------------------------ #
#  pure parts
# ------------------------------------------------------------------ #

def pairShare(pred, actual):
    """Share of pairs in the actual order; equal actual times are no
    evidence, equal predictions count one half. None with no pairs."""
    good = n = 0.0
    for i in range(len(pred)):
        for k in range(i + 1, len(pred)):
            if actual[i] == actual[k]:
                continue
            n += 1
            if pred[i] == pred[k]:
                good += 0.5
            else:
                good += (pred[i] < pred[k]) == (actual[i] < actual[k])
    return good / n if n else None


def _trimmed(vals):
    """The ratings with the slow outliers dropped (predict._formRating's rule)."""
    if not vals:
        return []
    med = sorted(vals)[len(vals) // 2]
    return [v for v in vals if v >= med - OUTLIER_PTS]


def seasonForm(rows, day):
    """Hindsight: the mean rating of the season's OTHER races, either side
    of `day`, in the pool of the race on `day` (else the newest). rows:
    [(date, rating, pool)], one runner, one season. (rating, pool) or None."""
    pool = _poolOn(rows, day)
    vals = _trimmed([r for d, r, p in rows if p == pool and d != day])
    return (statistics.fmean(vals), pool) if vals else None


def nearestForm(rows, day, k=NEAREST_K):
    """Hindsight: the k nearest other races on each side of `day`, weighted
    1 / (1 + weeks away). (rating, pool) or None."""
    pool = _poolOn(rows, day)
    d0 = datetime.date.fromisoformat(day)
    mine = [(datetime.date.fromisoformat(d), r) for d, r, p in rows
            if p == pool and d != day]
    before = sorted((x for x in mine if x[0] < d0), key=lambda x: x[0])[-k:]
    after = sorted((x for x in mine if x[0] > d0), key=lambda x: x[0])[:k]
    use = before + after
    keep = set(_trimmed([r for _d, r in use]))
    use = [(d, r) for d, r in use if r in keep]
    if not use:
        return None
    w = [1.0 / (1.0 + abs((d - d0).days) / 7.0) for d, _r in use]
    return sum(wi * r for wi, (_d, r) in zip(w, use)) / sum(w), pool


def lastForm(rows, day):
    """The newest rated race before `day`. (rating, pool) or None."""
    before = [(d, r, p) for d, r, p in rows if d < day]
    if not before:
        return None
    d, r, p = max(before)
    return r, p


def _poolOn(rows, day):
    on = [p for d, _r, p in rows if d == day]
    if on:
        return on[0]
    return max(rows)[2] if rows else None


def raceScores(got):
    """got: [(actual seconds, {basis: predicted seconds})] for one race, every
    runner priced by every basis. {basis: (pairs, rho, spread %)}. Pure."""
    from backtest_predictions import _spearman
    act = [a for a, _p in got]
    out = {}
    for b in got[0][1]:
        pred = [p[b] for _a, p in got]
        e = [math.log(x / a) for x, a in zip(pred, act)]
        m = statistics.median(e)
        out[b] = (pairShare(pred, act), _spearman(pred, act),
                  100.0 * statistics.median(abs(x - m) for x in e))
    return out


def runnerMisses(got):
    """[{basis: within-race miss %}] per runner, in `got`'s order: ln(pred /
    actual) less the race's own median, so only the runner-vs-field part is
    left. Pure."""
    act = [a for a, _p in got]
    cols = {}
    for b in got[0][1]:
        e = [math.log(p[b] / a) for (_a, p), a in zip(got, act)]
        m = statistics.median(e)
        cols[b] = [100.0 * (x - m) for x in e]
    return [{b: cols[b][i] for b in cols} for i in range(len(got))]


# ★ WHERE THE ROOM IS, PER RUNNER (owner's E0, 2026-10-24: the month split
#   put most of the gap in August and September). A runner's first cross
#   country race of the season is predicted from last season or a spring of
#   track; a mid-season race from last week. The bands split those.
BANDS = ("in season, <=21d", "in season, >21d", "season opener, <=120d",
         "season opener, 121-365d", "season opener, >1yr")


def runnerBand(all_dates, xc_season_dates, day):
    """all_dates: the runner's rated race dates before `day`, any sport;
    xc_season_dates: their cross country dates this season. Pure."""
    before = [d for d in all_dates if d < day]
    if not before:
        return None
    gap = (datetime.date.fromisoformat(day) - datetime.date.fromisoformat(max(before))).days
    if any(d < day for d in xc_season_dates):
        return BANDS[0] if gap <= 21 else BANDS[1]
    if gap <= 120:
        return BANDS[2]
    return BANDS[3] if gap <= 365 else BANDS[4]


def paired(xs, ys, draws=2000, seed=0):
    """Mean of xs - ys over races, with a 90% interval by resampling races."""
    d = [x - y for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(d) < 5:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(d) for _k in d) for _i in range(draws))
    return statistics.fmean(d), means[int(0.05 * draws)], means[int(0.95 * draws) - 1]


def summarize(races):
    """races: [{"scores": raceScores(...), "level", "month", "n"}]. Lines."""
    lines = []
    if not races:
        return ["nothing scorable"]
    lines.append(f"{'basis':8} {'pairs':>7} {'rho':>6} {'spread':>7}")
    mean = {}
    bases = list(races[0]["scores"])
    for b in bases:
        ps = [r["scores"][b][0] for r in races if r["scores"][b][0] is not None]
        rs = [r["scores"][b][1] for r in races if r["scores"][b][1] is not None]
        ss = [r["scores"][b][2] for r in races]
        mean[b] = statistics.fmean(ss)
        lines.append(f"{b:8} {100 * statistics.fmean(ps):>6.1f}% {statistics.fmean(rs):>6.3f} "
                     f"{statistics.median(ss):>6.2f}%")
    lines.append("\npaired by race, pairs in order (points; 90% interval)")
    for x, y in (("prior", "last"), ("season", "prior"), ("nearest", "prior"),
                 ("model", "prior"), ("nearest", "model")):
        if x not in bases or y not in bases:
            continue
        got = paired([100 * r["scores"][x][0] for r in races if r["scores"][x][0] is not None],
                     [100 * r["scores"][y][0] for r in races if r["scores"][y][0] is not None])
        if got:
            lines.append(f"  {x:>7} - {y:<6} {got[0]:+.2f} [{got[1]:+.2f}, {got[2]:+.2f}]")
    p2 = statistics.fmean(r["scores"]["prior"][2] ** 2 for r in races)
    n2 = statistics.fmean(r["scores"]["nearest"][2] ** 2 for r in races)
    lines.append(f"\nwithin-race spread (rms of race medians): prior {math.sqrt(p2):.2f}%, "
                 f"nearest {math.sqrt(n2):.2f}% -> form error the ratings carry "
                 f"<= ~{math.sqrt(max(p2 - n2, 0.0)):.2f}% in the same median-miss units "
                 "(the rest is the day)")
    rows = [(band, m) for r in races for band, m in r.get("runners") or [] if band]
    if rows:
        lines.append("\nby runner: median |within-race miss| %, prior -> nearest "
                     "(last beside it)")
        for band in BANDS:
            ms = [m for b, m in rows if b == band]
            if len(ms) < 50:
                continue
            med = {b: statistics.median(abs(m[b]) for m in ms) for b in bases}
            extra = f", model {med['model']:.2f}%" if "model" in med else ""
            lines.append(f"  {band:26} {len(ms):>6}  {med['prior']:.2f}% -> "
                         f"{med['nearest']:.2f}%   (last {med['last']:.2f}%{extra})")
    for title, key in (("level", "level"), ("month", "month")):
        groups = {}
        for r in races:
            groups.setdefault(r[key], []).append(r)
        lines.append(f"\nby {title}: races, pairs prior -> nearest"
                     + (" (model)" if "model" in bases else ""))
        for g, rs in sorted(groups.items()):
            if len(rs) < 5:
                continue
            a = statistics.fmean(r["scores"]["prior"][0] for r in rs
                                 if r["scores"]["prior"][0] is not None)
            c = statistics.fmean(r["scores"]["nearest"][0] for r in rs
                                 if r["scores"]["nearest"][0] is not None)
            mo = ""
            if "model" in bases:
                mo = (f"  ({100 * statistics.fmean(r['scores']['model'][0] for r in rs if r['scores']['model'][0] is not None):5.1f}%)")
            lines.append(f"  {str(g):12} {len(rs):>4}  {100 * a:5.1f}% -> {100 * c:5.1f}%{mo}")
    return lines


# ------------------------------------------------------------------ #
#  the database side
# ------------------------------------------------------------------ #

def _seasonBounds(day):
    """The academic season holding `day` (from 1 August, season_year's clock)."""
    from season_year import ACADEMIC_START_MONTH
    y = day.year if day.month >= ACADEMIC_START_MONTH else day.year - 1
    return (datetime.date(y, ACADEMIC_START_MONTH, 1),
            datetime.date(y + 1, ACADEMIC_START_MONTH, 1))


def scoreRace(cur, P, race, with_model=False, val_only=False, min_matched=MIN_MATCHED):
    from backtest_baseline import finishers
    day = datetime.date.fromisoformat(race["day"])
    fin = finishers(cur, race)
    ids = [f[0] for f in fin]
    target = {"mode": "rerun_exact", "sport": "XC", "meet_id": race["meet_id"],
              "div_id": race["div_id"], "source": race["source"]}
    spec = P._targetSpec(cur, target)
    prior = P._ratingTimes(cur, ids, spec, day)
    lo, hi = _seasonBounds(day)
    rows = P._ratingRows(cur, ids, lo.isoformat(), hi.isoformat())
    every = P._ratingRows(cur, ids, None, day.isoformat())     # for "last"
    # the network as the page runs it, the race's own result excluded by
    # the cut (history_before = the day)
    model = (dict(zip(ids, P._predictTimes(cur, ids, dict(target, history_before=race["day"]),
                                           spec=spec)))
             if with_model else {})
    cur.connection.rollback()

    def secs(form):
        if not form:
            return None
        return P._ratingToSeconds(form[0], form[1], spec, day.year)

    got, levels, bands = [], [], []
    for pid, actual, _school in fin:
        mine = [(str(r[0])[:10], r[1], r[2]) for r in rows.get(pid) or [] if r[3] == "XC"]
        past = [(str(r[0])[:10], r[1], r[2]) for r in every.get(pid) or [] if r[3] == "XC"]
        pred = {"last": secs(lastForm(past, race["day"])),
                "prior": (prior.get(pid) or {}).get("seconds"),
                "season": secs(seasonForm(mine, race["day"])),
                "nearest": secs(nearestForm(mine, race["day"]))}
        if with_model:
            m = model.get(pid) or {}
            pred["model"] = m.get("seconds") if m.get("is_race_time") else None
        if val_only:
            from feature_extraction import isValPerson
            if not isValPerson(pid):
                continue
        if all(pred.values()):
            got.append((actual, pred))
            levels.append((prior[pid].get("form_pool") or "?").split("|")[0])
            bands.append(runnerBand([str(r[0])[:10] for r in every.get(pid) or []],
                                    [d for d, _r, _p in mine], race["day"]))
    if len(got) < min_matched:
        return None
    return {"scores": raceScores(got), "n": len(got),
            "runners": list(zip(bands, runnerMisses(got))),
            "level": statistics.mode(levels), "month": race["day"][:7]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--divs", type=int, default=300)
    ap.add_argument("--min-field", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--source", choices=("anet", "tfrrs"), default=None,
                    help="only this feed's races (tfrrs = college, Kailo's world)")
    ap.add_argument("--with-model", action="store_true",
                    help="add the network (model.pt) as a fifth basis, a few s a race")
    ap.add_argument("--val-only", action="store_true",
                    help="only runners on the network's validation side (the 10%% "
                         "of athletes train.py never trained on); pair it with "
                         "--min-matched 4, as a race keeps about a tenth of its field")
    ap.add_argument("--min-matched", type=int, default=MIN_MATCHED)
    a = ap.parse_args()

    import psycopg2.extras
    import predict as P
    from database import getConn
    from backtest_predictions import sampleRaces

    out = []
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        races = sampleRaces(cur, a.lo, a.hi, "XC", a.min_field)
        if a.source:
            races = [r for r in races if r["source"] == a.source]
        conn.rollback()
        random.Random(a.seed).shuffle(races)
        races = races[:a.divs]
        print(f"{len(races)} races {a.lo}..{a.hi} (fields >= {a.min_field})", flush=True)
        for n, race in enumerate(races, 1):
            try:
                got = scoreRace(cur, P, race, a.with_model, a.val_only, a.min_matched)
            except Exception as exc:                 # noqa: BLE001
                conn.rollback()
                print(f"  {race['meet_id']}/{race['div_id']}: {exc}", flush=True)
                continue
            if got:
                out.append(got)
            if n % 25 == 0 or n == len(races):
                print(f"  [{n}/{len(races)}] {len(out)} scored", flush=True)
    print(f"\n{len(out)} races, {sum(r['n'] for r in out):,} runners priced by all four "
          "(hindsight = a CEILING: it uses races after the day)\n")
    print("\n".join(summarize(out)))


if __name__ == "__main__":
    main()
