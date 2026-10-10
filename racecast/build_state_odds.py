# Project: xc-predictor / racecast
# File:    build_state_odds.py
# Purpose: State odds, precomputed (owner, 2026-10-10, approved). For every
#          state, division and gender of high school cross country, run the
#          season state_odds.DRAWS times (state_odds.simulate) and write each
#          athlete's and each team's odds into three tables the pages only
#          read. Nightly after the boards (nightly_update.sh) and pipeline
#          step 13h (run_pipeline.sh), off the && chain both times.
#
#     python racecast/build_state_odds.py                 # every state
#     python racecast/build_state_odds.py --state OR,WA   # just these
#     python racecast/build_state_odds.py --force         # ignore fingerprints
#     python racecast/build_state_odds.py --dry-run       # simulate, write nothing
#
# ★ INCREMENTAL PER STATE, IDEMPOTENT. Each (state, division, gender) carries a
#   fingerprint of everything its odds depend on (state_odds.fingerprint: the
#   field's ids, schools, ratings and sigmas, the rounds, the draw count and
#   MODEL_VERSION). A division whose fingerprint has not moved since the last
#   build is skipped; one that moved is recomputed with a seed taken FROM the
#   fingerprint, so the same inputs always write the same numbers. Each state
#   is written in its own transaction (delete its rows, insert them), so a
#   failed state leaves yesterday's rows standing and the others proceed.
#
# ! NOT SWAPPED, like build_rank_snapshot: the tables are small (one row per
#   athlete in a projected field) and written in place per state; a reader
#   sees the old state or the new one, never a half.
import argparse
import datetime
import json
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import projections as P                                 # noqa: E402
import state_odds as S                                  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS state_odds_meta (
    year            int     NOT NULL,
    state           text    NOT NULL,
    division        text    NOT NULL,   -- projections.divisionSlug, or 'all'
    gender          text    NOT NULL,   -- 'boys' | 'girls'
    division_label  text,
    mode            text    NOT NULL,   -- state_odds.MODE_QUAL | MODE_STATE
    rounds          jsonb,              -- the rounds simulated (no members)
    why             text,               -- why there are none, in words
    draws           int     NOT NULL,
    n_field         int     NOT NULL,
    n_no_path       int     NOT NULL DEFAULT 0,
    as_of           date,               -- the field's newest race
    fingerprint     text    NOT NULL,
    built_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (year, state, division, gender)
);
CREATE TABLE IF NOT EXISTS state_odds_athlete (
    year         int     NOT NULL,
    state        text    NOT NULL,
    division     text    NOT NULL,
    gender       text    NOT NULL,
    person_id    bigint  NOT NULL,
    name         text,
    school       text,
    school_state text,
    grade        text,
    rating       real,
    sigma        real,
    p_qualify    real,                  -- NULL: state-only odds
    p_top        real,                  -- cuts.PLACE_LINES 'top'
    p_podium     real,                  -- cuts.PLACE_LINES 'podium'
    p_win        real,
    place_mean   real,
    path         text,                  -- the round's key, 'state' or 'none'
    PRIMARY KEY (year, state, division, gender, person_id)
);
CREATE INDEX IF NOT EXISTS state_odds_athlete_person
    ON state_odds_athlete (person_id, year);
CREATE TABLE IF NOT EXISTS state_odds_team (
    year         int     NOT NULL,
    state        text    NOT NULL,
    division     text    NOT NULL,
    gender       text    NOT NULL,
    school       text    NOT NULL,
    school_state text,
    n_runners    int,
    p_qualify    real,
    p_top3       real,
    p_win        real,
    score_mean   real,
    path         text,
    PRIMARY KEY (year, state, division, gender, school)
);
CREATE INDEX IF NOT EXISTS state_odds_team_school
    ON state_odds_team (school, year);
"""


# ------------------------------------------------------------------ #
#  inputs
# ------------------------------------------------------------------ #

def _sigmas(cur, person_ids):
    """{pid: sigma} -- predict's form band per athlete, read over the same
    window predict._ratingTimes reads (its _RATING_WINDOW_DAYS)."""
    from predict import _RATING_WINDOW_DAYS, _ratingRows
    hi = datetime.date.today() + datetime.timedelta(days=1)
    lo = hi - datetime.timedelta(days=_RATING_WINDOW_DAYS)
    rows = _ratingRows(cur, person_ids, lo.isoformat(), hi.isoformat())
    out = {}
    for pid in person_ids:
        sig = S.sigmaOf(rows.get(pid) or [])
        out[pid] = sig if sig is not None else S.fallbackSigma()
    return out


def _schoolUnits(cur, state, schools):
    """{school: {section, section_div, region, state_div, class}} from
    school_unit -- the table cuts reads a finisher's section from."""
    if not schools:
        return {}
    cur.execute("""
        SELECT school, section, section_div, region, state_div, class
        FROM   school_unit
        WHERE  state = %s AND sport = 'XC' AND NOT is_college
          AND  school = ANY(%s)
    """, (state, sorted(schools)))
    out = {}
    for r in cur.fetchall():
        d = dict(r)
        out.setdefault(d.pop("school"), d)
    return out


def _marks(cur, state):
    """cuts' record of the state's rounds, or None when it cannot be read --
    which sends every division to the state-only odds, said so."""
    import cuts
    try:
        return cuts._computeUncached(cur, state)
    except Exception as exc:                            # noqa: BLE001
        cur.connection.rollback()
        print(f"  {state}: no rounds on record ({type(exc).__name__})")
        return None


def plan(cur, state, year):
    """[(division dict or None, slug, label, gender, pool)] for one state.
    ★ THE DIVISIONS /projections OFFERS; a state with none on record is one
      race ('all'), as cuts files it."""
    divs = P.divisionsFor(cur, state)
    out = []
    for g, (pool, _w) in S.GENDERS.items():
        if divs:
            for d in divs:
                out.append((d, d["slug"], d["label"], g, pool))
        else:
            out.append((None, "all", f"{state} (one race)", g, pool))
    return out


# ------------------------------------------------------------------ #
#  one state
# ------------------------------------------------------------------ #

def _old(cur, state, year):
    cur.execute("""SELECT division, gender, fingerprint FROM state_odds_meta
                   WHERE state = %s AND year = %s""", (state, year))
    return {(r["division"], r["gender"]): r["fingerprint"] for r in cur.fetchall()}


def buildState(cur, state, year, force=False, dry=False, log=print):
    """Simulate and write one state. Returns (built, skipped)."""
    jobs = plan(cur, state, year)
    old = {} if (dry or force) else _old(cur, state, year)
    data = None
    fields, units = {}, {}
    for div, slug, label, g, pool in jobs:
        fields[(slug, g)] = P._fieldUncached(cur, pool, year, state, div)
    ids = sorted({f["person_id"] for fl in fields.values() for f in fl})
    sig = _sigmas(cur, ids) if ids else {}
    schools = {f["school"] for fl in fields.values() for f in fl if f.get("school")}
    units = _schoolUnits(cur, state, schools)
    data = _marks(cur, state)
    built = skipped = 0
    keep = set()
    for div, slug, label, g, pool in jobs:
        field = [dict(f, sigma=sig.get(f["person_id"], S.fallbackSigma()))
                 for f in fields[(slug, g)]]
        keep.add((slug, g))
        if not field:
            if not dry:
                cur.execute("""DELETE FROM state_odds_meta WHERE year = %s
                               AND state = %s AND division = %s AND gender = %s""",
                            (year, state, slug, g))
                _clearRows(cur, year, state, slug, g)
            continue
        rounds, why = S.roundsFor(field, data, g, slug, units)
        fp = S.fingerprint(field, rounds, S.DRAWS)
        if old.get((slug, g)) == fp:
            skipped += 1
            continue
        t0 = time.time()
        res = S.simulate(field, rounds, draws=S.DRAWS, seed=S.seedOf(fp))
        built += 1
        log(f"  {state} {slug} {g}: {res['mode']}, {len(field)} athletes, "
            f"{len(res['teams'])} teams, {len(res['no_path'])} without a route "
            f"[{time.time() - t0:.1f}s]" + (f" -- {why}" if why else ""))
        if dry:
            continue
        _write(cur, year, state, slug, label, g, field, rounds, why, res, fp)
    if not dry:
        # a division that is gone tonight leaves no stale odds behind
        for d, gg in [k for k in (old or _old(cur, state, year)) if k not in keep]:
            cur.execute("""DELETE FROM state_odds_meta WHERE year = %s
                           AND state = %s AND division = %s AND gender = %s""",
                        (year, state, d, gg))
            _clearRows(cur, year, state, d, gg)
    return built, skipped


def _clearRows(cur, year, state, slug, g):
    for t in ("state_odds_athlete", "state_odds_team"):
        cur.execute(f"""DELETE FROM {t} WHERE year = %s AND state = %s
                        AND division = %s AND gender = %s""",
                    (year, state, slug, g))


def _write(cur, year, state, slug, label, g, field, rounds, why, res, fp):
    import psycopg2.extras
    _clearRows(cur, year, state, slug, g)
    path_of = {}
    for r in rounds or []:
        for pid in r["members"]:
            path_of[pid] = r["key"]
    no_path = set(res["no_path"])
    by_pid = {f["person_id"]: f for f in field}
    rows = []
    for pid, f in by_pid.items():
        a = res["athletes"].get(pid)
        path = ("none" if pid in no_path else
                path_of.get(pid, "state") if rounds else "state")
        rows.append((year, state, slug, g, pid, f.get("name"), f.get("school"),
                     f.get("school_state"), f.get("grade"), f.get("rating"),
                     f.get("sigma"),
                     a["p_qualify"] if a else None,
                     a["p_top"] if a else None,
                     a["p_podium"] if a else None,
                     a["p_win"] if a else None,
                     a["place_mean"] if a else None, path))
    psycopg2.extras.execute_values(cur, """
        INSERT INTO state_odds_athlete
               (year, state, division, gender, person_id, name, school,
                school_state, grade, rating, sigma, p_qualify, p_top,
                p_podium, p_win, place_mean, path)
        VALUES %s""", rows, page_size=1000)
    school_state = {f.get("school"): f.get("school_state") for f in field}
    team_path = {}
    for f in field:
        p = path_of.get(f["person_id"])
        if p:
            team_path.setdefault(f.get("school"), p)
    trows = [(year, state, slug, g, s, school_state.get(s), t["n_runners"],
              t["p_qualify"], t["p_top3"], t["p_win"], t["score_mean"],
              team_path.get(s, "state" if not rounds else "none"))
             for s, t in res["teams"].items()]
    if trows:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO state_odds_team
                   (year, state, division, gender, school, school_state,
                    n_runners, p_qualify, p_top3, p_win, score_mean, path)
            VALUES %s""", trows, page_size=1000)
    rd_public = [{k: v for k, v in r.items() if k != "members"}
                 for r in rounds or []]
    cur.execute("""
        INSERT INTO state_odds_meta
               (year, state, division, gender, division_label, mode, rounds,
                why, draws, n_field, n_no_path, as_of, fingerprint, built_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (year, state, division, gender) DO UPDATE SET
            division_label = EXCLUDED.division_label, mode = EXCLUDED.mode,
            rounds = EXCLUDED.rounds, why = EXCLUDED.why,
            draws = EXCLUDED.draws, n_field = EXCLUDED.n_field,
            n_no_path = EXCLUDED.n_no_path, as_of = EXCLUDED.as_of,
            fingerprint = EXCLUDED.fingerprint, built_at = now()
    """, (year, state, slug, g, label, res["mode"], json.dumps(rd_public),
          why, res["draws"], len(field), len(res["no_path"]),
          S.asOf(f.get("last_race") for f in field), fp))


# ------------------------------------------------------------------ #
#  main
# ------------------------------------------------------------------ #

def _states(arg):
    from landing import US_STATES
    if arg:
        want = [s.strip().upper() for s in arg.split(",") if s.strip()]
        bad = [s for s in want if s not in US_STATES]
        if bad:
            raise SystemExit(f"unknown state(s): {', '.join(bad)}")
        return want
    return list(US_STATES)


def main():
    ap = argparse.ArgumentParser(description="Precompute the state odds.")
    ap.add_argument("--state", help="comma-separated codes; default every state")
    ap.add_argument("--force", action="store_true",
                    help="recompute even where the fingerprint is unchanged")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    import psycopg2.extras
    from database import getConn
    t_all = time.time()
    tot_b = tot_s = 0
    failed = []
    modes = {}
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.athlete_season') AS t")
            if cur.fetchone()["t"] is None:
                print("  athlete_season missing -- run the boards first.")
                return 0
            if not a.dry_run:
                cur.execute(DDL)
                conn.commit()
            year = P.currentYear(cur)
            print(f"  state odds, {year} season, {S.DRAWS:,} draws per division")
            for st in _states(a.state):
                t0 = time.time()
                try:
                    b, s = buildState(cur, st, year, force=a.force,
                                      dry=a.dry_run)
                    if a.dry_run:
                        conn.rollback()
                    else:
                        conn.commit()
                    tot_b += b
                    tot_s += s
                    if not a.dry_run:
                        cur.execute("""SELECT mode, count(*) AS n FROM state_odds_meta
                                       WHERE state = %s AND year = %s GROUP BY mode""",
                                    (st, year))
                        modes[st] = {r["mode"]: r["n"] for r in cur.fetchall()}
                    print(f"  {st}: {b} built, {s} unchanged [{time.time() - t0:.0f}s]")
                except Exception as exc:                # noqa: BLE001
                    conn.rollback()
                    failed.append(st)
                    print(f"  {st}: FAILED {type(exc).__name__}: {exc}")
    q = sorted(st for st, m in modes.items() if m.get(S.MODE_QUAL))
    print(f"  qualifying rounds simulated in: {', '.join(q) if q else 'none'}")
    print(f"  done: {tot_b} built, {tot_s} unchanged, "
          f"{len(failed)} failed [{time.time() - t_all:.0f}s]")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
