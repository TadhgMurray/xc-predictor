#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/diagnose_link_quality_fast.py   (supersedes diagnose_link_quality.py)
# Purpose: Same diagnosis -- is a low-confidence athlete link a real person or a
#          name+time coincidence, judged by whether the two sides' SCHOOLS agree --
#          but BATCHED so it runs in seconds instead of minutes.
#
#          WHY THE OLD ONE WAS SLOW: it ran two queries PER sampled link, and the
#          tfrrs lookup filtered native_id, which is UNindexed on results (XC) ->
#          a 34M-row seq scan per link, thousands of times. This version collects
#          every sampled id first and fetches all schools in ONE query per side
#          (one scan, not thousands), then scores in Python.
#
#          Same reading: high conf-1 school agreement -> conf-1 links are real and
#          a lower gate is defensible; low -> keep gate 5 and merge conf>=3. The
#          gradient across buckets is the gate curve. Read-only.

import argparse
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, "scripts")
from database import getConn

RESULTS = {"TF": "results_tf", "XC": "results"}
SCHOOL_SIM = 0.60
SAMPLE_PER_BUCKET = 400


# ================================================================== #
# CHUNK 1 -- DB ACCESS + SCHOOL SIMILARITY
# ================================================================== #


def _query(sql, params=()):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _normSchool(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[.,'&()-]", " ", s)
    s = re.sub(r"\b(university|univ|college|of|the|high|school|hs|academy|state)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _schoolsAgree(a, b):
    na, nb = _normSchool(a), _normSchool(b)
    if not na or not nb:
        return None                       # unknown -- a side has no school
    if na == nb or na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= SCHOOL_SIM


# ================================================================== #
# CHUNK 2 -- SAMPLE THE BUCKETS (small, indexed queries on entity_links)
# ================================================================== #


# _sampleAllBuckets
# Purpose: Sample up to N athlete links in each confidence bucket for one sport.
# Output:  list of (bucket_label, anet_id, tfrrs_id).
def _sampleAllBuckets(sport, n):
    buckets = [("conf 1", "confidence = 1"), ("conf 2", "confidence = 2"),
               ("conf 3", "confidence = 3"), ("conf 4", "confidence = 4"),
               ("conf 5+", "confidence >= 5")]
    out = []
    for label, cond in buckets:
        rows = _query(
            f"""
            SELECT anet_id, tfrrs_id FROM entity_links
            WHERE entity_type='athlete' AND sport=%s AND {cond}
            LIMIT %s
            """,
            (sport, n),
        )
        for aid, tid in rows:
            out.append((label, aid, tid))
    return out


# ================================================================== #
# CHUNK 3 -- BATCH SCHOOL LOOKUP (one query per side, modal school per id)
# ================================================================== #


# _schoolMap
# Purpose: {id: modal_school} for a set of ids on one source, in ONE query. Modal
#          = the school appearing on the most of that id's result rows (row_number
#          over a per-id count, keep rank 1).
# Arguments: table, source ('anet'/'tfrrs'), id_col ('athlete_id'/'native_id'), ids.
# Output:  dict {id: school}.
def _schoolMap(table, source, id_col, ids):
    if not ids:
        return {}
    rows = _query(
        f"""
        SELECT id, school FROM (
            SELECT {id_col} AS id, school,
                   row_number() OVER (PARTITION BY {id_col} ORDER BY count(*) DESC) AS rn
            FROM {table}
            WHERE source=%s AND {id_col} = ANY(%s)
              AND school IS NOT NULL AND school <> ''
            GROUP BY {id_col}, school
        ) s WHERE rn = 1
        """,
        (source, list(ids)),
    )
    return {i: sch for i, sch in rows}


# ================================================================== #
# CHUNK 4 -- SCORE + REPORT
# ================================================================== #


def _pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


# _reportSport
# Purpose: For one sport: sample all buckets, batch-fetch both sides' schools once,
#          then score agreement per bucket and print the gradient.
def _reportSport(sport):
    table = RESULTS[sport]
    print(f"\n=== {sport}: school-agreement by confidence "
          f"(sample {SAMPLE_PER_BUCKET}/bucket) ===")

    sampled = _sampleAllBuckets(sport, SAMPLE_PER_BUCKET)
    if not sampled:
        print("  (no athlete links)")
        return

    # one batched fetch per side, over every id we sampled
    anet_ids  = {aid for _lbl, aid, _tid in sampled}
    tfrrs_ids = {tid for _lbl, _aid, tid in sampled}
    anet_school  = _schoolMap(table, "anet",  "athlete_id", anet_ids)
    tfrrs_school = _schoolMap(table, "tfrrs", "native_id",  tfrrs_ids)

    # tally per bucket
    order = ["conf 1", "conf 2", "conf 3", "conf 4", "conf 5+"]
    stats = {lbl: {"n": 0, "agree": 0, "disagree": 0, "unknown": 0,
                   "agree_ex": [], "disagree_ex": []} for lbl in order}
    for label, aid, tid in sampled:
        s = stats[label]
        s["n"] += 1
        sa, st = anet_school.get(aid), tfrrs_school.get(tid)
        verdict = _schoolsAgree(sa, st)
        if verdict is None:
            s["unknown"] += 1
        elif verdict:
            s["agree"] += 1
            if len(s["agree_ex"]) < 3:
                s["agree_ex"].append(f"{sa!r} == {st!r}")
        else:
            s["disagree"] += 1
            if len(s["disagree_ex"]) < 4:
                s["disagree_ex"].append(f"{sa!r} =/= {st!r}")

    for label in order:
        s = stats[label]
        if s["n"] == 0:
            print(f"  {label:<7}: (none)")
            continue
        known = s["agree"] + s["disagree"]
        print(f"  {label:<7}: n={s['n']:>4}  agree={_pct(s['agree'], known):5.1f}%  "
              f"(unknown school: {s['unknown']})")
        if label in ("conf 1", "conf 2"):
            for e in s["agree_ex"]:
                print(f"        agree    {e}")
            for e in s["disagree_ex"]:
                print(f"        DISAGREE {e}")


def _parseArgs():
    p = argparse.ArgumentParser(description="Diagnose low-confidence athlete link quality (fast).")
    p.add_argument("--sport", choices=["TF", "XC"], help="one sport (default: both).")
    return p.parse_args()


def main():
    args = _parseArgs()
    print("=== link quality via school agreement (read-only, batched) ===")
    print("high conf-1 agreement -> conf-1 real, lower gate OK; low -> keep gate 5, merge conf>=3.")
    for sport in ([args.sport] if args.sport else ["TF", "XC"]):
        _reportSport(sport)
    print("\n=== done. ===")


if __name__ == "__main__":
    main()