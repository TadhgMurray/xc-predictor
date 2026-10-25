#!/usr/bin/env python3
"""
kailo_compare.py -- our race predictions against Kailo's KPACE, on the same
college cross country races and the same runners. READ-ONLY on our
database; Kailo's public API.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/kailo_compare.py                # score every race Kailo scored
    /srv/venv/bin/python scripts/kailo_compare.py --season 2026 --show 10
    /srv/venv/bin/python scripts/kailo_compare.py --record       # save OUR predictions for
                                                                 # their upcoming races, now
    /srv/venv/bin/python scripts/kailo_compare.py --basis model  # score one basis of ours
    sudo bash deploy/install_kailo_record_timer.sh               # --record every 6 hours

  The RATINGS alone (no course, no day): scripts/kailo_ratings.py.

★ WHY (owner, 2026-10-21: "how can we compare our accuracy to Kailo's").
  Kailo publishes, per college meet, every runner's predicted time with the
  time it was published (races[].published_log; the scored version is
  final_seq, published before start_at), and scores itself in public
  (/api/v1/rankings/track-record: average miss 2.84%, backtests 2.86%).
  Their number and our scorecard's are not the same statistic on the same
  runners, so this lines the two up runner by runner:

    miss        |predicted - actual| / actual, in % -- Kailo's own measure
                (mean), and the median beside it
    within 2%   the share of runners predicted within 2% -- theirs too
    bias        mean signed miss (+ = predicted slower than they ran)
    order       of every pair of finishers in a race, how often each
                system had them in the right order

  On the runners BOTH predicted and who finished, only.

★ OURS, TWO WAYS, AND IT SAYS WHICH.
    recorded  --record ran before the race and saved our numbers in
              engine/data/kailo/<race>.json. The fair one: neither system
              could see the result.
    rebuilt   no recording: racecast.predict._servedTimes on the meet with
              history_before = race day, so no result from that day or
              later is in any runner's form. The course's difficulty is
              today's number, which has seen this race -- slightly
              flattering to us, and printed as "rebuilt".

Matching: a Kailo race -> our tfrrs meet by date (+-1 day) and name; the
division by distance (3%), sex and the section's own words ("Maize",
"Navy"); a runner by name (words sorted, as lacctic_compare) then school.
"""
import argparse
import datetime
import json
import math
import os
import re
import statistics
import sys
import time

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

API = "https://kailo.fit/api/v1/rankings"
STORE = os.path.join(_ROOT, "engine", "data", "kailo")
_S = requests.Session()
_S.headers.update({"User-Agent": "racecast-compare/1.0 (read-only research)",
                   "Accept": "application/json"})
# ★ EVERY RACE ALREADY RUN, NOT ONLY THE ONES KAILO SCORED (2026-10-21: of 9
#   races, 2 "scored"; D3 and D2 pre-nationals read "results_unavailable" /
#   "awaiting_results" for weeks). Their prediction for those was published
#   before the start all the same, and the results are in our tfrrs rows.
SCORED = {"scored", "results_unavailable", "awaiting_results", "held", "locked"}
UPCOMING = {"published", "locked"}
# trend (review 2026-10, predict.trendApply): recorded beside the others before
#   race day, so the clean test can judge it before anyone serves it
BASES = ("rating", "guard", "model", "blend", "trend")
_STOP = {"the", "of", "and", "at", "xc", "cross", "country", "invitational",
         "invite", "meet", "championship", "championships", "college", "open"}


def fetch(path, sleep=1.0, params=None):
    for attempt in range(3):
        r = _S.get(f"{API}{path}", params=params, timeout=60)
        if r.status_code == 200:
            time.sleep(sleep)
            return r.json()
        time.sleep(max(sleep, 3) * (attempt + 1))
    raise SystemExit(f"[kailo] {path}: HTTP {r.status_code}")


# ------------------------------------------------------------------ #
#  pure parts
# ------------------------------------------------------------------ #

def nameKey(s):
    """'Hammerand, Isaiah' and 'Isaiah Hammerand' -> 'hammerand isaiah'."""
    return " ".join(sorted(re.findall(r"[a-z]+", (s or "").lower())))


def words(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in _STOP}


def similarity(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def finalVersion(race):
    """The prediction Kailo scored, if it was published before the start:
    race['latest'] when its seq is final_seq. None otherwise."""
    latest = race.get("latest") or {}
    final = race.get("final_seq")
    if not latest.get("rows") or final is None or latest.get("seq") != final:
        return None
    start = race.get("start_at") or ""
    pub = [p.get("published_at") for p in race.get("published_log") or []
           if p.get("seq") == final]
    if start and pub and pub[0] and pub[0] >= start:
        return None
    return latest


_SEX_WORDS = {"women", "woman", "womens", "female", "girls", "men", "man", "mens", "male",
              "boys"}


def _divSimilarity(a, b):
    """A section name against a division name: the words that tell sections
    apart ("Maize", "Navy Championship", "Open"), with the apostrophes, the
    sex and the distance taken out. No stop list: "Open" is a section here."""
    def w(x):
        toks = re.findall(r"[a-z0-9]+", (x or "").lower().replace("'", "").replace("’", ""))
        return {t for t in toks if t not in _SEX_WORDS and not re.search(r"\d", t)}
    wa, wb = w(a), w(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def pickDivision(divs, distance_m, sex, section):
    """divs: {div_id: (distance, name)}. The division of the race: distance
    within 3%, the sex's word, then most of the section's own words."""
    # ! "Womens"/"Mens" WITHOUT THE APOSTROPHE (2026-10-22: all five Running
    #   of the Cows sections unmatched, ours read "Womens Maize 6000M")
    want_sex = {"F": ("women", "woman", "womens", "female", "girls"),
                "M": ("men", "man", "mens", "male", "boys")}.get(sex, ())
    best, best_score = None, -1.0
    for div, (dist, name) in divs.items():
        if not dist or abs(float(dist) - distance_m) > 0.03 * distance_m:
            continue
        low = (name or "").lower()
        toks = set(re.findall(r"[a-z]+", low))
        if want_sex and not (toks & set(want_sex)):
            continue
        if sex == "M" and toks & {"women", "woman", "womens", "female", "girls"}:
            continue
        score = _divSimilarity(section, name)
        if score > best_score:
            best, best_score = div, score
    return best


def scores(rows):
    """rows: [(predicted_ours, predicted_theirs, actual)] seconds, one race.
    Returns per-system lists of signed % misses and (good_ours, good_theirs,
    pairs) for the order. Pure."""
    miss_o = [100.0 * (o - a) / a for o, _t, a in rows]
    miss_t = [100.0 * (t - a) / a for _o, t, a in rows]
    go = gt = n = 0
    for i in range(len(rows)):
        for k in range(i + 1, len(rows)):
            ai, ak = rows[i][2], rows[k][2]
            if ai == ak:
                continue
            first = ai < ak
            n += 1
            go += (rows[i][0] < rows[k][0]) == first
            gt += (rows[i][1] < rows[k][1]) == first
    return miss_o, miss_t, (go, gt, n)


def summary(miss):
    if not miss:
        return None
    ab = [abs(x) for x in miss]
    return {"n": len(miss), "mean": sum(ab) / len(ab), "median": statistics.median(ab),
            "within2": sum(x <= 2.0 for x in ab) / len(ab), "bias": sum(miss) / len(miss)}


# ------------------------------------------------------------------ #
#  our side
# ------------------------------------------------------------------ #

def ourMeet(cur, name, day, lo_days=-1, hi_days=1, course=None):
    """(meet_id, meet_name, {div: (distance, name)}) of the tfrrs XC meet
    that is this race (dated within [day + lo_days, day + hi_days]), or None.

    ★ OR THE SAME VENUE (2026-10-22: "NCAA Division II Pre-Nationals" found
      nothing; a meet's name is the host's to choose, the course is not).
      Kailo's course name against our venue name counts as a fair match."""
    d = datetime.date.fromisoformat(day)
    cur.execute("""
        SELECT meet_id, meet_name, division_distances, venue_name
        FROM   meets_tfrrs
        WHERE  sport = 'XC'
          AND  substr(date::text, 1, 10) BETWEEN %s AND %s""",
                ((d + datetime.timedelta(days=lo_days)).isoformat(),
                 (d + datetime.timedelta(days=hi_days)).isoformat()))
    best, best_s = None, 0.25
    for mid, mname, divs, venue in cur.fetchall():
        s = similarity(name, mname)
        if course and similarity(course, venue) >= 0.5:
            s = max(s, 0.5)
        if s > best_s:
            best, best_s = (mid, mname, divs), s
    if not best:
        return None
    mid, mname, divs = best
    if isinstance(divs, str):
        divs = json.loads(divs or "{}")
    out = {}
    for k, v in (divs or {}).items():
        if isinstance(v, dict) and str(k).isdigit():
            out[int(k)] = (v.get("distance"), v.get("div_name") or v.get("name") or "")
    return mid, mname, out


def ourResults(cur, meet_id, div_id):
    """[(person_id, name key, school, seconds)] of the division's finishers."""
    cur.execute("""
        SELECT r.person_id, r.time_seconds, r.school,
               (SELECT NULLIF(TRIM(concat_ws(' ', a.first_name, a.last_name)), '')
                FROM athletes a WHERE a.athlete_id IN (r.person_id, r.athlete_id)
                  AND COALESCE(TRIM(a.last_name), '') <> '' LIMIT 1),
               r.athlete_name
        FROM   results r
        WHERE  r.source = 'tfrrs' AND r.meet_id = %s AND r.div_id = %s
          AND  r.time_seconds > 0 AND r.time_seconds < 20000
          AND  r.person_id IS NOT NULL""", (meet_id, div_id))
    return [(pid, nameKey(nm or an), sch or "", float(sec))
            for pid, sec, sch, nm, an in cur.fetchall()]


def ourPredictions(cur, person_ids, target, detail=None):
    """{person_id: seconds} from the predictor every page serves. `detail`,
    a dict, also gets each runner's whole entry (form pool, races, basis).

    ! ON A DICT CURSOR (2026-10-22, first server run: "dictionary update
      sequence element #0 has length 28"). predict reads rows by column
      name, as the site's RealDictCursor gives them; this script's own
      queries unpack tuples."""
    import predict as P
    from psycopg2.extras import RealDictCursor
    dcur = cur.connection.cursor(cursor_factory=RealDictCursor)
    preds = P._servedTimes(dcur, list(person_ids), target)
    out = {}
    for pid, e in zip(person_ids, preds):
        if e and e.get("seconds") and e.get("is_race_time", True):
            out[pid] = float(e["seconds"])
            if detail is not None:
                detail[pid] = e
    return out


def targetLine(cur, target):
    """What our predictor thinks the race is: course, distance, the venue's
    difficulty cell -- the conversion every rating-served time goes through."""
    import predict as P
    from psycopg2.extras import RealDictCursor
    try:
        sp = P._targetSpec(cur.connection.cursor(cursor_factory=RealDictCursor), target)
    except Exception as exc:                                    # noqa: BLE001
        cur.connection.rollback()
        return f"(spec failed: {exc})"
    cur.connection.rollback()
    return (f"course {sp.get('course_name')!r}  {sp.get('distance_meters')} m  "
            f"canonical {sp.get('canonical_id')}  difficulty {sp.get('course_difficulty')}  "
            f"cell {sp.get('venue_cell')}  date {sp.get('date')}")


def runnerGroups(e):
    """[(table, group)] a runner's misses are filed under, from their
    predictor entry. Pure."""
    e = e or {}
    pool = (e.get("form_pool") or "?").split("|", 1)[0].split("_", 1)[0] or "?"
    n = e.get("n_races")
    nb = "?" if n is None else ("1-2" if n <= 2 else "3-5" if n <= 5 else "6+")
    return [("form pool", pool), ("races in form", nb),
            ("basis", e.get("basis") or e.get("time_basis") or "?"),
            ("stale", "yes" if (e.get("stale_years") or 0) > 0 else "no")]


def breakdown(recs):
    """recs: [(groups, miss_ours, miss_theirs)]. {(table, group): (n, mean
    |ours|, mean |theirs|, bias ours, bias theirs)}. Pure."""
    acc = {}
    for groups, mo, mt in recs:
        for g in groups:
            acc.setdefault(g, []).append((mo, mt))
    out = {}
    for g, v in acc.items():
        n = len(v)
        out[g] = (n, sum(abs(a) for a, _b in v) / n, sum(abs(b) for _a, b in v) / n,
                  sum(a for a, _b in v) / n, sum(b for _a, b in v) / n)
    return out


def spread(miss_by_race):
    """Average miss once each race's own mean miss is taken off: how well
    the runners are placed against each other, with the course and the
    day's level set aside. Pure."""
    xs = []
    for ms in miss_by_race:
        if ms:
            m = sum(ms) / len(ms)
            xs += [abs(x - m) for x in ms]
    return sum(xs) / len(xs) if xs else None


def matchRunners(kailo_rows, athletes, ours):
    """[(kailo predicted, our person, actual)] for the runners on both sides.
    kailo_rows: Kailo's rows; athletes: Kailo's athlete dict; ours:
    ourResults. Matched on the name key, ties broken by school."""
    by_name = {}
    for pid, key, school, sec in ours:
        by_name.setdefault(key, []).append((pid, school, sec))
    out = []
    for row in kailo_rows:
        ath = athletes.get(row.get("athlete_key")) or {}
        cands = by_name.get(nameKey(ath.get("display_name")), [])
        if not cands or not row.get("predicted_s"):
            continue
        if len(cands) > 1:
            cands = sorted(cands, key=lambda c: -similarity(ath.get("school_name"), c[1]))
        pid, _school, sec = cands[0]
        out.append((float(row["predicted_s"]), pid, sec))
    return out


# ------------------------------------------------------------------ #
#  the two modes
# ------------------------------------------------------------------ #

def compareSeason(cur, season, sleep, show, csv_path=None, basis=None):
    tr = fetch("/track-record", sleep, params={"season": season} if season else None)
    today = datetime.date.today().isoformat()
    races = [r for r in tr.get("races", []) if r.get("state") in SCORED
             and (r.get("race_date") or "9999") < today]
    print(f"[kailo] season {tr.get('season')}: {len(races)} races run "
          f"({sum(r.get('state') == 'scored' for r in races)} scored by Kailo); their published "
          f"average miss {tr.get('avg_miss_pct') or 0:.2f}% (backtests "
          f"{(tr.get('benchmark') or {}).get('avg_miss_pct', float('nan')):.2f}%)")
    meets = {}
    all_o, all_t, order = [], [], [0, 0, 0]
    per_race = []
    recs, by_race_o, by_race_t, csv_rows = [], [], [], []
    for r in races:
        mk = r["meet_key"]
        if mk not in meets:
            meets[mk] = fetch(f"/meets/{mk}", sleep)
        m = meets[mk]
        race = next((x for x in m.get("races", []) if x.get("race_key") == r["race_key"]), None)
        ver = finalVersion(race or {})
        label = f"{r['meet_name']} -- {r['section']} ({r['race_date']})"
        if ver is None:
            print(f"  ✗ {label}: no prediction published before the start")
            continue
        found = ourMeet(cur, r["meet_name"], r["race_date"],
                        course=((m.get("meet") or {}).get("venue") or {}).get("course_name"))
        if not found:
            print(f"  ✗ {label}: no tfrrs meet of ours matches")
            continue
        mid, mname, divs = found
        div = pickDivision(divs, float(r["distance_m"]), r["sex"], r["section"])
        if div is None:
            print(f"  ✗ {label}: our meet {mid} ({mname}) has no matching division "
                  f"({r['distance_m']} m, {r['sex']}); ours: "
                  + "; ".join(f"{d}={nm} {dist}" for d, (dist, nm) in sorted(divs.items())))
            continue
        ours = ourResults(cur, mid, div)
        trip = matchRunners(ver["rows"], m.get("athletes") or {}, ours)
        store = os.path.join(STORE, f"{r['race_key'].replace(':', '_')}.json")
        how = "rebuilt"
        recorded, detail = {}, {}
        if os.path.exists(store):
            try:
                saved = json.load(open(store))
                # ! A BASIS THE RECORDING DOES NOT HAVE IS REBUILT, not read
                #   off the served one (review 2026-10): a file saved before
                #   by_basis -- or before "trend" -- used to hand --basis X
                #   the served basis's numbers, labelled "recorded"
                pick = ((saved.get("by_basis") or {}).get(basis) if basis
                        else saved["ours"])
                recorded = {int(k): v for k, v in (pick or {}).items()}
                how = "recorded" if recorded else "rebuilt"
            except (OSError, ValueError, KeyError):
                recorded = {}
        if not recorded:
            target = {"mode": "meet", "meet_id": mid, "div_id": div, "sport": "XC",
                      "source": "tfrrs", "history_before": r["race_date"]}
            recorded = ourPredictions(cur, [p for _t, p, _a in trip], target, detail)
            cur.connection.rollback()
            print(f"  · {label}: {targetLine(cur, target)}")
        kept = [(p, recorded[p], t, a) for t, p, a in trip if p in recorded]
        rows = [(o, t, a) for _p, o, t, a in kept]
        if len(rows) < 5:
            print(f"  ✗ {label}: {len(rows)} runners predicted by both")
            continue
        mo, mt_, (go, gt, n) = scores(rows)
        by_race_o.append(mo)
        by_race_t.append(mt_)
        for (p, o, t, a), x, y in zip(kept, mo, mt_):
            e = detail.get(p) or {}
            recs.append((runnerGroups(e), x, y))
            csv_rows.append((label, p, round(a, 1), o, t, round(x, 2), round(y, 2),
                             e.get("form_pool"), e.get("form_rating"), e.get("n_races"),
                             e.get("stale_years"), e.get("basis")))
        all_o += mo
        all_t += mt_
        order = [order[0] + go, order[1] + gt, order[2] + n]
        so, st = summary(mo), summary(mt_)
        per_race.append((label, how, len(rows), so, st, go / n if n else None,
                         gt / n if n else None))
    if not per_race:
        print("[kailo] nothing to score yet")
        return
    print(f"\n  {'race':<62}{'how':<10}{'runners':>8}  {'miss ours':>10}{'theirs':>8}"
          f"  {'order ours':>11}{'theirs':>8}")
    for label, how, n, so, st, oo, ot in per_race[:show] if show else per_race:
        print(f"  {label[:61]:<62}{how:<10}{n:>8}  {so['mean']:>9.2f}%{st['mean']:>7.2f}%"
              f"  {100 * (oo or 0):>10.1f}%{100 * (ot or 0):>7.1f}%")
    so, st = summary(all_o), summary(all_t)
    print(f"\n  ALL {so['n']:,} runners in {len(per_race)} races "
          f"({sum(1 for p in per_race if p[1] == 'recorded')} recorded before the race, "
          f"the rest rebuilt):")
    for name, s in (("ours", so), ("Kailo", st)):
        print(f"    {name:<6} average miss {s['mean']:.2f}%   median {s['median']:.2f}%   "
              f"within 2% {100 * s['within2']:.0f}%   bias {s['bias']:+.2f}%")
    if order[2]:
        print(f"    order: ours {100 * order[0] / order[2]:.1f}%   Kailo "
              f"{100 * order[1] / order[2]:.1f}%   ({order[2]:,} pairs)")
    so_, st_ = spread(by_race_o), spread(by_race_t)
    if so_ is not None:
        print(f"    with each race's own level taken off: ours {so_:.2f}%   Kailo {st_:.2f}%"
              "   (the runners against each other; the course and day aside)")
    print("  (miss = |predicted - actual| / actual; Kailo's own track record uses the "
          "average. 'rebuilt' = ours from results before race day only.)")
    if recs and any(g for g, _a, _b in recs):
        print(f"\n  where ours misses ('?' = recorded races, no entry kept)\n  {'':<16}{'group':<12}{'runners':>8}"
              f"{'miss ours':>11}{'Kailo':>8}{'bias ours':>11}{'Kailo':>8}")
        bd = breakdown(recs)
        for (tab, grp), (n, ao, at, bo, bt) in sorted(bd.items()):
            print(f"  {tab:<16}{str(grp):<12}{n:>8}{ao:>10.2f}%{at:>7.2f}%{bo:>+10.2f}%{bt:>+7.2f}%")
    if csv_path and csv_rows:
        import csv
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["race", "person_id", "actual_s", "ours_s", "kailo_s", "miss_ours",
                        "miss_kailo", "form_pool", "form_rating", "n_races", "stale_years",
                        "basis"])
            w.writerows(csv_rows)
        print(f"  {len(csv_rows)} runners written to {csv_path}")


def recordUpcoming(cur, season, sleep):
    """Save our predictions for Kailo's published, not yet run races."""
    import predict as P
    tr = fetch("/track-record", sleep, params={"season": season} if season else None)
    os.makedirs(STORE, exist_ok=True)
    keys = {r["meet_key"] for r in tr.get("races", [])}
    if tr.get("next_meet"):
        keys.add(tr["next_meet"]["meet_key"])
    n_saved = 0
    for mk in sorted(keys):
        m = fetch(f"/meets/{mk}", sleep)
        athletes = m.get("athletes") or {}
        course = ((m.get("meet") or {}).get("venue") or {}).get("course_name")
        for race in m.get("races", []):
            if race.get("state") not in UPCOMING or not (race.get("latest") or {}).get("rows"):
                continue
            store = os.path.join(STORE, f"{race['race_key'].replace(':', '_')}.json")
            # ★ RE-RECORDED UNTIL THE GUN, so ours is as late as Kailo's final
            #   version (published the morning of); never after the start
            now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            if not race.get("start_at") or race["start_at"] <= now:
                continue
            day = (race.get("start_at") or "")[:10]
            people = {}
            for row in race["latest"]["rows"]:
                ath = athletes.get(row.get("athlete_key")) or {}
                pid = findPerson(cur, ath.get("display_name"), ath.get("school_name"))
                if pid:
                    people[pid] = row["athlete_key"]
            if not people:
                continue
            target = upcomingTarget(cur, m, race, day, course)
            # ★ EVERY BASIS, so the clean test can say which one to serve
            #   (2026-10-22: guard 4.70%, model 3.71% on Cowboy Jamboree)
            served = os.environ.get("XCP_PREDICT_BASIS")
            by_basis = {}
            for b in BASES:
                os.environ["XCP_PREDICT_BASIS"] = b
                by_basis[b] = {str(p): s for p, s in
                               ourPredictions(cur, list(people), target).items()}
                cur.connection.rollback()
            if served is None:
                os.environ.pop("XCP_PREDICT_BASIS", None)
            else:
                os.environ["XCP_PREDICT_BASIS"] = served
            got = by_basis.get(served or "rating") or by_basis["rating"]
            json.dump({"race_key": race["race_key"], "recorded_at":
                       datetime.datetime.utcnow().isoformat() + "Z",
                       "target": target, "served_basis": served or "rating",
                       "ours": got, "by_basis": by_basis},
                      open(store, "w"))
            n_saved += 1
            print(f"  ✓ {race['race_key']}: {len(got)} of {len(race['latest']['rows'])} "
                  f"runners predicted ({day}, {target['mode']}: "
                  f"{target.get('meet_id') or course})")
    print(f"[kailo] {n_saved} upcoming races recorded under {STORE}")


def upcomingTarget(cur, meet, race, day, course):
    """The predictor's target for a race not yet run.

    ★ LAST YEAR'S EDITION, RE-RUN ON THIS YEAR'S DATE (2026-10-22). A
      'manual' target finds its course by the anet meets table's exact
      course name, which Kailo's venue name almost never is -- so the race
      would be predicted on a neutral course, the conversion that came out
      8% fast at Cowboy Jamboree. The same meet a year earlier is in
      meets_tfrrs with its venue, distance and course cell; 'rerun' keeps
      that and moves the date. Manual only when there is no last edition."""
    name = (meet.get("meet") or {}).get("name") or race.get("meet_name") or ""
    dist = float(race.get("distance_m") or 0) or None
    for years in (1, 2):
        found = (ourMeet(cur, name, day, -365 * years - 21, -365 * years + 21, course=course)
                 if name else None)
        if not found or not dist:
            continue
        mid, _mname, divs = found
        div = pickDivision(divs, dist, race.get("sex"), race.get("section") or "")
        if div is not None:
            return {"mode": "rerun", "meet_id": mid, "div_id": div, "sport": "XC",
                    "source": "tfrrs", "date": day, "history_before": day}
    return {"mode": "manual", "sport": "XC", "date": day, "distance": dist,
            "course": course, "history_before": day}


def findPerson(cur, name, school):
    """Our person for a Kailo athlete with no results in our meet yet."""
    parts = re.findall(r"[A-Za-z'\-]+", name or "")
    if len(parts) < 2:
        return None
    cur.execute("""SELECT person_id, school FROM athletes
                   WHERE lower(first_name) = %s AND lower(last_name) = %s
                     AND person_id IS NOT NULL LIMIT 20""",
                (parts[0].lower(), " ".join(parts[1:]).lower()))
    rows = cur.fetchall()
    if not rows:
        return None
    rows.sort(key=lambda r: -similarity(school, r[1]))
    return rows[0][0]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--record", action="store_true",
                    help="save our predictions for Kailo's upcoming races now")
    ap.add_argument("--show", type=int, default=0, help="races listed (0 = all)")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--csv", default=None, help="write every runner's row here")
    ap.add_argument("--basis", choices=BASES, default=None,
                    help="score this basis of ours (default: what the site serves, "
                         "XCP_PREDICT_BASIS)")
    a = ap.parse_args()
    if a.basis:
        os.environ["XCP_PREDICT_BASIS"] = a.basis
    from database import getConn
    with getConn() as conn:
        cur = conn.cursor()
        if a.record:
            recordUpcoming(cur, a.season, a.sleep)
        else:
            compareSeason(cur, a.season, a.sleep, a.show, a.csv, a.basis)
        conn.rollback()


if __name__ == "__main__":
    main()
