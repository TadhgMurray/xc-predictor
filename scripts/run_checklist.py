# Project: xc-predictor / scripts
# File:    run_checklist.py
# Purpose: The owner's go/no-go checklist, verified against the LIVE DB
#          after every pipeline run. Four conditions (2026-08-27):
#
#            1. Result rows are not overturned willy-nilly -- no
#               auto-generated drop block past the rail, no unread
#               OVER-CAP files silently ignored.
#            2. Corrections apply correctly and only ever tune distance
#               DOWNWARD; corrected and dropped divisions never rank.
#            3. The canary athletes look right (scripts/canaries.json --
#               e.g. the 9:01 tops that career, the Tufts runner is rated
#               again after 2021).
#            4. No wheelchair division carries a rating anywhere, and
#               no athlete who ever raced one carries a rating on ANY
#               of their races (the division titles label only some) --
#               AND the exclusion list they are checked against is itself
#               current (4a-ii, 2026-09-08: checks 4 and 4a read the same
#               list the engine reads, so a list that has gone stale
#               passes them both while chair athletes rank).
#
#     python scripts/run_checklist.py          # exit 0 = all PASS/SKIP
#
# Runs LAST in the pipeline (step 17), so a FAIL is loud in the morning
# log but cannot stop any build work -- everything else already ran.
# Read-only (the one temp table it makes is session-scoped).

import json
import os
import re
import sys
from datetime import date as _date

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn                          # noqa: E402

_CORR = os.path.join("engine", "corrections.py")
_CANARIES = os.path.join("scripts", "canaries.json")
_DROP_CAP = 2000                 # triage_suspects._MAX_DROPS, restated
_WHEEL_RX = "wheelchair|seated|ambulator"

_results = []                    # (status, name, detail)


def _mark(status, name, detail):
    _results.append((status, name, detail))
    print(f"  [{status}] {name}: {detail}")


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


# ---- 1. no willy-nilly overturns -------------------------------------- #
def checkDrops():
    src = open(_CORR, encoding="utf-8").read()
    today = _date.today().isoformat()
    bad = []
    for m in re.finditer(
            r"=== triage-applied (result_drop\S*?)\.py md5=\w+ "
            rf"\({today}\) ===", src):
        nxt = src.find("=== ", m.end())
        body = src[m.end():nxt if nxt > 0 else len(src)]
        n = len(re.findall(r"^\s*-?\d+,", body, re.M))
        if n > _DROP_CAP:
            bad.append((m.group(1), n))
    if bad:
        _mark("FAIL", "row overturns",
              f"drop block(s) past the {_DROP_CAP:,} rail applied TODAY: "
              + ", ".join(f"{f} ({n:,})" for f, n in bad))
    else:
        _mark("PASS", "row overturns",
              f"no drop block past the {_DROP_CAP:,} rail applied today")
    caps = [f for f in os.listdir("scripts") if ".OVER-CAP" in f]
    if caps:
        _mark("WARN", "row overturns",
              f"{len(caps)} OVER-CAP file(s) awaiting a human: "
              + ", ".join(caps[:4]))


# ---- 2. corrections downward-only, applied, never ranked -------------- #
def checkCorrections(cur):
    if not _exists(cur, "dist_override"):
        _mark("FAIL", "corrections", "dist_override table missing -- "
              "dump_overrides has not run")
        return
    cur.execute("SELECT count(*) FROM dist_override")
    n_ov = cur.fetchone()[0]
    # downward-only, judged against the scraped anet distance where known.
    # ! JOIN ON THE PAIR: dist_override holds tfrrs overrides too, and
    #   tfrrs div_ids are per-meet-local small ints -- a div_id-only join
    #   would cross-match them against unrelated anet divisions. The pair
    #   silently excludes tfrrs rows (their scraped distance lives in the
    #   blob, and the backfill clamp guards them at apply time anyway).
    cur.execute("""
        SELECT count(*) FROM dist_override d
        JOIN meets m ON m.meet_id = d.meet_id AND m.div_id = d.div_id
        WHERE m.distance IS NOT NULL AND d.distance > m.distance + 1""")
    n_up = cur.fetchone()[0]
    # ! WARN, NOT FAIL (owner, 2026-09-07: "doesn't matter"): the rowguard
    #   and the restored corrections raise distances on purpose where the
    #   scrape was short; the check predates them and would fail every run.
    if n_up:
        _mark("WARN", "downward-only",
              f"{n_up} override(s) RAISE distance vs the scraped value")
    else:
        _mark("PASS", "downward-only",
              f"{n_ov:,} overrides, none raises a known scraped distance")
    # corrected divisions never rank
    if _exists(cur, "ranking_results"):
        cur.execute("""
            SELECT count(*) FROM ranking_results rr
            JOIN results r ON r.result_id = rr.result_id
            JOIN dist_override d
              ON d.meet_id = r.meet_id AND d.div_id = r.div_id
            WHERE rr.sport = 'XC'""")
        n_ranked = cur.fetchone()[0]
        # ! WARN (owner, 2026-09-07): a corrected division is rated on its
        #   corrected distance and ranks; the old rule is gone.
        _mark("WARN" if n_ranked else "PASS", "corrected never ranked",
              f"{n_ranked:,} board rows sit in corrected divisions"
              if n_ranked else "no board row sits in a corrected division")
        if _exists(cur, "dist_drop"):
            cur.execute("""
                SELECT count(*) FROM ranking_results rr
                JOIN results r ON r.result_id = rr.result_id
                JOIN dist_drop d
                  ON d.sport = rr.sport AND d.meet_id = r.meet_id
                 AND d.div_id = r.div_id""")
            n_drk = cur.fetchone()[0]
            _mark("WARN" if n_drk else "PASS", "dropped never ranked",
                  f"{n_drk:,} board rows sit in dropped divisions"
                  if n_drk else "no board row sits in a dropped division")
    else:
        _mark("SKIP", "corrected never ranked", "ranking_results absent")


# ---- 2b. the RESTORED old corrections actually reach the DB ----------- #
#   restore_old_overrides seeds _DISTANCE_RESTORED_XC fill-only; a wipe or
#   precedence bug could silently shed them. Every restored key must be in
#   dist_override unless a later drop or hand override superseded it.
def checkRestored(cur):
    import corrections
    restored = getattr(corrections, "_DISTANCE_RESTORED_XC", {})
    if not restored:
        _mark("SKIP", "restored corrections",
              "no _DISTANCE_RESTORED_XC block in corrections.py")
        return
    dropped = corrections._DISTANCE_DROP_BY_SPORT.get("XC", set())
    keys = [k for k in restored if k not in dropped]
    if not keys:
        _mark("SKIP", "restored corrections",
              "every restored key superseded by a drop")
        return
    cur.execute("CREATE TEMP TABLE _chk_restored "
                "(meet_id bigint, div_id bigint)")
    args = ",".join(cur.mogrify("(%s,%s)", k).decode() for k in keys)
    cur.execute("INSERT INTO _chk_restored VALUES " + args)
    # ★ A VETO IS A SUPERSEDING DECISION, NOT A LOSS (2026-10-29: "115 of
    #   4,978 MISSING"). wrong_distance's distance_pin vetoes a dict entry
    #   whose distance the times contradict, and dump_overrides keeps it in
    #   dist_override_vetoed for the next run to judge again -- the same
    #   standing as a drop above. Only a key in neither table is lost.
    vetoed_sql = ("AND NOT EXISTS (SELECT 1 FROM dist_override_vetoed v "
                  "WHERE v.meet_id = c.meet_id AND v.div_id = c.div_id)"
                  if _exists(cur, "dist_override_vetoed") else "")
    cur.execute(f"""
        SELECT count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM dist_override d
                   WHERE d.meet_id = c.meet_id AND d.div_id = c.div_id) {vetoed_sql}),
               count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM dist_override d
                   WHERE d.meet_id = c.meet_id AND d.div_id = c.div_id))
        FROM _chk_restored c""")
    missing, absent = cur.fetchone()
    vetoed = absent - missing
    note = f" ({vetoed:,} more vetoed by distance_pin)" if vetoed else ""
    _mark("FAIL" if missing else "PASS", "restored corrections",
          f"{missing:,} of {len(keys):,} restored overrides MISSING from "
          f"dist_override{note}" if missing else
          f"all {len(keys) - vetoed:,} restored overrides present in dist_override{note}")


# ---- 3. canaries ------------------------------------------------------ #
def _career(cur, pid):
    rows = []
    for t in ("results", "results_tf"):
        if not _exists(cur, t):
            continue
        cur.execute(f"""
            SELECT date, speed_rating FROM {t}
            WHERE COALESCE(person_id, athlete_id) = %s
              AND speed_rating IS NOT NULL""", (pid,))
        rows += cur.fetchall()
    return rows


def checkCanaries(cur):
    try:
        with open(_CANARIES, encoding="utf-8") as f:
            canaries = json.load(f).get("canaries", [])
    except (OSError, ValueError) as e:
        _mark("WARN", "canaries", f"cannot read {_CANARIES}: {e}")
        return
    for c in canaries:
        pid, label = c.get("person_id"), c.get("label", "?")
        if not pid:
            continue
        rows = _career(cur, pid)
        since = c.get("rated_since")
        if since is not None:
            n = sum(1 for d, _ in rows if d and str(d) >= since)
            _mark("PASS" if n else "FAIL", f"canary {label}",
                  f"{n} rated row(s) on/after {since}" if n
                  else f"NO rated rows on/after {since}")
        top = c.get("top_race_date")
        if top is not None:
            if not rows:
                _mark("FAIL", f"canary {label}", "no rated rows at all")
                continue
            best = max(rows, key=lambda r: r[1])
            ok = str(best[0]).startswith(top)
            _mark("PASS" if ok else "FAIL", f"canary {label}",
                  f"top-rated race is {best[0]} ({best[1]:.1f})"
                  + ("" if ok else f" -- expected {top}"))


# ---- 4. wheelchair ---------------------------------------------------- #
def checkWheelchair(cur):
    cur.execute(f"""
        SELECT count(*) FROM results r
        JOIN meets m ON m.div_id = r.div_id
        WHERE m.division ~* '{_WHEEL_RX}'
          AND r.speed_rating IS NOT NULL""")
    n = cur.fetchone()[0]
    # the TF seam too -- the 2026-08-27 stale rows were mostly here
    cur.execute(f"""
        SELECT count(*) FROM results_tf
        WHERE event_short ~* '{_WHEEL_RX}'
          AND speed_rating IS NOT NULL""")
    n_tf = cur.fetchone()[0]
    _mark("FAIL" if n or n_tf else "PASS", "wheelchair",
          f"{n:,} XC + {n_tf:,} TF rated rows in wheelchair "
          "divisions/events" if n or n_tf
          else "no rated row in any wheelchair division or event")


# ---- 4a. and NOT ONE RACE of a wheelchair athlete -------------------- #
#   The labels are incomplete: a chair athlete's unlabelled races were
#   priced on the running scale and rated absurdly high. One labelled
#   race withholds every race they ran (backfill _loadWheelchairPeople),
#   and this proves the propagation actually reached the whole career.
def checkWheelchairPeople(cur):
    cur.execute(f"""
        CREATE TEMP TABLE _ck_wcp AS
        WITH wc AS (
            SELECT r.person_id, r.athlete_id, r.source
            FROM   results r JOIN meets m ON m.div_id = r.div_id
                          AND m.meet_id = r.meet_id
                          AND m.source = r.source
            WHERE  m.division ~* '{_WHEEL_RX}'
            UNION ALL
            SELECT r.person_id, r.athlete_id, r.source
            FROM   results_tf r
            WHERE  r.event_short ~* '{_WHEEL_RX}')
        SELECT DISTINCT person_id, athlete_id, source FROM wc""")
    # ★ AND THE LIST THE ENGINE ACTUALLY CONSULTS (2026-09-03). The labels
    #   above are the narrow rule; wheelchair_person (engine/wheelchair_flag,
    #   step 04b) carries the wider one -- para words, the T/F class codes,
    #   the tfrrs division titles -- and it is by that list that the pack
    #   refused rows. A check on the labels alone passed while fill_ratings
    #   was pricing every athlete the wider rule had excluded.
    n_wcp = 0
    if _exists(cur, "wheelchair_person"):
        cur.execute("""
            INSERT INTO _ck_wcp (person_id, athlete_id, source)
            SELECT w.person_id, NULL, NULL FROM wheelchair_person w
            WHERE  NOT EXISTS (SELECT 1 FROM _ck_wcp c
                               WHERE c.person_id = w.person_id)""")
        n_wcp = cur.rowcount
    # ★ TWO LOOKUPS FROM THE SHORT LIST, NOT A PROBE OF EVERY ROW (2026-11-10;
    #   run 20261109_033652: this check took 845 s of 17_checklist). The rows
    #   wanted were "rated, AND (the person is listed OR, with no person, the
    #   feed's athlete is)" -- an OR of two EXISTS, which Postgres runs as a
    #   probe of all ~211M result rows, on one core (the list is a TEMP
    #   table). The two halves cannot both hold for one row (a person, or
    #   none), so the rows are exactly the two halves added: each a join from
    #   the de-duplicated list through the person_id / athlete_id index. The
    #   same rows, the same count, the same five shown.
    cur.execute("ANALYZE _ck_wcp")
    bad, shown = [], []
    for t in ("results", "results_tf"):
        rows = f"""
            SELECT r.result_id, r.person_id, r.source, r.meet_id, r.speed_rating
            FROM   (SELECT DISTINCT person_id FROM _ck_wcp WHERE person_id IS NOT NULL) w
            JOIN   {t} r ON r.person_id = w.person_id
            WHERE  r.speed_rating IS NOT NULL
            UNION ALL
            SELECT r.result_id, r.person_id, r.source, r.meet_id, r.speed_rating
            FROM   (SELECT DISTINCT source, athlete_id FROM _ck_wcp WHERE person_id IS NULL) w
            JOIN   {t} r ON r.source = w.source AND r.athlete_id = w.athlete_id
            WHERE  r.person_id IS NULL AND r.speed_rating IS NOT NULL"""
        cur.execute(f"SELECT count(*) FROM ({rows}) x")
        n = cur.fetchone()[0]
        if n:
            bad.append(f"{t}: {n:,}")
            # ★ NAME THE ROWS (2026-10-19: "results: 1; results_tf: 1" for
            #   days, and nothing to act on without a database session).
            #   Enough to open the athlete page and the race, and to see
            #   whether the row came back through a merge or a pin.
            cur.execute(f"""
                SELECT result_id, person_id, source, meet_id, speed_rating
                FROM ({rows}) x
                ORDER BY result_id LIMIT 5""")
            shown += [f"{t} result {rid} person {pid} ({src} meet {mid}) "
                      f"rated {float(sr):.1f}" for rid, pid, src, mid, sr in cur.fetchall()]
    via = (f" (labels + {n_wcp:,} more from wheelchair_person)" if n_wcp
           else " (labels only; wheelchair_person absent or adds nobody)")
    _mark("FAIL" if bad else "PASS", "wheelchair athletes",
          ("rated rows survive for athletes who raced a wheelchair "
           "division: " + "; ".join(bad) if bad
           else "no rated row for any athlete who raced a wheelchair "
                "division") + via)
    for line in shown:
        print(f"      {line}")


# ---- 4a-ii. IS THE LIST ITSELF CURRENT? (2026-09-08) ------------------ #
#   ★ CHECK 4a IS TAUTOLOGICAL AND THAT IS WHY IT KEEPS PASSING. It asks
#     "does anyone on the exclusion list carry a rating?", and the engine
#     builds its refusals from that same list -- so it can only catch a
#     PROPAGATION failure (the list was consulted and the rows survived
#     anyway). It cannot catch a DETECTION failure: a chair athlete the list
#     has never heard of is rated by the engine and passed by the check,
#     identically, for the same reason.
#
#   ⚠ AND DETECTION IS THE FAILURE THAT ACTUALLY HAPPENS. wheelchair_person
#     is a DROP-and-CREATE snapshot built only by engine/wheelchair_flag.py
#     --write, pipeline step 04b. Every run that omits that step -- it did
#     not exist before 2026-09-01, and the standard `--from 8` recipe put it
#     on --skip after that -- leaves the snapshot describing an older
#     corpus, and every chair athlete ingested since is priced by
#     fill_ratings and ranked on the boards. Twice now the owner has found
#     them there while the checklist said PASS.
#
#   So: apply the engine's OWN rule to the corpus as it stands right now,
#   and compare against what the table stored. A person the live rule finds
#   and the table does not is the snapshot being out of date; one of those
#   carrying a rating is a chair athlete on the boards today.
_RESCAN_TIMEOUT_MS = 900_000            # 15 minutes; it is a scan of results

_FRESH_SQL = """
    CREATE TEMP TABLE _ck_fresh AS
    SELECT DISTINCT 'XC'::text AS sport, r.result_id, r.person_id
    FROM   results r
    LEFT   JOIN meets m  ON m.div_id  = r.div_id  AND r.source = 'anet'
    LEFT   JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id AND r.source = 'tfrrs'
    WHERE  r.person_id IS NOT NULL
      AND (COALESCE(m.division, '') ~* %(rx)s
        OR COALESCE(mt.division_distances -> r.div_id::text ->> 'div_name',
                    '') ~* %(rx)s)
    UNION
    SELECT DISTINCT 'TF', r.result_id, r.person_id
    FROM   results_tf r
    WHERE  r.person_id IS NOT NULL
      AND  COALESCE(r.event_short, '') ~* %(rx)s
    UNION
    SELECT DISTINCT 'TF', r.result_id, r.person_id
    FROM   results_tf r
    JOIN   (SELECT DISTINCT meet_id, div_id, source FROM meets_tf
             WHERE division ~* %(rx)s) d
           ON d.meet_id = r.meet_id AND d.div_id = r.div_id AND d.source = r.source
    WHERE  r.person_id IS NOT NULL
"""

# ★ THE ENGINE'S WHOLE RULE, NOT HALF OF IT (2026-11-10; run 20261108_155220
#   FAILED here on 126 "missing" athletes). Since 2026-11-02 wheelchair_flag
#   lists a PERSON only at MIN_CHAIR_RACES chair races or MIN_CHAIR_SHARE of
#   their races (Chris Barbato: one para race among 128 took his whole
#   career out); below that bar only the chair RACES are left out, in
#   wheelchair_result. This check still said "any chair race -> the person
#   must be listed", so every below-bar runner read as a stale list, and the
#   run failed on exactly the people the 11-02 change chose not to list.
#   Now: a person over the bar must be in wheelchair_person, and a chair race
#   of anyone else must be in wheelchair_result. Either one missing is the
#   list out of date; missing AND rated is a chair time on the boards.
#   (It also reads the anet track DIVISION, as step 04b does -- the
#   Kohen Grantom shape, which the old rescan never looked at.)
_FRESH_PEOPLE_SQL = """
    CREATE TEMP TABLE _ck_people AS
    WITH c AS (SELECT person_id, count(*) AS n_chair FROM _ck_fresh GROUP BY 1),
    t AS (SELECT person_id, count(*) AS n_total FROM (
            SELECT person_id FROM results
             WHERE person_id IN (SELECT person_id FROM c)
            UNION ALL
            SELECT person_id FROM results_tf
             WHERE person_id IN (SELECT person_id FROM c)) x GROUP BY 1)
    SELECT c.person_id,
           (c.n_chair >= %(min_races)s
            OR c.n_chair::float / GREATEST(COALESCE(t.n_total, c.n_chair), 1) >= %(min_share)s)
             AS over_bar
    FROM c LEFT JOIN t ON t.person_id = c.person_id
"""


def checkWheelchairFresh(cur):
    name = "wheelchair list is current"
    if not _exists(cur, "wheelchair_person"):
        _mark("FAIL", name,
              "wheelchair_person does not exist -- nothing excludes chair "
              "athletes by person. Run: python engine/wheelchair_flag.py --write")
        return
    try:
        from wheelchair_flag import WHEELCHAIR_RX, MIN_CHAIR_RACES, MIN_CHAIR_SHARE
    except Exception as exc:                               # noqa: BLE001
        _mark("WARN", name, f"could not import the engine's rule: {exc}")
        return
    has_wr = _exists(cur, "wheelchair_result")

    # ! BOUNDED. This is the same scan step 04b does, and the checklist runs
    #   at the end of a long night; a timeout is a WARN, never a PASS --
    #   "we did not look" and "we looked and it was clean" are not the same.
    try:
        cur.execute(f"SET LOCAL statement_timeout = {_RESCAN_TIMEOUT_MS}")
        cur.execute(_FRESH_SQL, {"rx": WHEELCHAIR_RX})
        cur.execute(_FRESH_PEOPLE_SQL, {"min_races": int(MIN_CHAIR_RACES),
                                        "min_share": float(MIN_CHAIR_SHARE)})
    except Exception as exc:                               # noqa: BLE001
        cur.connection.rollback()
        _mark("WARN", name,
              f"the rescan did not finish ({str(exc).strip().splitlines()[0]}); "
              "the list's freshness is UNKNOWN. Run step 04b "
              "(python engine/wheelchair_flag.py --write) and re-check")
        return
    cur.execute("SET LOCAL statement_timeout = 0")

    # people over the bar the list does not hold
    cur.execute("""
        CREATE TEMP TABLE _ck_missing_people AS
        SELECT p.person_id FROM _ck_people p
        WHERE  p.over_bar
          AND  NOT EXISTS (SELECT 1 FROM wheelchair_person w
                           WHERE w.person_id = p.person_id)""")
    cur.execute("SELECT count(*) FROM _ck_missing_people")
    n_people = cur.fetchone()[0]
    # chair races of everyone else, not left out one by one
    wr = ("AND NOT EXISTS (SELECT 1 FROM wheelchair_result x "
          "WHERE x.sport = f.sport AND x.result_id = f.result_id)") if has_wr else ""
    cur.execute(f"""
        CREATE TEMP TABLE _ck_missing_races AS
        SELECT f.sport, f.result_id FROM _ck_fresh f
        WHERE  NOT EXISTS (SELECT 1 FROM wheelchair_person w
                           WHERE w.person_id = f.person_id) {wr}""")
    cur.execute("SELECT count(*) FROM _ck_missing_races")
    n_races = cur.fetchone()[0]
    if not n_people and not n_races:
        _mark("PASS", name,
              "the engine's rule finds no listed-worthy athlete and no chair "
              "race the lists leave rated")
        return

    rated = []
    for t, sp in (("results", "XC"), ("results_tf", "TF")):
        cur.execute(f"""
            SELECT count(*) FROM {t} r
            WHERE  r.speed_rating IS NOT NULL
              AND (EXISTS (SELECT 1 FROM _ck_missing_people m
                           WHERE m.person_id = r.person_id)
                OR EXISTS (SELECT 1 FROM _ck_missing_races m
                           WHERE m.sport = %s AND m.result_id = r.result_id))""", (sp,))
        n = cur.fetchone()[0]
        if n:
            rated.append(f"{t}: {n:,}")
    what = (f"{n_people:,} athlete(s) over the bar not in wheelchair_person, "
            f"{n_races:,} chair race(s) of others not in wheelchair_result")
    fix = ("Rebuild it: python engine/wheelchair_flag.py --write "
           "(pipeline step 04b), then rerun 09b_fill and the boards.")
    if rated:
        _mark("FAIL", name, f"the chair lists are STALE -- {what}, and they carry "
                            f"ratings right now ({'; '.join(rated)}). " + fix)
    else:
        _mark("WARN", name, f"the chair lists are STALE: {what}. None is rated, "
                            f"so no board is wrong today. " + fix)


# ---- 4b. the stale-rating invariant (2026-08-27 postmortem) ----------- #
#   The backfill NULLs normalized_time for skipped rows but never touched
#   speed_rating; every nuked row kept its pre-nuke rating. fill_ratings
#   scrubs this each run -- this check proves the invariant held.
def checkStale(cur):
    bad = []
    for t in ("results", "results_tf"):
        cur.execute(f"SELECT count(*) FROM {t} "
                    f"WHERE normalized_time IS NULL "
                    f"AND speed_rating IS NOT NULL")
        n = cur.fetchone()[0]
        if n:
            bad.append(f"{t}: {n:,}")
    _mark("FAIL" if bad else "PASS", "no-nt-no-rating invariant",
          "STALE ratings survive (rating with no normalized_time): "
          + "; ".join(bad) if bad
          else "every rating has a normalized_time behind it")


def main():
    print("\n  RUN CHECKLIST (owner's go/no-go, 2026-08-27)\n")
    checkDrops()
    with getConn() as conn, conn.cursor() as cur:
        checkCorrections(cur)
        checkRestored(cur)
        checkCanaries(cur)
        checkWheelchair(cur)
        checkWheelchairPeople(cur)
        checkWheelchairFresh(cur)
        checkStale(cur)
    fails = [r for r in _results if r[0] == "FAIL"]
    warns = [r for r in _results if r[0] == "WARN"]
    print(f"\n  CHECKLIST {'FAIL' if fails else 'PASS'}: "
          f"{len(fails)} fail, {len(warns)} warn, "
          f"{len(_results) - len(fails) - len(warns)} pass/skip\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
