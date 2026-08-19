# =====================================================================
# build_school_levels.py — ONE-TIME: cache school -> level, from anet grades
# =====================================================================
#
# WHY THIS EXISTS
# ---------------
# poolFor(grade, gender, source) never sees the school. For tfrrs, grade is
# always NULL, so it falls through to one line: "tfrrs -> college". But the
# evidence says 60% of tfrrs rows are YOUTH (hs+ms). Every one of them is
# currently judged against the COLLEGE raw-time floor (720s/5k) instead of the
# stricter youth floor (hs 780, ms 840-960) -- so the youth floor NEVER fires on
# a tfrrs row, and real elite college races sit at the hs_unknown_gender floor.
#
# THE FIX (conservative, no inference):
#   anet rows carry a REAL grade -> GRADE_TO_LEVEL gives ground truth.
#   tfrrs rows have grade=NULL but name the SAME schools.
#   So: learn each school's level from its anet grades, transfer to tfrrs.
#
# WHAT WE DELIBERATELY DO NOT DO (all four were tested and REFUTED)
#   * division majority voting   -- narrow divisions were worst in WELL-POPULATED
#                                   divisions, not thin ones
#   * meet-level pooling         -- flattens 22.6% genuinely multi-level meets,
#                                   and ate the ms pool (2,785 -> 876 divisions)
#   * div_name level parsing     -- split meets were LESS labelled (10.0%) than
#                                   decisive ones (12.7%); removing labelled divs
#                                   left 91.2% still split
#   * school-name morphology     -- reached only 10.4% of rows, and misfires on
#                                   schools named after universities
#
# So: NO VOTING, NO INFERENCE. A row's level comes from its OWN school, and only
# when that school is UNAMBIGUOUS. Everything else keeps today's exact behaviour.
#
# THREE THINGS ARE EXCLUDED FROM THE MAP (each earned by evidence):
#   1. ROSTER STATUSES ('unattached' etc). Not schools. `unattached` was the
#      single worst collided key at 42,128 tfrrs rows -- HS and college runners
#      both race unattached, so it can never carry a level.
#   2. COLLIDED SCHOOLS -- one name, two institutions (north central, penn,
#      trinity, wheaton...). Confirmed 5.0x enriched in contested divisions
#      (25.7% vs 5.1%). A collided key cannot speak honestly, so it is omitted
#      and its rows fall back to today's behaviour.
#   3. THIN EVIDENCE -- a school with too few graded anet rows to be sure.
#
# OUTPUT: engine/data/school_levels.pkl
#     {"levels": {norm_key: "ms"|"hs"|"college"}, "meta": {...}}
# Loaded once at import by normalize_distance (same pattern as the splines).
#
# RUN (once, and again whenever anet is re-scraped):
#     python scripts/build_school_levels.py
# =====================================================================

import os
import pickle
import re
from collections import Counter, defaultdict
import sys

sys.path.insert(0, "backfill")
sys.path.insert(0, "engine")

from database import getConn

# SINGLE SOURCE OF TRUTH for grade -> level. Do NOT re-declare it here: the real
# map includes grades 1-4 as 'ms', which a hand-copied 6-12 map would silently miss.
from normalize_distance import GRADE_TO_LEVEL


# ---------------------------------------------------------------------
# CHUNK 0 — where the artifact goes (mirrors normalize_distance's paths)
# ---------------------------------------------------------------------
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "engine", "data")
_OUT_FILE = os.path.join(_DATA_DIR, "school_levels.pkl")

# Only rows that are real finishes inform the map.
_VALID_ROW = """
      r.time_seconds BETWEEN 1 AND 999000
      AND r.place IS DISTINCT FROM 0
"""

# Roster statuses, not institutions. These can never carry a level.
_NON_SCHOOLS = {"unattached", "unattached runner", "unat", "independent",
                "individual", "alumni", "open", "none", "na", "n a", "guest"}

# --- tuning knobs, all in one place ---
_MIN_GRADED = 5       # graded anet rows before a school may claim a youth level
_MIN_OTHER = 5        # ungraded anet rows before a school may claim college
_COLLIDE_MIN_SIDE = 50   # each side of a collision must be this real
_COLLIDE_RATIO = 0.25    # ...and the smaller side at least this fraction of the bigger


# ---------------------------------------------------------------------
# CHUNK 1 — the canonical school key
# ---------------------------------------------------------------------
# Each expansion is a spelling convention OBSERVED in the data, not a guess:
# tfrrs abbreviates where anet spells out. Six of the top-25 unmatched schools
# were the same Wisconsin system.
#
# The subtle trap this ordering avoids: "St." means SAINT at the start of a name
# ("St. Louis") and STATE at the end ("Cortland St."), and either side may be
# spelled out. Canonicalising BOTH to the same key is what makes them meet.
#
# And the trailing "(Pa.)" is KEPT. It exists precisely because the bare name is
# ambiguous -- stripping it merged Trinity (Tex.) with Trinity (Conn.) and was
# measured to CREATE 161,907 rows of extra collision.
_ABBREV = [
    (re.compile(r"\bwis\.?\s*-\s*"),             "wisconsin-"),
    (re.compile(r"\bu\.\s*of\s+"),               "university of "),
    (re.compile(r"\bu\.\s*$"),                   " university"),
    (re.compile(r"\b(st\.?|state)\s*$"),         " state"),   # SUFFIX -> state
    (re.compile(r"^\s*(st\.?|saint)\s+"),        "st "),      # PREFIX -> saint
    (re.compile(r"\b(st\.?|saint)\s+(?=[a-z])"), "st "),
]
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")


# Purpose   : canonical key so tfrrs and anet spellings of one school meet.
# Arguments : school -- raw school string (may be None/empty).
# Output    : normalized key str, or None if this is not a school at all.
# Detail    : NEVER assigns a level; it only merges names. Returns None for
#             roster statuses so they can never enter the map.
def normSchool(school):
    if not school:
        return None
    t = school.strip().lower()
    m = _PAREN.search(t)
    suffix = ""
    if m:                                        # keep "(pa.)" as " pa"
        suffix = " " + re.sub(r"[^a-z0-9]+", "", m.group(0))
        t = _PAREN.sub("", t)
    for rx, repl in _ABBREV:
        t = rx.sub(repl, t)
    t = re.sub(r"[.'`]", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    key = (re.sub(r"\s+", " ", t) + suffix).strip()
    if not key or key in _NON_SCHOOLS:
        return None
    return key


# ---------------------------------------------------------------------
# CHUNK 2 — gather anet grade evidence per school
# ---------------------------------------------------------------------

# Purpose   : every anet school's grade profile, the ground truth for level.
# Arguments : conn -- pooled connection.
# Output    : dict {norm_key: Counter({"ms": n, "hs": n, "college": n, "other": n})}
# Detail    : grades map through GRADE_TO_LEVEL (imported, not copied), so
#             'Fr'/'So'/'Jr'/'Sr'/'RS' correctly count as COLLEGE evidence and
#             grades 1-4 count as ms. 'other' = a grade the map doesn't know
#             (blank, junk) and is NOT evidence of anything by itself.
def fetchSchoolGrades(conn):
    sql = f"""
        SELECT nullif(trim(r.school),'') AS school,
               trim(coalesce(r.grade,'')) AS grade,
               count(*) AS n
        FROM results r
        WHERE r.source = 'anet' AND {_VALID_ROW}
          AND nullif(trim(r.school),'') IS NOT NULL
        GROUP BY 1, 2
    """
    profiles = defaultdict(Counter)
    with conn.cursor() as cur:
        cur.execute(sql)
        for school, grade, n in cur:
            key = normSchool(school)
            if not key:
                continue
            level = GRADE_TO_LEVEL.get(grade)     # SSOT
            profiles[key][level or "other"] += n
    return profiles


# ---------------------------------------------------------------------
# CHUNK 3 — decide (or refuse to decide) one school's level
# ---------------------------------------------------------------------

# Purpose   : does this key carry BOTH real youth evidence AND real adult
#             evidence -- i.e. two institutions sharing one name?
# Arguments : counts -- the school's Counter.
# Output    : True if collided (must be OMITTED from the map).
# Detail    : measured, not guessed: collided keys are 5.0x enriched in the
#             divisions where school-level answers contradict each other.
def isCollided(counts):
    youth = counts["ms"] + counts["hs"]
    adult = counts["college"] + counts["other"]
    if youth < _COLLIDE_MIN_SIDE or adult < _COLLIDE_MIN_SIDE:
        return False
    small, big = sorted((youth, adult))
    return (small / big) >= _COLLIDE_RATIO


# Purpose   : the level for one school, or None to abstain.
# Arguments : counts -- the school's grade Counter.
# Output    : ("ms"|"hs"|"college", reason) or (None, reason)
# Detail    : PRECEDENCE. Explicit youth grades (1-12) beat everything. Explicit
#             college grades ('Fr'..'RS') come next. 'other' (blank grade) is
#             NEVER evidence -- absence of a grade is not evidence of adulthood,
#             which the density probe confirmed (grade density is bimodal).
def classify(counts):
    if isCollided(counts):
        return None, "collided"
    youth = counts["ms"] + counts["hs"]
    if youth >= _MIN_GRADED:                       # real 1-12 grades: ground truth
        return ("hs" if counts["hs"] >= counts["ms"] else "ms"), "grade"
    if counts["college"] >= _MIN_OTHER:            # explicit Fr/So/Jr/Sr/RS grades
        return "college", "grade"
    return None, "thin"                            # not enough evidence to speak


# ---------------------------------------------------------------------
# CHUNK 4 — build + persist
# ---------------------------------------------------------------------

# Purpose   : turn grade profiles into the {school: level} map, plus a census.
# Arguments : profiles -- from fetchSchoolGrades.
# Output    : (levels dict, reasons Counter)
def buildMap(profiles):
    levels = {}
    reasons = Counter()
    for key, counts in profiles.items():
        level, reason = classify(counts)
        reasons[reason] += 1
        if level:
            levels[key] = level
    return levels, reasons


# Purpose   : write the pickle where normalize_distance expects it.
# Arguments : levels; reasons -- for the meta block.
# Output    : none (writes the file, prints a census).
def saveMap(levels, reasons):
    os.makedirs(_DATA_DIR, exist_ok=True)
    payload = {"levels": levels,
               "meta": {"n_schools": len(levels), "reasons": dict(reasons)}}
    with open(_OUT_FILE, "wb") as fh:
        pickle.dump(payload, fh)

    byLevel = Counter(levels.values())
    total = sum(reasons.values())
    print("-" * 66)
    print(f"school_levels.pkl written: {os.path.abspath(_OUT_FILE)}")
    print(f"  schools mapped   : {len(levels):,} of {total:,} anet schools")
    for lvl, n in byLevel.most_common():
        print(f"    {lvl:<10} {n:>8,}")
    print("  omitted (abstain -> today's behaviour):")
    for reason in ("collided", "thin"):
        print(f"    {reason:<10} {reasons[reason]:>8,}")
    print("-" * 66)


def main():
    with getConn() as conn:
        profiles = fetchSchoolGrades(conn)
    levels, reasons = buildMap(profiles)
    saveMap(levels, reasons)


if __name__ == "__main__":
    main()