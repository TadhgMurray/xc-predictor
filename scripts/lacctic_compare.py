#!/usr/bin/env python3
"""
lacctic_compare.py -- our college numbers against LACCTiC's, on the same
races and the same runners. READ-ONLY (our database; LACCTiC's public API).

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/lacctic_compare.py --race 12194
    /srv/venv/bin/python scripts/lacctic_compare.py --race 12194,12158 --runners 60
    /srv/venv/bin/python scripts/lacctic_compare.py --runner 144903

★ WHY (owner, 2026-10-04: "lacctic just seems more authoritative, even if
  it also feels off"). LACCTiC is college only and states every result as a
  TRACK 5K EQUIVALENT (modern_tic = ln seconds; a track 5000 is its own
  time). Ours become the same thing through conversions.fiveKForRating (a
  rating's 5K on a typical outdoor track), so the two can be laid side by
  side, result by result:

    LEVEL      median (ours - theirs), % of time: + = we call the result
               SLOWER than they do. A level offset alone is a choice of
               scale, not an error -- it moves everyone alike.
    AGREEMENT  the spread of that difference within a race: low = the two
               systems agree on who ran well that day.
    CONSISTENCY  per runner, how far each system's per-race numbers stray
               from the runner's own season median, over the races BOTH
               have. Course and day corrections that are right make a
               runner's races agree with each other; the system with the
               tighter spread is correcting better. This is the fair test
               without either system's ratings from before the race.
    CROSS-SPORT  per runner-season, the median track number minus the
               median cross country number, in each system: what each says
               about XC against track for the same people and races.

  Matching: a LACCTiC race carries its TFRRS url, whose meet id is our
  tfrrs meet_id; a runner within it is matched by name and time (+-1.0 s).
  A runner's other races are matched by date and time.

  Polite: one request per race and per runner, --sleep apart.
"""
import argparse
import math
import os
import re
import statistics
import sys
import time

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from database import getConn                                   # noqa: E402

API = "https://api.lacctic.com/api_ranking"
_S = requests.Session()
_S.headers.update({"User-Agent": "racecast-compare/1.0 (read-only research)",
                   "Accept": "application/json"})


def fetch(path, sleep):
    for attempt in range(3):
        r = _S.get(f"{API}/{path}", timeout=60)
        if r.status_code == 200 and r.text[:1] == "{":
            time.sleep(sleep)
            return r.json()
        time.sleep(max(sleep, 3) * (attempt + 1))
    raise SystemExit(f"[lacctic] {path}: HTTP {r.status_code}")


def norm_name(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


def mmss(sec):
    return f"{int(sec // 60)}:{sec % 60:04.1f}" if sec else "-"


def pct(x):
    return f"{100 * x:+.2f}%"


def robust_sd(v):
    if len(v) < 3:
        return float("nan")
    m = statistics.median(v)
    return 1.4826 * statistics.median([abs(x - m) for x in v])


class Ours:
    """Our rows and their track-5K equivalents."""

    def __init__(self, cur):
        self.cur = cur
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name = 'results' AND column_name = 'rating_pool'""")
        self.pool_col = "rating_pool" if cur.fetchone() else "NULL::text"
        import conversions as C
        self.C = C
        self._five = {}

    def five(self, rating, pool):
        """ln seconds of the rating's 5K on a typical outdoor track."""
        if rating is None or not pool:
            return None
        key = (round(float(rating), 2), pool)
        if key not in self._five:
            f = self.C.fiveKForRating(float(rating), pool)
            t = f.get("track") if f else None
            self._five[key] = math.log(t) if t else None
        return self._five[key]

    def race(self, meet_id):
        self.cur.execute(f"""
            SELECT result_id, person_id, athlete_name, time_seconds, speed_rating,
                   {self.pool_col}, date
            FROM results WHERE meet_id = %s AND source = 'tfrrs'""", (meet_id,))
        return self.cur.fetchall()

    def person_rows(self, pid):
        out = []
        for table, sport, ev in (("results", "XC", "NULL::text"),
                                 ("results_tf", "TF", "event_short")):
            self.cur.execute(f"""
                SELECT date, time_seconds, speed_rating, {self.pool_col}, {ev}
                FROM {table} WHERE person_id = %s AND speed_rating IS NOT NULL""", (pid,))
            out += [(sport,) + tuple(r) for r in self.cur.fetchall()]
        return out


def compare_race(lac_id, ours, sleep, default_pool):
    d = fetch(f"race_page/{lac_id}/", sleep)
    m = re.search(r"/results/xc/(\d+)/", d.get("tfrrs_url") or "")
    if not m:
        print(f"[lacctic] race {lac_id}: no TFRRS url; skipped")
        return []
    meet = int(m.group(1))
    pool = default_pool or ("college_f" if d.get("sex") == "F" else "college_m")
    rows = ours.race(meet)
    by_name = {}
    for r in rows:
        by_name.setdefault(norm_name(r[2]), []).append(r)
    matched = []
    for x in d.get("xc_results") or []:
        rn = x.get("runner") or {}
        cand = by_name.get(norm_name(f"{rn.get('firstname', '')} {rn.get('lastname', '')}"), [])
        hit = [r for r in cand if r[3] and abs(float(r[3]) - float(x["time"])) <= 1.0]
        if len(hit) != 1 or hit[0][4] is None:
            continue
        r = hit[0]
        p = (r[5] or "").split("|", 1)[0] or pool
        f = ours.five(r[4], p)
        if f is None or x.get("modern_tic") is None:
            continue
        matched.append({"lac_runner": rn.get("id"), "person": r[1], "time": float(x["time"]),
                        "place": x.get("place"), "name": f"{rn.get('firstname')} {rn.get('lastname')}",
                        "ours": f, "theirs": float(x["modern_tic"]),
                        "ability": rn.get("ability"), "rating": r[4], "pool": p})
    diff = [m_["ours"] - m_["theirs"] for m_ in matched]
    print(f"\n== {d.get('meet_name')} ({d.get('date')}) | {str(d.get('section'))[:48]}")
    print(f"   LACCTiC course {pct(d.get('course_difficulty') or 0)}; TFRRS meet {meet}; "
          f"{len(matched)} of {len(d.get('xc_results') or [])} runners matched to ours "
          f"({len(rows)} of our rows)")
    if len(diff) >= 5:
        print(f"   LEVEL      ours - theirs: median {pct(statistics.median(diff))} "
              f"(+ = we call it slower)")
        print(f"   AGREEMENT  spread of the difference within the race: "
              f"{100 * robust_sd(diff):.2f}% (robust sd)")
        # by their ability quartile: does the gap move with ability?
        ab = sorted(m_["ability"] for m_ in matched if m_["ability"] is not None)
        if len(ab) >= 20:
            qs = [ab[len(ab) * k // 4] for k in (1, 2, 3)]
            line = []
            for k in range(4):
                lo = -1e9 if k == 0 else qs[k - 1]
                hi = 1e9 if k == 3 else qs[k]
                v = [m_["ours"] - m_["theirs"] for m_ in matched
                     if m_["ability"] is not None and lo <= m_["ability"] < hi]
                if v:
                    line.append(f"Q{k + 1} {pct(statistics.median(v))}")
            print("   by their ability, fastest first: " + "  ".join(line))
        for m_ in sorted(matched, key=lambda z: z["place"] or 999)[:5]:
            print(f"     {str(m_['place']):>3} {m_['name']:<24} {mmss(m_['time'])}  "
                  f"ours {mmss(math.exp(m_['ours']))}  theirs {mmss(math.exp(m_['theirs']))}")
    return matched


def runner_check(lac_id, person, ours, sleep):
    """Per-race 5K equivalents of one runner in both systems, matched by date
    and time; returns {season: {'XC': [(o, t)], 'TF': [(o, t)]}}."""
    d = fetch(f"runner_page/{lac_id}/", sleep)
    rows = ours.person_rows(person)
    out = {}
    for sr in d.get("season_ratings") or []:
        season = (sr.get("season") or {}).get("year")
        for sport, key in (("XC", "season_xc_performances"), ("TF", "season_track_performances")):
            for perf in sr.get(key) or []:
                date = perf.get("date") or (perf.get("race") or {}).get("date")
                t = perf.get("time")
                tic = perf.get("modern_tic")
                if not date or not t or tic is None:
                    continue
                hit = [r for r in rows if r[0] == sport and str(r[1])[:10] == str(date)[:10]
                       and r[2] and abs(float(r[2]) - float(t)) <= 1.0]
                if len(hit) != 1:
                    continue
                pool = (hit[0][4] or "").split("|", 1)[0]
                f = ours.five(hit[0][3], pool)
                if f is None:
                    continue
                out.setdefault(season, {"XC": [], "TF": []})[sport].append((f, float(tic)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--race", default="", help="LACCTiC race ids, comma separated")
    ap.add_argument("--runner", default="", help="LACCTiC runner ids: compare their races")
    ap.add_argument("--runners", type=int, default=40,
                    help="runners sampled from the races for the consistency and "
                         "cross-sport checks")
    ap.add_argument("--pool", default=None, help="our pool if the rows carry none")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between requests")
    a = ap.parse_args()
    races = [int(x) for x in a.race.split(",") if x.strip()]
    lac_runners = [int(x) for x in a.runner.split(",") if x.strip()]
    if not races and not lac_runners:
        ap.error("--race or --runner")
    with getConn() as conn:
        cur = conn.cursor()
        ours = Ours(cur)
        matched = []
        for rid in races:
            matched += compare_race(rid, ours, a.sleep, a.pool)
        # the runners for the per-runner checks: an even spread over the order
        pairs = {}
        if matched:
            ordered = sorted(matched, key=lambda m_: m_["ability"] or 99)
            step = max(1, len(ordered) // max(a.runners, 1))
            for m_ in ordered[::step][:a.runners]:
                if m_["person"]:
                    pairs[m_["lac_runner"]] = m_["person"]
        for lr in lac_runners:
            d = fetch(f"runner_page/{lr}/", a.sleep)
            cur.execute("SELECT DISTINCT person_id FROM results WHERE native_id = %s "
                        "AND source = 'tfrrs' AND person_id IS NOT NULL LIMIT 2",
                        (int(d.get("tfrrs_id") or 0),))
            got = cur.fetchall()
            if len(got) == 1:
                pairs[lr] = got[0][0]
                print(f"[lacctic] runner {lr} ({d.get('firstname')} {d.get('lastname')}) "
                      f"= our person {got[0][0]}")
            else:
                print(f"[lacctic] runner {lr}: tfrrs {d.get('tfrrs_id')} -> "
                      f"{len(got)} of our persons; skipped")
        if not pairs:
            conn.rollback()
            return
        print(f"\n== per runner, over the races BOTH systems have ({len(pairs)} runners)")
        dev_o, dev_t, cross_o, cross_t = [], [], [], []
        for lr, pid in pairs.items():
            by_season = runner_check(lr, pid, ours, a.sleep)
            for season, sp in by_season.items():
                xc = sp["XC"]
                if len(xc) >= 3:
                    mo = statistics.median(o for o, _ in xc)
                    mt = statistics.median(t for _, t in xc)
                    dev_o += [o - mo for o, _ in xc]
                    dev_t += [t - mt for _, t in xc]
                if sp["XC"] and sp["TF"]:
                    cross_o.append(statistics.median(o for o, _ in sp["TF"])
                                   - statistics.median(o for o, _ in sp["XC"]))
                    cross_t.append(statistics.median(t for _, t in sp["TF"])
                                   - statistics.median(t for _, t in sp["XC"]))
            if lr in lac_runners:
                for season, sp in sorted(by_season.items()):
                    for sport in ("XC", "TF"):
                        for o, t in sp[sport]:
                            print(f"     {season} {sport}  ours {mmss(math.exp(o))}  "
                                  f"theirs {mmss(math.exp(t))}  ({pct(o - t)})")
        if dev_o:
            print(f"   CONSISTENCY  a runner's XC races about their own season median, "
                  f"robust sd over {len(dev_o)} races:")
            print(f"     ours {100 * robust_sd(dev_o):.2f}%   theirs {100 * robust_sd(dev_t):.2f}%"
                  f"   (lower = the course and day corrections agree with the runner)")
        if cross_o:
            print(f"   CROSS-SPORT  track minus XC, median over {len(cross_o)} runner-seasons "
                  f"(- = track reads FASTER than their XC):")
            print(f"     ours {pct(statistics.median(cross_o))}   theirs "
                  f"{pct(statistics.median(cross_t))}")
        conn.rollback()
    print("\n[lacctic] read-only; nothing written")


if __name__ == "__main__":
    main()
