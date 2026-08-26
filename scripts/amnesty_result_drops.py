# Project: xc-predictor / scripts
# File:    amnesty_result_drops.py
# Purpose: Re-try the ~1.4M auto-generated _RESULT_DROP convictions with a
#          FAIR echo test, and pardon every row the athlete's own career
#          corroborates.
#
#     python scripts/amnesty_result_drops.py                 # report only
#     python scripts/amnesty_result_drops.py --canary 23947175
#     python scripts/amnesty_result_drops.py --write         # append pardons
#     python scripts/amnesty_result_drops.py --sport TF --write
#
# ★ THE WRONGFUL-CONVICTION MACHINE (found 2026-08-27, person 23947175).
#   The 2026-07 triage/diag passes filled _RESULT_DROP -- a container
#   documented as "hand-confirmed cooked individual rows" -- with ~1.4M
#   auto-generated ids. The condemnation rule ("swings too far from the
#   athlete's own norm") fired at swings as small as -13%, which is ordinary
#   teenage improvement. The exoneration rule required an ECHO: another race
#   by the same athlete within 5%, different meet, different day -- but the
#   echo join demanded `r.speed_rating > 0`, so only RATED races could be
#   alibis. Dropped rows never get a normalized_time, so each round's drops
#   erased the next round's alibis: the 07-16/17 blocks show the spiral
#   (280k, then 92k/35k/15k/8k/4.6k/2.7k/1.7k rounds, then 521k). A runner
#   whose rated history is his slow freshman self has every faster season
#   condemned forever -- and his tfrrs copies die too, as dedup twins of the
#   dropped anet rows.
#
# ★ THE FAIR RETRIAL. Rebuild the backfill's own row function with the drop
#   list DISABLED, so every affected row gets its would-be normalized time.
#   Then the echo test runs DROP-BLIND: an alibi is any same-person row with
#   a would-be nt within the window, at a different meet on a different day
#   (dedup twins never alibi -- same day). Two independent near-identical
#   races cannot both be flukes; a genuinely cooked one-off still has no
#   echo and STAYS dropped. Only ids from the auto-generated triage blocks
#   are retried -- the small hand-curated base set is never touched.
#
# --write appends a `difference_update` pardon block to engine/corrections.py
# (append-only, like the triage blocks it answers -- the history of the
# conviction and the pardon both stay readable). Re-runnable: already-pardoned
# ids are no longer in the live set, so they are not re-tried.

import argparse
import hashlib
import os
import re
import sys
from datetime import date as _date

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")
sys.path.insert(0, "backfill")

from database import getConn                          # noqa: E402
import psycopg2.extras                                # noqa: E402

_CORR = os.path.join("engine", "corrections.py")

ECHO_WINDOW = 0.05          # same window the original triage used
ECHOES_REQ = 1              # same corroboration bar the original triage used

# The triage/diag appliers all stamped this header shape. Anything inside one
# of these blocks is an auto-generated conviction and eligible for retrial.
_BLOCK_RX = re.compile(
    r"=== triage-applied (result_drop\S*?)\.py md5=\w+ \([\d-]+\) ===")


# ---- collect the auto-generated conviction ids, per sport, from the
#      corrections SOURCE (the block headers say which applier wrote them;
#      the filename's _xc/_tf suffix says which table they were verified
#      against). The LIVE set is consulted afterwards so prior pardons and
#      hand edits are respected.
def _triageIds(src_path):
    ids = {"XC": set(), "TF": set()}
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    pos = 0
    while True:
        m = _BLOCK_RX.search(src, pos)
        if not m:
            break
        nxt = _BLOCK_RX.search(src, m.end())
        body = src[m.end():nxt.start() if nxt else len(src)]
        sport = "TF" if m.group(1).endswith("_tf") else "XC"
        ids[sport].update(
            int(x) for x in re.findall(r"^\s*(-?\d+),", body, re.M))
        pos = m.end()
    return ids


def _liveDrops(sport):
    import corrections
    return set(corrections._RESULT_DROP_BY_SPORT[sport])


# ---- the drop-blind row function: the backfill's own classifier, with the
#      result-drop container emptied so convicted rows get their would-be nt.
#      Weather is patched off (it nudges values by fractions of the echo
#      window and its index is the slowest lookup to build).
def _bakeRowFn(conn, sport):
    import backfill_normalize as BF
    BF._weatherEnabled = lambda s: False
    BF._RESULT_DROP_BY_SPORT = {"XC": frozenset(), "TF": frozenset()}
    cfg = BF._configFor(sport)
    print("  building the backfill's lookups (the slow part) ...")
    lookups = BF._buildLookups(conn, cfg)
    return BF, cfg, BF._makeRowFn(cfg, *lookups)


# ---- one person's retrial. rows = [(rid, meet, date, nt|None)] for the
#      person's ENTIRE career, would-be nts included. A convicted row with no
#      would-be nt (sentinel time, wheelchair, no distance ...) cannot be
#      retried -- it stays dropped for its other reason.
def _judge(rows, convicted, window, required):
    pardons, islands, unvaluable = [], [], 0
    for rid, meet, day, nt in rows:
        if rid not in convicted:
            continue
        if nt is None:
            unvaluable += 1
            continue
        n = sum(1 for rid2, meet2, day2, nt2 in rows
                if nt2 is not None and rid2 != rid
                and meet2 != meet and day2 != day
                and abs(nt2 / nt - 1.0) <= window)
        (pardons if n >= required else islands).append(rid)
    return pardons, islands, unvaluable


def _writePardons(sport, pardons):
    ids = sorted(pardons)
    digest = hashlib.md5(
        ",".join(map(str, ids)).encode()).hexdigest()
    today = _date.today().isoformat()
    lines = [
        "",
        f"# === amnesty-applied result_drop_pardon_"
        f"{sport.lower()} md5={digest} ({today}) ===",
        "# Auto-generated by amnesty_result_drops.py -- career-corroborated",
        "# pardons. The 2026-07 triage counted only RATED races as alibis",
        "# (`r.speed_rating > 0`), so each round's drops erased the next",
        "# round's alibis. These ids were re-tried on would-be normalized",
        "# times computed with the drop list DISABLED: each has >= "
        f"{ECHOES_REQ} echo(es)",
        f"# within {ECHO_WINDOW:.0%} by the same person, different meet, "
        "different day.",
        "_RESULT_DROP_PARDON = {",
    ]
    lines += [f"    {i}," for i in ids]
    lines += [
        "}",
        f"_RESULT_DROP_{sport}.difference_update(_RESULT_DROP_PARDON)",
        "del _RESULT_DROP_PARDON",
        "",
    ]
    with open(_CORR, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n  WROTE {len(ids):,} pardons to {_CORR} (md5 {digest})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=("XC", "TF"), default="XC")
    ap.add_argument("--window", type=float, default=ECHO_WINDOW)
    ap.add_argument("--echoes", type=int, default=ECHOES_REQ)
    ap.add_argument("--canary", type=int, action="append", default=[],
                    help="person_id to print a full retrial for")
    ap.add_argument("--write", action="store_true",
                    help="append the pardon block to engine/corrections.py")
    args = ap.parse_args()

    convicted = _triageIds(_CORR)[args.sport] & _liveDrops(args.sport)
    print(f"\n  {args.sport}: {len(convicted):,} auto-generated convictions "
          "still in force (triage blocks ∩ live set)")
    if not convicted:
        print("  nothing to retry.")
        return

    with getConn() as conn:
        BF, cfg, row_fn = _bakeRowFn(conn, args.sport)
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _amnesty_ids "
                        "(result_id bigint PRIMARY KEY)")
            psycopg2.extras.execute_values(
                cur, "INSERT INTO _amnesty_ids VALUES %s "
                     "ON CONFLICT DO NOTHING",
                [(i,) for i in convicted], page_size=10_000)
            # every person who owns at least one convicted row; rows with no
            # person AND no athlete id have no career to vouch for them.
            cur.execute(f"""
                CREATE TEMP TABLE _amnesty_people AS
                SELECT DISTINCT COALESCE(r.person_id, r.athlete_id) AS ident
                FROM {cfg.table} r
                JOIN _amnesty_ids a USING (result_id)
                WHERE COALESCE(r.person_id, r.athlete_id) IS NOT NULL""")
            cur.execute("SELECT count(*) FROM _amnesty_people")
            n_people = cur.fetchone()[0]
            cur.execute(f"""
                SELECT count(*) FROM {cfg.table} r
                JOIN _amnesty_ids a USING (result_id)
                WHERE COALESCE(r.person_id, r.athlete_id) IS NULL""")
            n_orphan = cur.fetchone()[0]
        print(f"  {n_people:,} people own a convicted row "
              f"({n_orphan:,} convicted rows have no identity -> stay "
              "dropped)")

        # ★ STREAM THE AFFECTED CAREERS person-by-person. ORDER BY the
        #   identity makes each career contiguous, so memory holds ONE
        #   person's rows at a time no matter how many people are affected.
        stream = conn.cursor(name="amnesty_stream")
        stream.itersize = 20_000
        stream.execute(
            BF._streamSQL(cfg)
            + " JOIN _amnesty_people p"
            "   ON COALESCE(person_id, athlete_id) = p.ident"
            " ORDER BY p.ident")

        canaries = set(args.canary)
        pardons, n_islands, n_unval, n_done = [], 0, 0, 0
        cur_ident, career = None, []

        def close_person():
            nonlocal n_islands, n_unval, n_done
            if not career:
                return
            p, isl, unv = _judge(career, convicted,
                                 args.window, args.echoes)
            pardons.extend(p)
            n_islands += len(isl)
            n_unval += unv
            n_done += 1
            if cur_ident in canaries:
                print(f"\n  CANARY person {cur_ident}: "
                      f"{len(p)} pardoned, {len(isl)} still islands, "
                      f"{unv} unvaluable, career of {len(career)} rows")
                for rid, meet, day, nt in career:
                    mark = ("PARDON" if rid in p else
                            "island" if rid in isl else
                            "     -" if rid not in convicted else "no-nt")
                    nt_s = f"{nt:8.1f}" if nt is not None else "    none"
                    print(f"    {day}  meet {meet}  nt {nt_s}  {mark}")
            if n_done % 20_000 == 0:
                print(f"    ... {n_done:,}/{n_people:,} people, "
                      f"{len(pardons):,} pardons so far")

        for row in stream:
            ident = (row[BF._PERSON] if row[BF._PERSON] is not None
                     else row[BF._AID])
            if ident != cur_ident:
                close_person()
                cur_ident, career = ident, []
            rid, value, _reason, _trace = row_fn(row)
            career.append((rid, row[BF._MEET], row[BF._DATE], value))
        close_person()
        stream.close()

    print(f"\n  RETRIAL COMPLETE ({args.sport}): {len(convicted):,} "
          "convictions in force; verdicts on the rows found in "
          f"{cfg.table}:")
    print(f"    PARDONED  {len(pardons):,}  (career corroborates the race)")
    print(f"    ISLANDS   {n_islands:,}  (still no echo -- stay dropped)")
    print(f"    NO VALUE  {n_unval:,}  (skipped for another reason -- "
          "stay dropped)")
    print(f"    NO PERSON {n_orphan:,}  (no identity -- stay dropped)")

    if args.write:
        if pardons:
            _writePardons(args.sport, pardons)
            print("  Rerun the pipeline (-From 05) to rate the pardoned "
                  "rows.")
        else:
            print("\n  nothing to write.")
    else:
        print("\n  report only -- rerun with --write to append the pardon "
              "block.")


if __name__ == "__main__":
    main()
