#!/usr/bin/env python3
"""
qualifier_audit.py -- for meets you QUALIFY for (sectionals -> regionals ->
state, conference -> regionals -> nationals), would "who qualified at this
year's earlier rounds" guess the field better than "the teams that came last
year"? Measured on a finished season. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/qualifier_audit.py --season 2025          # rules from 2024, scored on 2025

★ WHY (owner, 2026-11-02: "we have no way currently to make it so that you
  can 'qualify' for a meet, it just takes the same teams/amount as last
  year"). A state meet's field is the regionals' qualifiers, this year's.

★ EVERYTHING FROM THE RESULTS, NOTHING TYPED IN. For each race R of the
  season before (Y-1), its FEEDERS are the races in the 28 days before it
  whose finishers went on to R, when those who went on were the TOP of the
  feeder (most of them inside the first 1.5x as many places) and only part
  of it (at most 70%). R is a qualifying race when its feeders hold 60% or
  more of its finishers. Each feeder's rule is then read off: k teams (the
  schools with four or more of their feeder runners in R) and m individuals
  (R's other runners from that feeder).

★ ONLY MEETS NAMED AS A ROUND (race_rows.meetType 4+: conference, district,
  sectional, regional, state, national). The first run took invitationals
  too: good runners top their small meets, so those passed the feeder test.

★ APPLIED A YEAR LATER, AS THE SITE WOULD. This year's edition of R is found
  by name (last_edition.editionName) and race (last_edition.mapRace); each
  feeder's by name, else by the most of last year's feeder runners who came
  back to one race within ten days of its date. Never by who ran R. The field: each feeder's
  top k teams by team score (all of their runners there, at most seven) and
  its top m individuals off those teams.

  Scored against who finished this year's R:
    found     share of R's finishers in the guessed field
    precise   share of the guessed field who finished R
    last year share of R's finishers whose SCHOOL ran R last year: the
              most today's method (last year's teams, this year's squads)
              could list, before it picks the wrong seven
"""
import argparse
import collections
import datetime
import math
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

MIN_FIELD = 30
FEED_DAYS = 28
TOP_SLACK = 1.5          # the qualifiers sit inside the first 1.5x as many places
TOP_SHARE = 0.6          # ... most of them
MAX_ADVANCE = 0.7        # a feeder sends on at most this share of its field
MIN_COVER = 0.6          # a qualifying race: its feeders hold this share of it
TEAM_MIN = 4             # a school with this many feeder runners in R went as a team
TEAM_SIZE = 7
ROUTE_FOUND = 0.7        # --write-routes: a route must rebuild this much of its own field
ROUTE_PRECISE = 0.6      # ... at this precision
NEXT_FOUND = 0.6         # ... and, where this season's edition has run, find this much of it
# ! NOT A MEET YOU QUALIFY FOR (review of the first routes file): NXR regionals
#   are open entry (the round for NXN), a "Top 10" race is each team's best
#   ten, an invitational invites
NOT_A_ROUTE = re.compile(r"\bnxr\b|nike cross regional|\btop\s*\d+|seeded|invit|"
                         r"ncaa.*\bregion", re.I)
# ★ A LEAGUE'S OWN CHAMPIONSHIP IS EVERY TEAM IN THE LEAGUE (review of the
#   weakest routes: Coastal Conference, SMC, HVAL, PCAL, City League, CCSD
#   middle schools ... one or two "rounds", the league's last meet before
#   it). Such a name needs LEAGUE_ROUNDS rounds and a strong own field to be
#   a route; every route needs MIN_ROUNDS and own found x precise MIN_OWN.
LEAGUE = re.compile(r"conference|league|\bcity\b|county|\bcyo\b|\bisd\b|wpial", re.I)
# ★ AND THE REVERSE, BECAUSE LEAGUES GO BY INITIALS (second review: NAIC,
#   BIIF, BCL, GFMSL, Bi-State MS, FCPS middle schools, MSHSL and Shenandoah
#   sections, a Maine regional -- open meets, every team of the area). Only
#   a name that says it is a level you qualify for passes at the ordinary
#   bar; any other needs the league bar. ! "Bi-State" / "Tri-State" are
#   leagues, not a state meet (third review: Bi-State MS).
QUALIFIED = re.compile(r"(?<![-\w])state\b|federation|interscholastic|junior olympic|\bnational|"
                       r"\bnaia\b|\bnjcaa\b|\bcif\b|uil region|3c2a", re.I)
MIN_ROUNDS, LEAGUE_ROUNDS, MIN_OWN = 2, 4, 0.55


def keepRoute(r):
    """Is a learned route a meet you qualify for? Pure."""
    name = f"{r.get('meet_name') or ''} {r.get('race') or ''}"
    if NOT_A_ROUTE.search(name) or r.get("next_found", 1.0) < NEXT_FOUND:
        return False
    n = len(r.get("rounds") or [])
    if n < MIN_ROUNDS or r["own_found"] * r["own_precise"] < MIN_OWN:
        return False
    meet = r.get("meet_name") or ""
    if LEAGUE.search(meet) or not QUALIFIED.search(meet) or re.search(r"europe", meet, re.I):
        return n >= LEAGUE_ROUNDS and r["own_found"] >= 0.85 and r["own_precise"] >= 0.8 \
            and not re.search(r"europe", meet, re.I)
    return True


def placeRanks(rows):
    """{person_id: place} by time. rows: [(pid, school, seconds)]. Pure."""
    return {pid: i + 1 for i, (pid, _s, _t) in enumerate(sorted(rows, key=lambda r: r[2]))}


def teamOrder(rows):
    """Schools with five or more finishers, best team score first (the sum
    of their top five places among such schools' runners). Pure."""
    by = collections.defaultdict(list)
    for pid, sch, t in rows:
        if sch:
            by[sch].append((t, pid))
    teams = {s for s, v in by.items() if len(v) >= 5}
    scoring = sorted((t, pid, s) for s, v in by.items() if s in teams for t, pid in v)
    place, pts = 0, collections.defaultdict(list)
    for t, pid, s in scoring:
        place += 1
        if len(pts[s]) < 5:
            pts[s].append(place)
    return sorted(teams, key=lambda s: (sum(pts[s]), s))


def feederRule(R, F):
    """(k teams, m individuals) R took from feeder F, or None when F is not
    a feeder of R (see the header). R, F: [(pid, school, seconds)]. Pure."""
    rset = {p for p, _s, _t in R}
    went = [(p, s) for p, s, _t in F if p in rset]
    if len(went) < 3 or len(went) > MAX_ADVANCE * len(F):
        return None
    pl = placeRanks(F)
    top = sum(pl[p] <= math.ceil(TOP_SLACK * len(went)) for p, _s in went) / len(went)
    if top < TOP_SHARE:
        return None
    per = collections.Counter(s for _p, s in went if s)
    teams = {s for s, n in per.items() if n >= TEAM_MIN}
    m = sum(1 for _p, s in went if s not in teams)
    return len(teams), m


def qualifiers(F, k, m):
    """The field a feeder sends on: its top k teams' runners (at most seven
    each, fastest first) and its top m individuals off those teams. Pure."""
    order = teamOrder(F)
    teams = set(order[:k])
    out, per = set(), collections.Counter()
    ind = []
    for pid, sch, _t in sorted(F, key=lambda r: r[2]):
        if sch in teams:
            if per[sch] < TEAM_SIZE:
                out.add(pid)
                per[sch] += 1
        else:
            ind.append(pid)
    return out | set(ind[:m])


def _season(conn, y):
    """{race: [(pid, school, s)]}, {race: date} for the XC races of the
    season starting August y. race = (source, meet_id, div_id). Streamed."""
    from season_year import seasonStart          # the one season seam (Aug 15)
    lo, hi = seasonStart(y).isoformat(), f"{y}-12-31"
    cur = conn.cursor(name=f"qa_{y}")
    cur.itersize = 200_000
    cur.execute("""
        SELECT source, meet_id, div_id, date, person_id, school, time_seconds
        FROM   results
        WHERE  date ~ '^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}$' AND date BETWEEN %s AND %s
          AND  person_id IS NOT NULL AND time_seconds > 0 AND time_seconds < 20000
          AND  meet_id IS NOT NULL AND div_id IS NOT NULL""",
                (lo, hi))
    races, day = collections.defaultdict(list), {}
    seen = set()
    for src, mid, div, d, pid, sch, t in cur:
        key = (src, int(mid), int(div))
        if (key, pid) in seen:
            continue
        seen.add((key, pid))
        races[key].append((int(pid), sch, float(t)))
        day.setdefault(key, str(d)[:10])
    cur.close()
    return races, day


def _names(cur):
    cur.execute("SELECT DISTINCT 'anet', meet_id, meet_name FROM meets")
    out = {(s, int(m)): n for s, m, n in cur.fetchall()}
    cur.execute("SELECT 'tfrrs', meet_id, meet_name FROM meets_tfrrs WHERE sport = 'XC'")
    out.update({(s, int(m)): n for s, m, n in cur.fetchall()})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, required=True,
                    help="the season to score (its August's year); rules from the one before")
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--write-routes", default=None, metavar="JSON",
                    help="write the routes learned from the season before (each championship "
                         "race, its rounds, k teams and m individuals from each) for "
                         "racecast/qualifying.py; only routes that rebuild their own field")
    ap.add_argument("--frozen", default=None, metavar="TABLE",
                    help="this season, against the site's own field: meet_forecast (or a "
                         "pred_ archive), for the races it froze -- and the two combined")
    ap.add_argument("--max-races", type=int, default=600,
                    help="the largest qualifying races only (each reads its meets' races)")
    a = ap.parse_args()
    if a.frozen and not (a.frozen == "meet_forecast" or (
            a.frozen.startswith("pred_") and a.frozen.replace("_", "").isalnum())):
        raise SystemExit(f"--frozen {a.frozen!r}: meet_forecast or a pred_ name only")

    import psycopg2.extras
    import last_edition as LE
    import race_rows as RR
    from database import getConn

    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '1800s'")
        t0 = datetime.datetime.now()
        prev, dprev = _season(conn, a.season - 1)
        this, dthis = _season(conn, a.season)
        cur = conn.cursor()
        names = _names(cur)
        print(f"read {len(prev):,} and {len(this):,} races ({datetime.datetime.now() - t0})")
        dcur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        by_person = collections.defaultdict(list)
        for key, rows in prev.items():
            for pid, _s, _t in rows:
                by_person[pid].append(key)
        # this season's meets by edition name, for finding next year's editions
        ed_this = collections.defaultdict(set)
        for (src, mid, _div) in this:
            ed_this[(src, LE.editionName(names.get((src, mid))))].add(mid)
        races_of = {}

        def racesOf(src, mid):
            if (src, mid) not in races_of:
                races_of[(src, mid)] = LE.editionRaces(dcur, mid, "XC", src)
                conn.rollback()
            return races_of[(src, mid)]

        by_person_this = collections.defaultdict(list)
        for key, rows in this.items():
            for pid, _s, _t in rows:
                by_person_this[pid].append(key)

        def byReturners(F, near):
            """★ OR BY WHO CAME BACK (owner's run, 2026-11-02: most feeders
            were "not found" by name). This season's race, within ten days
            of `near`, that the most of last season's race's runners ran --
            at least a quarter of them. Last season's runners only: nothing
            of the race being guessed."""
            votes = collections.Counter()
            for pid, _s, _t in prev[F]:
                for k in by_person_this.get(pid, ()):
                    if k[0] == F[0] and abs((datetime.date.fromisoformat(dthis[k])
                                             - near).days) <= 10:
                        votes[k] += 1
            if not votes:
                return None
            k, n = votes.most_common(1)[0]
            return k if n >= max(5, 0.25 * len(prev[F])) else None

        def nextYear(src, mid, div, near, returners=False):
            """This season's edition of last season's race (src, mid, div),
            dated within three weeks of `near` (a date a year on): by name
            and race, else (returners=True) by who came back."""
            nm = LE.editionName(names.get((src, mid)))
            old = racesOf(src, mid) if nm else []
            me = next((r for r in old if int(r["div_id"]) == div), None)
            if me is None:
                return byReturners((src, mid, div), near) if returners else None
            for m2 in ed_this.get((src, nm), ()):
                if m2 == mid:
                    continue
                new = racesOf(src, m2)
                for r in new:
                    key = (src, m2, int(r["div_id"]))
                    if key not in dthis or abs((datetime.date.fromisoformat(dthis[key])
                                                - near).days) > 21:
                        continue
                    ids, how = LE.mapRace(r, new, old)
                    if how == "race" and ids and div in ids:
                        return key
            return byReturners((src, mid, div), near) if returners else None

        found = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
        # --frozen: group -> {method: [finishers found, listed]} and races
        versus = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
        vs_n = collections.Counter()
        vs_fin = collections.Counter()
        shown = []
        n_qual = 0
        done = 0
        routes = []
        for R, rrows in sorted(prev.items(), key=lambda kv: -len(kv[1])):
            if len(rrows) < MIN_FIELD or dprev[R][5:7] not in ("10", "11", "12"):
                continue
            if done >= a.max_races:
                break
            # ★ ONLY A MEET NAMED AS A ROUND (owner's run: invitationals passed
            #   the feeder test -- good runners top their small meets too)
            if RR.meetType(names.get((R[0], R[1]))) < 4:
                continue
            rday = datetime.date.fromisoformat(dprev[R])
            # ★ EACH RUNNER'S LAST RACE BEFORE IT, NOT EVERY ONE (review of the
            #   first routes file: IHSA 1A state read 38 "rounds", its
            #   regionals as well as its sectionals, so regional qualifiers
            #   who then went out at sectionals were listed for state)
            feeders = collections.Counter()
            for pid, _s, _t in rrows:
                before = [F for F in by_person[pid] if F[0] == R[0] and F != R and
                          1 <= (rday - datetime.date.fromisoformat(dprev[F])).days <= FEED_DAYS]
                if before:
                    feeders[max(before, key=lambda F: dprev[F])] += 1
            rules, cover = {}, set()
            for F, n in feeders.items():
                if n < 3:
                    continue
                rule = feederRule(rrows, prev[F])
                if rule:
                    rules[F] = rule
                    fids = {p for p, _s, _t in prev[F]}
                    cover |= {p for p, _s, _t in rrows if p in fids}
            if len(cover) < MIN_COVER * len(rrows):
                continue
            n_qual += 1
            done += 1
            # ★ THE ROUTE MUST REBUILD ITS OWN FIELD (owner: "you're checking
            #   it"): the rule applied to last season's rounds against last
            #   season's race. Only routes that do are written.
            rset = {p for p, _s, _t in rrows}
            own = set()
            for F, (k, m) in rules.items():
                own |= qualifiers(prev[F], k, m)
            own_found = len(own & rset) / max(len(rset), 1)
            own_prec = len(own & rset) / max(len(own), 1)
            route = None
            if (a.write_routes and own_found >= ROUTE_FOUND and own_prec >= ROUTE_PRECISE
                    and not NOT_A_ROUTE.search(names.get((R[0], R[1])) or "")):
                def label(key):
                    r = next((x for x in racesOf(key[0], key[1])
                              if int(x["div_id"]) == key[2]), {})
                    return r.get("label"), r.get("gender"), r.get("distance")
                lab, gen, dist = label(R)
                route = {"source": R[0], "meet": LE.editionName(names.get((R[0], R[1]))),
                         "meet_name": names.get((R[0], R[1])), "race": lab, "gender": gen,
                         "distance": dist, "date": dprev[R],
                         "own_found": round(own_found, 3), "own_precise": round(own_prec, 3),
                         "rounds": []}
                for F, (k, m) in sorted(rules.items(), key=lambda kv: dprev[kv[0]]):
                    fl, fg, fd = label(F)
                    route["rounds"].append({"meet": LE.editionName(names.get((F[0], F[1]))),
                                            "meet_name": names.get((F[0], F[1])), "race": fl,
                                            "gender": fg, "distance": fd, "date": dprev[F],
                                            "teams": k, "individuals": m,
                                            # the round itself, for qualifying._lastRound
                                            "meet_id": int(F[1]), "div_id": int(F[2])})
                routes.append(route)
            near = rday + datetime.timedelta(days=364)
            R2 = nextYear(R[0], R[1], R[2], near)
            if R2 is None:
                continue
            guess = set()
            ok = True
            for F, (k, m) in rules.items():
                F2 = nextYear(F[0], F[1], F[2],
                              datetime.date.fromisoformat(dprev[F]) + datetime.timedelta(days=364),
                              returners=True)
                if F2 is None or dthis[F2] >= dthis[R2]:
                    ok = False
                    continue
                guess |= qualifiers(this[F2], k, m)
            actual = {p for p, _s, _t in this[R2]}
            site = None
            if a.frozen:
                cur.execute(f"SELECT runners FROM {a.frozen} WHERE source = %s AND meet_id = %s "
                            "AND div_id = %s", R2)
                row = cur.fetchone()
                if row:
                    import meet_forecast as MF
                    rr = MF._load(row[0]) if isinstance(row[0], str) else row[0]
                    site = {int(r["person_id"]) for r in rr or []
                            if r.get("person_id") is not None}
            last_schools = {s for _p, s, _t in rrows if s}
            school_ok = sum(1 for _p, s, _t in this[R2] if s in last_schools)
            kind = {6: "national", 5: "state", 4: "conf / region"}.get(
                int(RR.meetType(names.get((R2[0], R2[1])))), "other")
            for g in (kind, "ALL", "ALL, every round found" if ok else "ALL, a round missing"):
                f = found[g]
                f[0] += 1
                f[1] += len(actual)
                f[2] += len(guess & actual)
                f[3] += len(guess)
                f[4] += school_ok
                if site is not None:
                    vs_n[g] += 1
                    vs_fin[g] += len(actual)
                    # ★ THE SITE'S FIELD, THE QUALIFIERS', BOTH, AND "THE
                    #   QUALIFIERS WHEN EVERY ROUND WAS FOUND, ELSE THE SITE'S"
                    for meth, fld in (("site", site), ("qualifiers", guess),
                                      ("both", site | guess),
                                      ("qualifiers if all found", guess if ok else site)):
                        versus[g][meth][0] += len(fld & actual)
                        versus[g][meth][1] += len(fld)
            if route is not None:
                route["next_found"] = round(len(guess & actual) / max(len(actual), 1), 3)
                route["next_precise"] = round(len(guess & actual) / max(len(guess), 1), 3)
            shown.append((len(actual), names.get((R2[0], R2[1])) or str(R2), dthis[R2],
                          len(rules), ok, len(guess & actual) / max(len(actual), 1),
                          len(guess & actual) / max(len(guess), 1), school_ok / max(len(actual), 1)))
        conn.rollback()

    if a.write_routes:
        import json
        with open(a.write_routes, "w") as fh:
            # ! A ROUTE THIS SEASON'S EDITION SHOWED WRONG IS NOT WRITTEN
            n_all = len(routes)
            routes = [r for r in routes if keepRoute(r)]
            print(f"  {n_all - len(routes):,} routes left out (keepRoute: not a qualifier by "
                  "name, too few rounds, a league's own meet, or wrong this season)")
            json.dump({"made": datetime.date.today().isoformat(), "from_season": a.season - 1,
                       "routes": routes}, fh, indent=1)
        print(f"wrote {len(routes):,} routes to {a.write_routes} (each rebuilds "
              f"{100 * ROUTE_FOUND:.0f}%+ of its own field at {100 * ROUTE_PRECISE:.0f}%+ precision)")
        for r in sorted(routes, key=lambda r: -len(r["rounds"]))[:a.show]:
            print(f"    {(r['meet_name'] or '')[:40]:40} {(r['race'] or '')[:18]:18} "
                  f"{len(r['rounds']):>2} rounds, own {100 * r['own_found']:.0f}%/"
                  f"{100 * r['own_precise']:.0f}%, next "
                  + (f"{100 * r['next_found']:.0f}%/{100 * r['next_precise']:.0f}%"
                     if "next_found" in r else "-"))

    print(f"\n{n_qual:,} qualifying races in {a.season - 1}, the largest {a.max_races} (feeders hold "
          f"{100 * MIN_COVER:.0f}%+ of the field); scored on their {a.season} editions:")
    print(f"  {'':24} {'races':>6} {'finishers':>10} {'found':>7} {'precise':>8} {'last year':>10}")
    for g in ("national", "state", "conf / region", "other", "ALL",
              "ALL, every round found", "ALL, a round missing"):
        f = found.get(g)
        if f and f[0]:
            print(f"  {g:24} {f[0]:>6} {f[1]:>10,} {100 * f[2] / max(f[1], 1):>6.0f}% "
                  f"{100 * f[2] / max(f[3], 1):>7.0f}% {100 * f[4] / max(f[1], 1):>9.0f}%")
    print("  (found: of the finishers, in the qualifiers' field; precise: of that field, "
          "finished; last year: of the finishers, school ran it last year)")
    if a.frozen:
        print(f"\n  AGAINST THE SITE'S OWN FIELD ({a.frozen}, frozen before the race), the races "
              "it froze:")
        print(f"  {'':24} {'races':>6} {'finishers':>10}   " + "   ".join(
            f"{m:>23}" for m in ("site", "qualifiers", "both", "qualifiers if all found")))
        for g in ("national", "state", "conf / region", "ALL",
                  "ALL, every round found", "ALL, a round missing"):
            if not vs_n.get(g):
                continue
            cells = []
            for m in ("site", "qualifiers", "both", "qualifiers if all found"):
                hit, n = versus[g][m]
                cells.append(f"{100 * hit / max(vs_fin[g], 1):>9.0f}% found "
                             f"{100 * hit / max(n, 1):>4.0f}% pr")
            print(f"  {g:24} {vs_n[g]:>6} {vs_fin[g]:>10,}   " + "   ".join(cells))
        print("  (found: of the finishers, listed; pr: of those listed, finished)")
    print("\n  the biggest:")
    for n, nm, d, nf, ok, fd, pr, ly in sorted(shown, reverse=True)[:a.show]:
        print(f"    {nm[:44]:44} {d}  {n:>5} finishers, {nf:>2} feeders"
              f"{'' if ok else ' (some not found)'}: found {100 * fd:.0f}%, "
              f"precise {100 * pr:.0f}%, last year {100 * ly:.0f}%")


if __name__ == "__main__":
    main()
