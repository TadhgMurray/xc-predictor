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
#               of their races (the division titles label only some).
#
#     python scripts/run_checklist.py          # exit 0 = all PASS/SKIP
#
# Runs LAST in the pipeline (step 17), so a FAIL is loud in the morning
# log but cannot stop any build work -- everything else already ran.
# Read-only.

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
    if n_up:
        _mark("FAIL", "downward-only",
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
        _mark("FAIL" if n_ranked else "PASS", "corrected never ranked",
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
            _mark("FAIL" if n_drk else "PASS", "dropped never ranked",
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
    cur.execute("""
        SELECT count(*) FROM _chk_restored c
        WHERE NOT EXISTS (SELECT 1 FROM dist_override d
                          WHERE d.meet_id = c.meet_id
                            AND d.div_id = c.div_id)""")
    missing = cur.fetchone()[0]
    _mark("FAIL" if missing else "PASS", "restored corrections",
          f"{missing:,} of {len(keys):,} restored overrides MISSING from "
          "dist_override" if missing else
          f"all {len(keys):,} restored overrides present in dist_override")


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
    bad = []
    for t in ("results", "results_tf"):
        cur.execute(f"""
            SELECT count(*) FROM {t}
            WHERE {t}.speed_rating IS NOT NULL
              AND (EXISTS (SELECT 1 FROM _ck_wcp w
                           WHERE w.person_id IS NOT NULL
                             AND w.person_id = {t}.person_id)
                OR ({t}.person_id IS NULL AND EXISTS (
                     SELECT 1 FROM _ck_wcp w
                     WHERE w.person_id IS NULL AND w.source = {t}.source
                       AND w.athlete_id = {t}.athlete_id)))""")
        n = cur.fetchone()[0]
        if n:
            bad.append(f"{t}: {n:,}")
    _mark("FAIL" if bad else "PASS", "wheelchair athletes",
          "rated rows survive for athletes who raced a wheelchair "
          "division: " + "; ".join(bad) if bad
          else "no rated row for any athlete who raced a wheelchair "
               "division")


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
        checkStale(cur)
    fails = [r for r in _results if r[0] == "FAIL"]
    warns = [r for r in _results if r[0] == "WARN"]
    print(f"\n  CHECKLIST {'FAIL' if fails else 'PASS'}: "
          f"{len(fails)} fail, {len(warns)} warn, "
          f"{len(_results) - len(fails) - len(warns)} pass/skip\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
