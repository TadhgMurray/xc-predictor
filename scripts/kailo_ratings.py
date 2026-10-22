#!/usr/bin/env python3
"""
kailo_ratings.py -- our ratings against Kailo's (KPACE fitness), on the
same college cross country runners in the same races. READ-ONLY on our
database; Kailo's public API. kailo_compare.py does the PREDICTIONS (race
times with the course and the day in them); this does the RATINGS alone.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/kailo_ratings.py --preseason   # both systems' pre-season
                                                                # ratings, every race so far
    /srv/venv/bin/python scripts/kailo_ratings.py --snapshot    # save both systems' ratings NOW
    /srv/venv/bin/python scripts/kailo_ratings.py               # score every saved snapshot on
                                                                # the races run after it
    /srv/venv/bin/python scripts/kailo_ratings.py --now         # today's ratings on races they
                                                                # have both seen (fit, not foresight)

★ WHY (owner, 2026-10-22: "can we compare our ratings to their ratings, and
  our predictions to their predictions separately"). The prediction test
  mixes three things: the rating, the course and distance conversion, and
  the day. On Cowboy Jamboree our model beat Kailo where the runner had run
  the distance (1.98% vs 3.44%) and lost by 8% where the time went through
  our conversion -- which says nothing about whose RATING is better.

★ BOTH ON ONE SCALE, NO CONVERSION NEEDED. Kailo's rating is fitness_5k_s,
  "seconds of a flat 5K-equivalent on a typical rated course and a typical
  day"; ours is the form rating the race pages use (predict._formRating:
  the last six rated races of the year before the cut, recency-weighted),
  put on the same footing by predict._ratingToSeconds as a 5K at no
  particular venue (each pool through its own curve, as the pages do). Within one race the course, the distance
  and the day are the same for everyone, so a rating is judged only on:
    order    of every pair of finishers, how often the faster rating
             finished ahead
    spread   |ln(actual) - ln(rating) - that race's mean of it|, in %: how
             far each runner finished from where the rating put them
             against the rest of the field. 0 = the rating knew the field
             exactly; the race's own level (course, distance, weather) is
             taken off, so no conversion of either system is in it.
  On the runners BOTH systems rate, only.

★ THREE TESTS, AND ONLY ONE IS FREE OF HINDSIGHT.
    --preseason  Kailo's pre_season_fitness_5k_s against ours as of
                 --season-start, on every race of the season so far. Ready
                 today. ! Both sides' numbers come from models fitted after
                 the fact (our solve has seen this season), so mildly
                 flattering to both, not to one.
    --snapshot   then (default) both systems' ratings saved on the same day,
                 scored on races AFTER it: the clean test. Run --snapshot
                 now and every few days (deploy/install_kailo_record_timer.sh).
    --now        today's ratings on races both have already seen: how well
                 each rating explains the season, not how well it foresaw it.

Matching: Kailo's athlete_id against our tfrrs rows' native_id (with the
name agreeing), otherwise the name (words sorted) and the school, among
this season's tfrrs cross country runners.
"""
import argparse
import datetime
import glob
import json
import math
import os
import random
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import kailo_compare as K                                       # noqa: E402

LISTS = ("all_M", "all_F")
SNAP_GLOB = os.path.join(K.STORE, "ratings_*.json")


# ------------------------------------------------------------------ #
#  pure parts
# ------------------------------------------------------------------ #

def nativeKey(native_id):
    """'906936' / '906936-rocky-hansen' -> '906936'; None without digits."""
    m = re.match(r"\s*(\d+)", str(native_id or ""))
    return m.group(1) if m else None


def matchAthletes(kailo, ours):
    """{kailo athlete_id: person_id}. kailo: [athlete dicts]; ours:
    [(person_id, name, school, native_id)]. By native id when the names
    agree, else by name key and school. A person claimed by two Kailo
    athletes is dropped from both. Pure."""
    by_native, by_name = {}, {}
    for pid, name, school, native in ours:
        key = K.nameKey(name)
        nk = nativeKey(native)
        if nk:
            by_native[nk] = (pid, key)
        by_name.setdefault(key, []).append((pid, school or ""))
    got, claims = {}, {}
    for a in kailo:
        key = K.nameKey(a.get("name"))
        hit = by_native.get(str(a.get("athlete_id")))
        pid = hit[0] if hit and hit[1] == key else None
        if pid is None:
            cands = by_name.get(key) or []
            if len(cands) == 1 and (not cands[0][1] or not a.get("school")
                                    or K.similarity(a.get("school"), cands[0][1]) >= 0.2):
                pid = cands[0][0]
            elif len(cands) > 1:
                best = max(cands, key=lambda c: K.similarity(a.get("school"), c[1]))
                if K.similarity(a.get("school"), best[1]) >= 0.3:
                    pid = best[0]
        if pid is not None:
            got[a["athlete_id"]] = pid
            claims[pid] = claims.get(pid, 0) + 1
    return {k: p for k, p in got.items() if claims[p] == 1}


def raceScores(rows):
    """rows: [(actual_s, kailo_5k, ours_5k)] of one race. (order_k, order_o,
    pairs, resid_k, resid_o): pairs each rating put in the right order, and
    each runner's level-free log miss in % (signed; + = slower than the
    rating said). Pure."""
    rows = [r for r in rows if r[0] > 0 and r[1] > 0 and r[2] > 0]
    gk = go = n = 0
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            ai, aj = rows[i][0], rows[j][0]
            if ai == aj:
                continue
            first = ai < aj
            n += 1
            gk += (rows[i][1] < rows[j][1]) == first
            go += (rows[i][2] < rows[j][2]) == first

    def resid(col):
        xs = [math.log(r[0] / r[col]) for r in rows]
        m = sum(xs) / len(xs) if xs else 0.0
        return [100.0 * (x - m) for x in xs]
    return gk, go, n, resid(1), resid(2)


def bootstrapOrder(per_race, reps=1000, seed=1):
    """95% interval of (ours - Kailo) order share, resampling races.
    per_race: [(order_k, order_o, pairs)]. Pure."""
    if len(per_race) < 3:
        return None
    rnd = random.Random(seed)
    diffs = []
    for _ in range(reps):
        s = [per_race[rnd.randrange(len(per_race))] for _ in per_race]
        n = sum(x[2] for x in s)
        if n:
            diffs.append(100.0 * (sum(x[1] for x in s) - sum(x[0] for x in s)) / n)
    diffs.sort()
    return diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]


def raceSex(pids, sex_of):
    """The sex most of a race's matched runners are. Pure."""
    c = {}
    for p in pids:
        s = sex_of.get(p)
        if s:
            c[s] = c.get(s, 0) + 1
    return max(c, key=c.get) if c else None


# ------------------------------------------------------------------ #
#  the two sides
# ------------------------------------------------------------------ #

def kailoAthletes(sleep):
    out, meta = [], {}
    for key in LISTS:
        d = K.fetch(f"/athletes/{key}", sleep)
        meta = {"version": d.get("version")}
        out += d.get("athletes") or []
    top = K.fetch("/athletes", sleep)
    meta.update({"generated": top.get("generated"),
                 "results_through": top.get("results_through"), "season": top.get("season")})
    return out, meta


def ourRunners(cur, since):
    """[(person_id, name, school, native_id)] of this season's tfrrs XC rows."""
    cur.execute("""
        SELECT person_id, max(athlete_name), max(school), max(native_id)
        FROM   results
        WHERE  source = 'tfrrs' AND person_id IS NOT NULL AND date >= %s
        GROUP  BY person_id""", (since,))
    return cur.fetchall()


def ourRatings(cur, pids, cut):
    """{pid: (flat-5K seconds, rating, pool, n)} -- our form rating as of
    `cut` (strictly before), as the rating basis builds it, at difficulty 0."""
    import predict as P
    from psycopg2.extras import RealDictCursor
    dcur = cur.connection.cursor(cursor_factory=RealDictCursor)
    hi = datetime.date.fromisoformat(cut)
    # ★ THROUGH THE PAGES' OWN CLOCK, NOT _norm_from_rating ALONE (2026-10-22,
    #   first run: freshmen on a high-school form rating read +24% "slow").
    #   A normalized time is on its RATED pool's scale (anchor_repair, 05b),
    #   so two pools' norms are not one clock; _ratingToSeconds puts each
    #   through its own pool's curve to real seconds -- here a 5K at no
    #   particular venue, the shape of Kailo's flat-5K fitness.
    spec = {"sport": "XC", "is_xc": True, "distance_meters": 5000.0}
    lo = (hi - datetime.timedelta(days=P._RATING_WINDOW_DAYS)).isoformat()
    out = {}
    pids = sorted(set(pids))
    for i in range(0, len(pids), 5000):
        rows = P._ratingRows(dcur, pids[i:i + 5000], None, cut)
        for pid, mine in rows.items():
            recent = [r for r in mine if r[0] >= lo]
            form = P._formRating(recent or mine, "XC")
            if not form:
                continue
            rating, _sig, pool, n = form
            secs = P._ratingToSeconds(rating, pool, spec, hi.year)
            if secs:
                out[pid] = (float(secs), round(float(rating), 1), pool, n)
    cur.connection.rollback()
    return out


def races(cur, lo, hi, min_field):
    """[(meet, div, day, {pid: seconds})] of tfrrs XC races in [lo, hi)."""
    cur.execute("""
        SELECT meet_id, div_id, min(date)::text, array_agg(person_id), array_agg(time_seconds)
        FROM   results
        WHERE  source = 'tfrrs' AND person_id IS NOT NULL
          AND  date >= %s AND date < %s
          AND  time_seconds BETWEEN 600 AND 4500
        GROUP  BY meet_id, div_id
        HAVING count(*) >= %s""", (lo, hi, min_field))
    out = []
    for meet, div, day, pids, secs in cur.fetchall():
        best = {}
        for p, s in zip(pids, secs):
            best[p] = min(float(s), best.get(p, 1e9))
        out.append((meet, div, (day or "")[:10], best))
    return out


# ------------------------------------------------------------------ #
#  scoring
# ------------------------------------------------------------------ #

def score(title, race_list, kailo_by_pid, ours_by_pid, info, min_both, keep=None):
    """kailo_by_pid: {pid: 5k s}; ours_by_pid: {pid: (5k s, ...)}; info:
    {pid: kailo athlete}; keep(pid) -> bool narrows the runners."""
    if keep is not None:
        kailo_by_pid = {p: v for p, v in kailo_by_pid.items() if keep(p)}
    sex_of = {p: a.get("sex") for p, a in info.items()}
    per_race, recs = [], []
    n_run = n_k = n_o = 0
    for meet, div, day, field in race_list:
        sx = raceSex(field, sex_of)
        n_run += len(field)
        n_k += sum(1 for p in field if p in kailo_by_pid)
        n_o += sum(1 for p in field if p in ours_by_pid)
        both = [(sec, kailo_by_pid[p], ours_by_pid[p][0], p) for p, sec in field.items()
                if p in kailo_by_pid and p in ours_by_pid
                and (sx is None or sex_of.get(p) in (None, sx))]
        if len(both) < min_both:
            continue
        gk, go, n, rk, ro = raceScores([b[:3] for b in both])
        per_race.append((gk, go, n, meet, div, day, len(both)))
        for (_s, _k, _o, p), a, b in zip(both, rk, ro):
            ath = info.get(p) or {}
            pool = (ours_by_pid[p][2] or "?").split("_", 1)[0]
            cs = ath.get("career_season")
            recs.append(([("division", ath.get("division") or "?"),
                          ("career season", "1st" if cs == 1 else "2nd+" if cs else "?"),
                          ("Kailo basis", ath.get("basis") or "?"),
                          ("our form pool", pool)], a, b))
    print(f"\n== {title}")
    print(f"  {len(race_list):,} tfrrs XC races; {n_run:,} finishers, Kailo rates {n_k:,}, "
          f"we rate {n_o:,}")
    if not per_race:
        print("  nothing both systems rate yet")
        return
    gk = sum(x[0] for x in per_race)
    go = sum(x[1] for x in per_race)
    n = sum(x[2] for x in per_race)
    sk = sum(abs(a) for _g, a, _b in recs) / len(recs)
    so = sum(abs(b) for _g, _a, b in recs) / len(recs)
    ci = bootstrapOrder(per_race)
    print(f"  {len(per_race):,} races, {len(recs):,} runners rated by both, {n:,} pairs")
    print(f"    order    Kailo {100 * gk / n:5.1f}%   ours {100 * go / n:5.1f}%"
          + (f"   (ours - Kailo, 95% over races: {ci[0]:+.2f} to {ci[1]:+.2f})" if ci else ""))
    print(f"    spread   Kailo {sk:5.2f}%   ours {so:5.2f}%   (the miss with each race's "
          "own level taken off; lower is better)")
    bd = K.breakdown(recs)
    print(f"\n  {'':<16}{'group':<12}{'runners':>8}{'spread Kailo':>14}{'ours':>8}"
          f"{'bias Kailo':>12}{'ours':>8}")
    for (tab, grp), (cnt, ak, ao, bk, bo) in sorted(bd.items()):
        if cnt >= 20:
            print(f"  {tab:<16}{str(grp):<12}{cnt:>8}{ak:>13.2f}%{ao:>7.2f}%{bk:>+11.2f}%"
                  f"{bo:>+7.2f}%")
    print("  (bias + = ran slower than the rating said, against the rest of the field)")


def _isCollege(ours, pid):
    v = ours.get(pid)
    return bool(v) and (v[2] or "").startswith(("college", "pro"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preseason", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--season-start", default=None,
                    help="default: August 1 of Kailo's season")
    ap.add_argument("--min-field", type=int, default=15, help="finishers in a race")
    ap.add_argument("--min-both", type=int, default=10, help="runners both rate, per race")
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args()
    from database import getConn

    kailo, meta = kailoAthletes(a.sleep)
    season = int(meta.get("season") or datetime.date.today().year)
    start = a.season_start or f"{season}-08-01"
    today = datetime.date.today().isoformat()
    print(f"[kailo] {len(kailo):,} rated college runners, generated {meta.get('generated')}, "
          f"results through {meta.get('results_through')} (version {meta.get('version')})")
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '15min'")
        mine = ourRunners(cur, start)
        match = matchAthletes(kailo, mine)
        info = {match[x["athlete_id"]]: x for x in kailo if x["athlete_id"] in match}
        print(f"[kailo] {len(match):,} of them matched to our people "
              f"({len(mine):,} tfrrs XC runners since {start})")

        if a.snapshot:
            asof = (meta.get("results_through") or today)
            cut = (datetime.date.fromisoformat(asof) + datetime.timedelta(days=1)).isoformat()
            ours = ourRatings(cur, list(info), cut)
            os.makedirs(K.STORE, exist_ok=True)
            path = os.path.join(K.STORE, f"ratings_{asof}.json")
            json.dump({"meta": meta, "cut": cut, "saved_at": datetime.datetime.utcnow()
                       .isoformat() + "Z",
                       "kailo": {str(p): {k: x.get(k) for k in (
                           "athlete_id", "name", "school", "sex", "division", "career_season",
                           "basis", "races", "fitness_5k_s", "pre_season_fitness_5k_s",
                           "projection_fitness_5k_s")} for p, x in info.items()},
                       "ours": {str(p): v for p, v in ours.items()}}, open(path, "w"))
            print(f"[kailo] saved {path}: {len(info):,} Kailo ratings, {len(ours):,} ours "
                  f"(as of {cut}, strictly before)")
            return

        if a.preseason or a.now:
            rl = races(cur, start, today, a.min_field)
            pids = {p for _m, _d, _day, f in rl for p in f} & set(info)
            if a.preseason:
                ours = ourRatings(cur, pids, start)
                # ! NOT KAILO'S "xc2026" RUNNERS (2026-10-22, first run: 1.75%
                #   spread, better than their in-season 1.88%). A runner whose
                #   whole record is this season has no pre-season; the number
                #   in that field is built from the races it is scored on.
                kk = {p: info[p]["pre_season_fitness_5k_s"] for p in pids
                      if info[p].get("pre_season_fitness_5k_s")
                      and info[p].get("basis") != "xc2026"}
                t = f"PRE-SEASON ratings (ours as of {start}) on races {start} .. {today}"
                score(t, rl, kk, ours, info, a.min_both)
                score(t + " -- runners on a COLLEGE form rating of ours", rl, kk, ours,
                      info, a.min_both, keep=lambda p: _isCollege(ours, p))
            if a.now:
                ours = ourRatings(cur, pids, today)
                kk = {p: info[p]["fitness_5k_s"] for p in pids if info[p].get("fitness_5k_s")}
                t = (f"TODAY's ratings on races both have seen ({start} .. {today}) -- fit, "
                     "not foresight")
                score(t, rl, kk, ours, info, a.min_both)
                score(t + " -- runners on a COLLEGE form rating of ours", rl, kk, ours,
                      info, a.min_both, keep=lambda p: _isCollege(ours, p))
            return

        snaps = sorted(glob.glob(SNAP_GLOB))
        if not snaps:
            print("[kailo] no snapshot yet: run --snapshot (and again every few days)")
            return
        for path in snaps:
            s = json.load(open(path))
            kin = {int(p): x for p, x in s["kailo"].items()}
            kk = {p: x["fitness_5k_s"] for p, x in kin.items() if x.get("fitness_5k_s")}
            ours = {int(p): tuple(v) for p, v in s["ours"].items()}
            rl = races(cur, s["cut"], today, a.min_field)
            score(f"SNAPSHOT {os.path.basename(path)} (both as of {s['cut']}) on races "
                  f"{s['cut']} .. {today}", rl, kk, ours, kin, a.min_both)


if __name__ == "__main__":
    main()
