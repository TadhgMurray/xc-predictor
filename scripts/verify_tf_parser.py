# Project: xc-predictor
# File:    scripts/verify_tf_parser.py
# Purpose: Prove that results_tf.normalized_time was written by the run that had
#          event_parse.py wired in -- not by an earlier, pre-parser run.
#
# WHY THIS IS NOT PARANOIA
# -----------------------
# resume_merge.py reported results_tf with 28,907,527 normalized rows and
# bf_staging_tf with exactly 28,907,527 rows. Those matching means the merge
# committed. It does NOT prove WHICH drain filled staging: the drain is
# deterministic, so a re-run produces the same count, and a merge that crashed
# after an earlier one succeeded leaves this exact fingerprint.
#
# The counts are consistent with the parser run. Consistency is not proof. The
# 880y episode was made of consistency.
#
# THE DIRECT TEST
# ---------------
# Before event_parse.py, EVENT_DISTANCES_TF was an exact-match dict of 12 anet
# short codes. Every tfrrs long name -- "Men's 800 Meters", "Women's Mile" --
# resolved to None and was skipped as `no_distance`. So:
#
#   * a tfrrs long-name row with a normalized_time  => the parser ran
#   * ALL tfrrs long-name rows NULL                 => the pre-parser run
#
# And three negative controls, which must stay NULL either way:
#   'sprintmed2248'  a medley relay's leg-sum. Non-null here = a fabricated value.
#   '4x400m'         a relay.
#   '100m'           below the 800m floor.
#
# SAFETY: read-only, rolls back immediately.
#
# USAGE
#   python scripts/verify_tf_parser.py

import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


TABLE = "results_tf"

# (label, event_short regex, expectation)
#   "SOME"  -> at least one row must carry a normalized_time
#   "NONE"  -> every row must be NULL; a value here is a fabrication
_CHECKS = [
    # POSITIVE: only reachable through event_parse.py
    ("tfrrs \"Men's 800 Meters\"",   r"^men's 800 meters$",  "SOME"),
    ("tfrrs \"Women's 5000 Meters\"", r"^women's 5000 meters$", "SOME"),
    ("tfrrs \"Men's Mile\"",         r"^men's mile$",        "SOME"),
    ("tfrrs \"Men's 1600 Yards\"",   r"^men's 1600 yards$",  "SOME"),
    ("anet '3ksteeple'",             r"^3ksteeple$",         "SOME"),
    ("anet '880y'",                  r"^880y$",              "SOME"),
    # NEGATIVE: must be NULL, parser or not. A value = a lie in the data.
    ("anet 'sprintmed2248'",         r"^sprintmed2248$",     "NONE"),
    ("anet 'distmed12,4,8,16'",      r"^distmed12,4,8,16$",  "NONE"),
    ("anet '4x400m'",                r"^4x400m$",            "NONE"),
    ("anet '100m'",                  r"^100m$",              "NONE"),
    ("tfrrs \"Men's 4 x 400 Relay\"", r"^men's 4 x 400 relay$", "NONE"),
]


# _coverage
# Purpose : for one event pattern, how many rows exist and how many carry a
#           normalized_time.
# Syntax  : `~*` is POSIX case-insensitive regex. Anchored ^...$ so
#           'sprintmed2248' cannot match 'sprintmed22485' and '100m' cannot
#           match '1100m'. `count(col)` counts NON-NULL values of col, unlike
#           count(*), which is exactly the distinction this whole script needs.
def _coverage(cur, pattern):
    cur.execute(f"""
        SELECT count(*), count(normalized_time)
        FROM {TABLE}
        WHERE event_short ~* %s
    """, (pattern,))
    return cur.fetchone()


# _judge
# Purpose : turn (rows, filled, expectation) into a pass/fail and a message.
# An event with ZERO rows is reported as NO DATA, never as a pass -- a check
#   that cannot fail is not a check.
def _judge(rows, filled, expect):
    if rows == 0:
        return None, "no rows match this pattern"
    if expect == "SOME":
        if filled > 0:
            return True, f"{filled:,} of {rows:,} normalized"
        return False, (f"0 of {rows:,} normalized -- this event is ONLY "
                       f"reachable through event_parse.py")
    if filled == 0:
        return True, f"0 of {rows:,} normalized (correctly rejected)"
    return False, (f"{filled:,} of {rows:,} normalized -- FABRICATED VALUES, "
                   f"this event is not a distance race")


def main():
    initPool()
    print("=" * 74)
    print(f"Did event_parse.py write {TABLE}.normalized_time?")
    print("=" * 74)

    with getConn() as conn:
        with conn.cursor() as cur:
            results = [(label, expect) + _coverage(cur, pat)
                       for label, pat, expect in _CHECKS]
        conn.rollback()          # release ACCESS SHARE immediately

    failures = 0
    no_data = 0
    print(f"\n{'expect':<8}{'check':<34}result")
    print("-" * 74)
    for label, expect, rows, filled in results:
        ok, msg = _judge(rows, filled, expect)
        if ok is None:
            tag, no_data = "  ??  ", no_data + 1
        elif ok:
            tag = " PASS "
        else:
            tag, failures = " FAIL ", failures + 1
        print(f"{expect:<8}{label:<34}[{tag}] {msg}")

    print("-" * 74)
    if failures:
        print(f"{failures} FAILURES. results_tf.normalized_time was NOT written")
        print("by the parser run, or contains fabricated values. Re-run:")
        print("  python scripts/verify_merge.py --table results_tf   (do NOT --drop)")
        sys.exit(1)
    print("ALL PASS. results_tf holds event_parse.py's output:")
    print("  * tfrrs long-name distance races ARE normalized (impossible before)")
    print("  * medley relays, relays, and sprints are NOT (no fabrications)")
    if no_data:
        print(f"\n{no_data} checks matched no rows and were not evaluated.")
    print("\nTF is done. Next:")
    print("  python scripts/verify_merge.py --table results_tf")
    print("  python scripts/verify_merge.py --table results_tf --drop")
    print("  python scripts/drop_dupe_indexes.py --table results_tf --rebuild")


if __name__ == "__main__":
    main()