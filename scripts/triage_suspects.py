# Project: xc-predictor
# File:    scripts/triage_suspects.py
# Purpose: POST-SUSPECT triage of the FAST-flagged rows that diag_suspects.py
#          emitted. READ-ONLY against the DB. Splits the fast flags into three
#          lanes by the ERROR MECHANISM that produced them, because the remedy
#          differs per mechanism:
#
#            CLUMP  : >= _CLUMP_MIN fast rows in one (meet,div). One race cannot
#                     contain that many independent typos -- part of the field
#                     ran a different distance than the label says. Remedy is a
#                     DIVISION correction, never row drops.
#            REPEAT : the same ident fast-flagged in >= _REPEAT_MIN different
#                     meets. Two people merged under one identity. Remedy is an
#                     IDENTITY SPLIT: the rows are real, the owner is wrong.
#            ONEOFF : everything else. Magnitude cannot separate real breakouts
#                     from typos here; the athlete's own career can. An echo is
#                     another race near the same level, DIFFERENT meet and day
#                     (independence: a short course must not corroborate
#                     itself). Echoed -> KEEP. Island -> DROP.
#
#          NEW (distance proposals): every division the division scan flagged
#          gets a PROPOSED true distance, inverted from its own worksheet
#          numbers -- c (whole-field log spike) and d_eff (distance the raw
#          times imply under the current label) -- via the per-sport distance
#          law, then snapped to the nearest canonical distance:
#
#              D_label = 5000 * (d_eff/5000)^(1/b)      undo the law on d_eff
#              D_true  = D_label * exp(-c / b)          undo the mislabel
#
#          b is a per-sport constant standing in for the fitted cubic
#          (distance_spline.pkl): XC exponents measured ~0.95-1.08 -> b=1.00,
#          TF ~1.04-1.22 -> b=1.10. Proposal-grade: the snap tolerance absorbs
#          the approximation. Sign check (real TF row): d_eff 828, c=-50.4%
#          -> implied ~1590 -> snaps to 1600. A 1600 mislabeled ~800/1000.
#
#          Confidence gates:
#            PROPOSE (worksheet column) : clean snap (within _SNAP_TOL).
#            AUTO-BLOCK (corrections.py): div-flagged AND fast-clumped AND
#                                         clean snap -- three detectors agree.
#            Clump WITHOUT div flag     : NO proposal. The field as a whole
#                                         was fine, so only a SUBGROUP ran a
#                                         different race; a division override
#                                         would corrupt the majority.
#            No snap                    : left blank -- may be a gender mix,
#                                         not a distance error.
#
# EMITS (under --out, default scripts/):
#   triage_div_worklist_<sport>.tsv       clumped divisions, ranked, + proposal
#   triage_distance_proposals_<sport>.tsv every div-flagged division, + proposal
#   distance_override_<sport>.py          corrections-ready block (3-way gate)
#   triage_ident_worklist_<sport>.tsv     repeat-offender idents, ranked
#   result_drop_oneoff_<sport>.py         corrections-ready drops (islands)
#   triage_keep_<sport>.tsv               corroborated keeps, for audit
#
# USAGE
#   python scripts/triage_suspects.py --sport XC
#   python scripts/triage_suspects.py --sport XC --echo-window 0.03 --echoes 2
#   python scripts/triage_suspects.py --sport XC --dry     # no DB: lanes +
#                                                          # distance proposals
# ============================================================================

import argparse
import math
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- CONSTANTS (every knob is CLI-overridable)
# ================================================================== #
_TABLE = {"XC": "results", "TF": "results_tf"}

# Lane thresholds. CLUMP: divisions carrying 5+ fast rows hold 41% of all XC
# fast flags and cannot be independent typos. REPEAT: a typo does not follow
# an athlete across meets; 2 different meets is already a merge signature.
_CLUMP_MIN = 5
_REPEAT_MIN = 2

# Corroboration. An echo = another race by the SAME athlete within
# +/-_ECHO_WINDOW of the flagged nt, on a DIFFERENT day and in a DIFFERENT
# meet (independence -- a course-length error must not corroborate itself).
_ECHO_WINDOW = 0.05
_ECHOES_REQ = 1

# SLOW drop LINE (level-dependent). The echo test is DIRECTIONAL: real fast
# races echo (fitness persists) but real slow catastrophes are legitimately
# unique (nothing echoes a fall), so corroboration cannot sentence slow rows;
# only implausibility can. And implausibility depends on LEVEL: elite race
# distributions are tight, novice ones loose, so a flat % is wrong at both
# ends. The line interpolates in ln(base) between two anchors and clamps
# outside them:
#   base <= 840s (14:00 5K)  -> drop at >= +30% slower  (18:12 is not a race
#                               for that athlete; comebacks run 10-25% slow,
#                               so injuries still clear it -- barely; recheck
#                               this anchor against real elite data after the
#                               --row-slow 0.25 re-run)
#   base >= 1800s (30:00 5K) -> drop at >= +70% slower  (51:00 -- walked it)
# Tightening past this starts deleting terrible-but-REAL races (a 17:00
# athlete's +34% cramp-death is a 22:46 someone actually ran), which would
# systematically bias every athlete's history optimistic. Do not go harsher
# without evidence from the re-run.
_SLOW_LINE_LO = (840.0, 30.0)     # (base seconds, drop line %)
_SLOW_LINE_HI = (1800.0, 70.0)

# INSERT batching. libpq caps a statement at 65,535 bound parameters;
# 10,000 rows x 4 columns = 40,000 stays under it on every driver.
_INSERT_CHUNK = 10_000

# Distance law, proposal-grade. Per-sport constant exponent standing in for
# the fitted cubic (engine/data/distance_spline.pkl). Measured local
# exponents: XC ~0.95-1.08, TF ~1.04-1.22 (see the distance session notes).
_EXPONENT = {"XC": 1.00, "TF": 1.10}

# Canonical race distances (metres) per sport -- the snap targets. Edit here
# if a legitimate local distance is missing; an unsnappable implied distance
# is left unproposed rather than forced.
_CANONICAL = {
    "XC": (1500, 1609, 2000, 2414, 3000, 3200, 3218, 4000, 4180,
           4800, 4828, 5000, 6000, 6437, 8000, 10000),
    "TF": (55, 60, 100, 110, 200, 300, 400, 500, 600, 800, 1000,
           1500, 1600, 1609, 2000, 3000, 3200, 5000, 10000),
}

# A proposal requires the implied distance within this fraction of a
# canonical one. 6% is wider than the law approximation error but narrower
# than the gap between adjacent canonical distances.
_SNAP_TOL = 0.06


# ================================================================== #
# CHUNK 2 -- IDENTIFIER SAFETY (same boundary rule as diag_suspects)
# ================================================================== #

# _ident : %s binds VALUES not IDENTIFIERS, so a table name is interpolated
#          and therefore validated once, at the boundary.
def _ident(name):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


# ================================================================== #
# CHUNK 3 -- LOAD THE WORKSHEETS (files in, no DB yet)
# ================================================================== #

# _intOrNone : worksheet key columns carry either an integer or the string
#   "None" (tfrrs rows can lack a div_id; diag writes the Python None through
#   str()). Parse both; None stays a legitimate dict key.
def _intOrNone(s):
    return None if s == "None" else int(s)


# _loadFastRows : parse suspects_row_<sport>.tsv down to the FAST rows we
#   triage. REGRESSION rows (already corrected) are excluded automatically
#   because we keep only proposed == "FAST".
#   Columns (0-based): 0 kind, 1 source, 2 meet_id, 3 div_id, 4 result_id,
#   5 ident, 6 nt, 7 ind_ratio, 8 swing_pct, 9 already, 10 proposed.
def _loadRows(path, tier):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\r\n").split("\t")
            if len(p) < 11 or p[10] != tier:
                continue
            if p[6] == "na":            # no normalized time -> nothing to echo
                continue
            rows.append({"source": p[1], "meet": int(p[2]),
                         "div": _intOrNone(p[3]),
                         "result_id": int(p[4]), "ident": int(p[5]),
                         "nt": float(p[6]),
                         "ind_ratio": None if p[7] == "na" else float(p[7]),
                         "swing": float(p[8])})
    return rows


# _loadFlaggedDivs : every division the DIVISION scan flagged, WITH the two
#   numbers the distance proposal inverts: c (log spike, stored as ln from
#   the worksheet's percent via log1p) and d_eff (metres, or None for "na").
#   Columns (0-based): 0 kind, 1 source, 2 meet_id, 3 div_id, 4 n,
#   5 c_pct, 6 iqr_pct, 7 d_eff, ...
#   Returned as a dict; plain `key in divs` membership still works wherever
#   only the flag matters.
def _loadFlaggedDivs(path):
    divs = {}
    if not os.path.exists(path):
        return divs
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\r\n").split("\t")
            if len(p) < 8:
                continue
            d_eff = None if p[7] == "na" else float(p[7])
            divs[(int(p[2]), _intOrNone(p[3]))] = {
                "c": math.log1p(float(p[5]) / 100.0),   # percent -> ln gap
                "d_eff": d_eff}
    return divs


# ================================================================== #
# CHUNK 4 -- LANE SPLIT (pure Python, mechanism before remedy)
# ================================================================== #

# _countDivFlags : fast-flag count per (meet,div). Counter defaults missing
#   keys to 0, so counting needs no key-exists checks.
def _countDivFlags(rows):
    return Counter((r["meet"], r["div"]) for r in rows)


# _findRepeatIdents : idents flagged in >= repeat_min DIFFERENT meets, among
#   non-clump rows only (a clump already explains its own rows).
def _findRepeatIdents(rows, repeat_min):
    meets_by_ident = defaultdict(set)
    for r in rows:
        meets_by_ident[r["ident"]].add(r["meet"])
    return {i for i, ms in meets_by_ident.items() if len(ms) >= repeat_min}


# _splitLanes : the triage tree. Priority order matters: CLUMP first (the
#   division explains the row), then REPEAT (the identity explains it), and
#   only unexplained rows fall through to the corroboration lane.
def _splitLanes(rows, clump_min, repeat_min):
    divc = _countDivFlags(rows)
    clump = [r for r in rows if divc[(r["meet"], r["div"])] >= clump_min]
    rest = [r for r in rows if divc[(r["meet"], r["div"])] < clump_min]
    repeats = _findRepeatIdents(rest, repeat_min)
    repeat = [r for r in rest if r["ident"] in repeats]
    oneoff = [r for r in rest if r["ident"] not in repeats]
    return clump, repeat, oneoff, divc


# ================================================================== #
# CHUNK 5 -- CORROBORATION (the only DB work: one stage + one join)
# ================================================================== #

# _stageFlagged : materialize the corroboration lane as a temp table so the
#   echo count is ONE set-based join, not per-row queries. Chunked inserts
#   (see _INSERT_CHUNK); params travel flat, one %s per value -- values are
#   never formatted into the SQL string.
def _stageFlagged(cur, rows):
    cur.execute("""
        CREATE TEMP TABLE _flagged (
            result_id bigint, ident bigint, nt double precision, meet bigint)
    """)
    vals = [(r["result_id"], r["ident"], r["nt"], r["meet"]) for r in rows]
    for i in range(0, len(vals), _INSERT_CHUNK):
        chunk = vals[i:i + _INSERT_CHUNK]
        tmpl = ",".join(["(%s,%s,%s,%s)"] * len(chunk))
        flat = [x for v in chunk for x in v]
        cur.execute("INSERT INTO _flagged VALUES " + tmpl, flat)
    cur.execute("CREATE INDEX ON _flagged (ident)")
    cur.execute("ANALYZE _flagged")     # fresh stats -> sane join plan


# _countEchoes : echoes per flagged row, in one query. `self` fetches the
#   flagged row's own date (the worksheet does not carry it); `r` is every
#   other NORMALIZED race by the same athlete; the echo conditions live in
#   the JOIN's ON clause. INNER join drops zero-echo rows -- the caller
#   defaults missing result_ids to 0 with dict.get.
#
# ! ALIBIS ARE NORMALIZED ROWS, NOT RATED ROWS (2026-08-27 lesson, person
#   23947175). This join used to demand `r.speed_rating > 0`, so only RATED
#   races could vouch for a flagged one. Dropped rows never get a
#   normalized_time, so each triage round's drops erased the next round's
#   alibis -- an improving athlete whose only rated races were his slow
#   freshman self had EVERY later season condemned as a one-off. nt > 0 is
#   the widest honest bar: the row survived every backfill sanity gate.
#   Rows already in _RESULT_DROP still have nt NULL and still cannot alibi;
#   scripts/amnesty_result_drops.py is the drop-blind retrial for those.
def _countEchoes(cur, table, window):
    t = _ident(table)
    cur.execute(f"""
        SELECT f.result_id, count(*) AS echoes
        FROM _flagged f
        JOIN {t} self ON self.result_id = f.result_id
        JOIN {t} r
          ON COALESCE(r.person_id, r.athlete_id) = f.ident
         AND r.result_id <> f.result_id
         AND r.meet_id   <> f.meet
         AND r.date IS NOT NULL AND self.date IS NOT NULL
         AND r.date <> self.date
         AND r.normalized_time > 0
         AND r.normalized_time BETWEEN f.nt * (1 - %s) AND f.nt * (1 + %s)
        GROUP BY f.result_id
    """, (window, window))
    return dict(cur.fetchall())


# ================================================================== #
# CHUNK 6 -- ROW VERDICTS
# ================================================================== #

# _verdicts : enough echoes -> the career vouches for the race -> KEEP;
#   island -> DROP.
def _verdicts(oneoff, echoes, required):
    keeps, drops = [], []
    for r in oneoff:
        n = echoes.get(r["result_id"], 0)
        r["echoes"] = n
        (keeps if n >= required else drops).append(r)
    return keeps, drops


# ================================================================== #
# CHUNK 6b -- DISTANCE PROPOSALS (invert the label from the field's spike)
# ================================================================== #

# _labeledDistance : undo the distance law on d_eff to recover the distance
#   the division is CURRENTLY labeled as. d_eff = 5000*(D_label/5000)^b was
#   built from time/normalized_time, so invert with the 1/b power.
def _labeledDistance(d_eff, b):
    return 5000.0 * (d_eff / 5000.0) ** (1.0 / b)


# _impliedDistance : the distance the field's own spike says they REALLY ran.
#   c is the ln rating gap; a mislabel of D_true vs D_label shifts ln(rating)
#   by -b*ln(D_true/D_label), so D_true = D_label * exp(-c/b). Negative c
#   (field rated too slow) -> true distance LONGER than the label, and vice
#   versa. Returns None when d_eff is unusable.
def _impliedDistance(c, d_eff, b):
    if d_eff is None or d_eff <= 0:
        return None
    return _labeledDistance(d_eff, b) * math.exp(-c / b)


# _snapCanonical : nearest canonical distance, accepted only within tol.
#   Distance measured in LOG space (abs(ln(d/k))) so 6% means 6% at every
#   scale -- 800m and 8000m are judged by the same relative yardstick.
#   Returns (target, rel_err) on a clean snap, (None, rel_err) otherwise.
def _snapCanonical(d, sport, tol):
    if d is None:
        return None, None
    best = min(_CANONICAL[sport], key=lambda k: abs(math.log(d / k)))
    err = d / best - 1.0
    return (best if abs(err) <= tol else None), err


# _proposeDistances : one proposal record per div-flagged division. The
#   worksheet gets EVERY flagged division (proposed target filled on a clean
#   snap, blank otherwise -- an unsnappable spike may be a gender mix, not a
#   distance error). auto=True marks the three-way-gated subset: div-flagged
#   AND fast-clumped AND cleanly snapped -- only those reach corrections.py.
def _proposeDistances(div_flagged, divc, sport, clump_min, tol):
    b = _EXPONENT[sport]
    out = []
    for (meet, div), info in div_flagged.items():
        implied = _impliedDistance(info["c"], info["d_eff"], b)
        target, err = _snapCanonical(implied, sport, tol)
        out.append({"meet": meet, "div": div,
                    "c": info["c"], "d_eff": info["d_eff"],
                    "implied": implied, "target": target, "err": err,
                    "fast_rows": divc.get((meet, div), 0),
                    # div None = the meet's UNASSIGNED rows lumped together,
                    # possibly spanning several events -- never auto-override.
                    "auto": (target is not None
                             and div is not None
                             and divc.get((meet, div), 0) >= clump_min)})
    out.sort(key=lambda p: -abs(p["c"]))
    return out


# ================================================================== #
# CHUNK 7 -- WORKLIST / CORRECTIONS EMISSION
# ================================================================== #

def _fmt(x, spec="{:.0f}", none="na"):
    """One place for the 'number or na' formatting every emitter needs."""
    return none if x is None else spec.format(x)


# _writeDivWorklist : clumped divisions ranked by fast-count, annotated with
#   the division flag and the distance proposal when one exists. A clump
#   with also_div_flagged '-' deliberately has NO proposal: the field as a
#   whole was fine, so it is a SUBGROUP problem -- eyes, not an override.
def _writeDivWorklist(path, clump, divc, proposals):
    prop = {(p["meet"], p["div"]): p for p in proposals}
    seen = sorted({(r["meet"], r["div"]) for r in clump},
                  key=lambda k: -divc[k])
    with open(path, "w", encoding="utf-8") as f:
        f.write("# meet_id\tdiv_id\tfast_rows\talso_div_flagged\t"
                "implied_m\tproposed_target\tdecision\n")
        f.write("# remedy at DIVISION level; no proposal + no div flag "
                "= subgroup, needs eyes\n")
        for key in seen:
            p = prop.get(key)
            f.write("\t".join(str(x) for x in (
                key[0], key[1], divc[key],
                "YES" if p else "-",
                _fmt(p["implied"]) if p else "na",
                (p["target"] if p and p["target"] else ""),
                "")) + "\n")


# _writeDistanceProposals : EVERY div-flagged division with its inversion --
#   this fills the `target` column diag's worksheet left blank. snap_err_pct
#   shows how clean the snap was; blank target = no canonical distance fits
#   (consider gender).
def _writeDistanceProposals(path, proposals):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# meet_id\tdiv_id\tc_pct\td_eff\timplied_m\t"
                "proposed_target\tsnap_err_pct\tfast_rows\tauto\n")
        f.write("# implied_m = labeled distance corrected by the field's own "
                "spike; blank target = no clean snap (gender?)\n")
        for p in proposals:
            f.write("\t".join(str(x) for x in (
                p["meet"], p["div"],
                f"{(math.exp(p['c']) - 1) * 100:+.1f}",
                _fmt(p["d_eff"]), _fmt(p["implied"]),
                p["target"] if p["target"] else "",
                _fmt(p["err"], "{:+.1%}"), p["fast_rows"],
                "AUTO" if p["auto"] else
                ("NODIV" if p["div"] is None else "-"))) + "\n")


# _writeDistanceOverrides : corrections.py-ready block, three-way gate only
#   (div-flagged AND clumped AND snapped). Same shape diag_suspects loads:
#   a dict keyed (meet_id, div_id) -> metres.
def _writeDistanceOverrides(path, proposals):
    auto = [p for p in proposals if p["auto"]]
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Auto-generated by triage_suspects.py -- distance overrides\n")
        f.write("# where THREE detectors agree: whole-field spike (division\n")
        f.write("# scan), 5+ implausible rows (clump), and a clean canonical\n")
        f.write("# snap. Merge into _DISTANCE_OVERRIDES.\n")
        f.write("_DISTANCE_OVERRIDES_ADDITIONS = {\n")
        for p in auto:
            f.write(f"    ({p['meet']}, {p['div']}): {p['target']},"
                    f"  # c={(math.exp(p['c'])-1)*100:+.1f}% "
                    f"implied={_fmt(p['implied'])} "
                    f"snap_err={_fmt(p['err'], '{:+.1%}')} "
                    f"fast_rows={p['fast_rows']}\n")
        f.write("}\n")
    return len(auto)


# _writeIdentWorklist : repeat-offender idents ranked by meets flagged. The
#   remedy is an identity split; one split clears every row under it.
def _writeIdentWorklist(path, repeat):
    meets = defaultdict(set)
    nrows = Counter()
    for r in repeat:
        meets[r["ident"]].add(r["meet"])
        nrows[r["ident"]] += 1
    order = sorted(meets, key=lambda i: -len(meets[i]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("# ident\tmeets_flagged\tflag_rows\tdecision\n")
        f.write("# remedy = IDENTITY SPLIT; the rows are real, the owner is wrong\n")
        for i in order:
            f.write(f"{i}\t{len(meets[i])}\t{nrows[i]}\t\n")


# ★ THE MASS-DROP RAIL (2026-08-27, issue #23). The 2026-07 waves put
#   hundreds of thousands of auto-generated ids into _RESULT_DROP in single
#   files. A drop list past the cap is DIVERTED to a name apply_triage
#   cannot merge (it matches *.py exactly), so an unattended pipeline can
#   propose at scale but never convict at scale -- the list survives for a
#   human to read. Same shape as shrinkByLinkage's athlete rails.
_MAX_DROPS = 2000


def _railed(path, n, cap):
    if n <= cap:
        return path, False
    return f"{path}.OVER-CAP-{n}.txt", True


# _writeDropList : corrections.py-ready block for the island one-offs.
def _writeDropList(path, drops, window, required):
    drops = sorted(drops, key=lambda r: r["swing"])   # worst first
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Auto-generated by triage_suspects.py -- UNCORROBORATED\n")
        f.write(f"# one-off fast rows: no other race within {window:.0%}\n")
        f.write(f"# (different meet, different day), {required} echo(es)\n")
        f.write("# required. Merge into _RESULT_DROP.\n")
        f.write("_RESULT_DROP_ADDITIONS = {\n")
        for r in drops:
            f.write(f"    {r['result_id']},  # swing={r['swing']:+.0f}% "
                    f"nt={r['nt']:.0f} echoes=0  meet/div {r['meet']}/{r['div']}\n")
        f.write("}\n")


# _slowLineFor : the drop line (in % slower) for an athlete of a given base
#   level. Linear in ln(base) between the anchors, clamped outside -- log
#   space so "halfway between 14:00 and 30:00" means the same thing
#   multiplicatively that every other yardstick in this pipeline uses.
def _slowLineFor(base, lo, hi):
    (b0, p0), (b1, p1) = lo, hi
    if base <= b0:
        return p0
    if base >= b1:
        return p1
    t = (math.log(base) - math.log(b0)) / (math.log(b1) - math.log(b0))
    return p0 + t * (p1 - p0)


# _slowDeepTail : SLOW rows past their OWN level's line, excluding rows in
#   div-flagged divisions (the distance/label fix repairs those -- dropping
#   them would delete athletes from a race we are about to make valid).
#   base = nt / ind_ratio (the athlete's bracket-median level); a row with no
#   ind_ratio cannot be placed on the curve and conservatively gets the
#   loosest line.
def _slowDeepTail(slow, div_flagged, lo, hi):
    out = []
    for r in slow:
        if (r["meet"], r["div"]) in div_flagged:
            continue
        base = r["nt"] / r["ind_ratio"] if r.get("ind_ratio") else None
        line = _slowLineFor(base, lo, hi) if base else hi[1]
        if r["swing"] >= line:
            r["line"] = line
            out.append(r)
    return out


# _writeSlowDropList : corrections.py-ready block for the deep-tail slow rows.
def _writeSlowDropList(path, drops):
    drops = sorted(drops, key=lambda r: -(r["swing"] - r["line"]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Auto-generated by triage_suspects.py -- SLOW rows past the\n")
        f.write("# LEVEL-DEPENDENT implausibility line (tight for fast athletes,\n")
        f.write("# loose for slow ones). Rows in divisions receiving a distance\n")
        f.write("# fix are excluded. Merge into _RESULT_DROP.\n")
        f.write("_RESULT_DROP_ADDITIONS = {\n")
        for r in drops:
            f.write(f"    {r['result_id']},  # swing={r['swing']:+.0f}% "
                    f"line=+{r['line']:.0f}% nt={r['nt']:.0f}  "
                    f"meet/div {r['meet']}/{r['div']}\n")
        f.write("}\n")


# _writeKeepAudit : the corroborated keeps, so the aggressive call stays
#   auditable.
def _writeKeepAudit(path, keeps):
    keeps = sorted(keeps, key=lambda r: r["swing"])
    with open(path, "w", encoding="utf-8") as f:
        f.write("# result_id\tident\tmeet_id\tdiv_id\tnt\tswing_pct\techoes\n")
        f.write("# kept: the athlete's own career echoes this level elsewhere\n")
        for r in keeps:
            f.write(f"{r['result_id']}\t{r['ident']}\t{r['meet']}\t{r['div']}\t"
                    f"{r['nt']:.0f}\t{r['swing']:+.0f}\t{r['echoes']}\n")


# ================================================================== #
# CHUNK 8 -- ORCHESTRATION
# ================================================================== #

def _laneSummary(total, clump, repeat, oneoff):
    print(f"\n  lanes ({total:,} FAST rows in):")
    print(f"    CLUMP  {len(clump):>8,}  -> division worklist (label fixes)")
    print(f"    REPEAT {len(repeat):>8,}  -> identity worklist (merge splits)")
    print(f"    ONEOFF {len(oneoff):>8,}  -> corroboration test (DB)")


def _proposalSummary(proposals, n_auto):
    snapped = sum(1 for p in proposals if p["target"] is not None)
    print(f"  distance proposals: {len(proposals):,} flagged divisions, "
          f"{snapped:,} snapped clean, {n_auto:,} AUTO (3-way gate)")


def _run(sport, in_dir, out_dir, window, required, clump_min, repeat_min,
         snap_tol, slow_base_lo, slow_pct_lo, slow_base_hi, slow_pct_hi, dry,
         max_drops=_MAX_DROPS):
    row_path = os.path.join(in_dir, f"suspects_row_{sport.lower()}.tsv")
    div_path = os.path.join(in_dir, f"suspects_div_{sport.lower()}.tsv")
    rows = _loadRows(row_path, "FAST")
    slow = _loadRows(row_path, "SLOW")
    div_flagged = _loadFlaggedDivs(div_path)
    clump, repeat, oneoff, divc = _splitLanes(rows, clump_min, repeat_min)
    _laneSummary(len(rows), clump, repeat, oneoff)

    # Distance proposals are file-only math -- they run even under --dry.
    proposals = _proposeDistances(div_flagged, divc, sport, clump_min, snap_tol)

    os.makedirs(out_dir, exist_ok=True)
    dv = os.path.join(out_dir, f"triage_div_worklist_{sport.lower()}.tsv")
    pv = os.path.join(out_dir, f"triage_distance_proposals_{sport.lower()}.tsv")
    ov = os.path.join(out_dir, f"distance_override_{sport.lower()}.py")
    iv = os.path.join(out_dir, f"triage_ident_worklist_{sport.lower()}.tsv")
    _writeDivWorklist(dv, clump, divc, proposals)
    _writeDistanceProposals(pv, proposals)
    n_auto = _writeDistanceOverrides(ov, proposals)
    _writeIdentWorklist(iv, repeat)
    sv = os.path.join(out_dir, f"result_drop_slow_{sport.lower()}.py")
    lo = (slow_base_lo, slow_pct_lo)
    hi = (slow_base_hi, slow_pct_hi)
    slow_drops = _slowDeepTail(slow, div_flagged, lo, hi)
    sv, sv_over = _railed(sv, len(slow_drops), max_drops)
    _writeSlowDropList(sv, slow_drops)
    if sv_over:
        print(f"\n  !! RAIL: {len(slow_drops):,} slow drops exceed the "
              f"{max_drops:,} cap -- DIVERTED to\n     {sv}\n     "
              "(apply_triage will not merge it; a human reads it first)")
    _proposalSummary(proposals, n_auto)
    print(f"  slow line (+{slow_pct_lo:.0f}% at {slow_base_lo:.0f}s .. "
          f"+{slow_pct_hi:.0f}% at {slow_base_hi:.0f}s): "
          f"{len(slow_drops):,} drops -> {sv}")

    if dry:
        print(f"\n  --dry: worklists + proposals written, corroboration skipped:")
        print(f"    {dv}\n    {pv}\n    {ov}\n    {iv}")
        return

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '2GB'")
        print("  staging corroboration lane + counting echoes (one join) ...")
        _stageFlagged(cur, oneoff)
        echoes = _countEchoes(cur, _TABLE[sport], window)
        conn.rollback()                 # read-only: nothing persists

    keeps, drops = _verdicts(oneoff, echoes, required)
    dp = os.path.join(out_dir, f"result_drop_oneoff_{sport.lower()}.py")
    kp = os.path.join(out_dir, f"triage_keep_{sport.lower()}.tsv")
    dp, dp_over = _railed(dp, len(drops), max_drops)
    _writeDropList(dp, drops, window, required)
    _writeKeepAudit(kp, keeps)
    if dp_over:
        print(f"\n  !! RAIL: {len(drops):,} one-off drops exceed the "
              f"{max_drops:,} cap -- DIVERTED to\n     {dp}\n     "
              "(apply_triage will not merge it; a human reads it first)")

    print(f"\n  one-off verdicts: KEEP {len(keeps):,}  DROP {len(drops):,}")
    print(f"  (echo = other race within {window:.0%}, different meet+day; "
          f"{required} required)")
    print(f"\n  wrote:\n    {dv}\n    {pv}\n    {ov}\n    {iv}\n    {dp}\n    {kp}")
    print("  merge result_drop_oneoff_*.py and distance_override_*.py into")
    print("  corrections.py; work the worklists top-down; re-run")
    print("  diag_suspects.py to confirm they clear.")


# ================================================================== #
# CHUNK 9 -- CLI
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Triage FAST suspects by mechanism: clump / repeat / "
                    "corroborated one-off; propose true distances for "
                    "flagged divisions (read-only).")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--in", dest="in_dir", default="scripts",
                    help="dir holding suspects_row/_div_<sport>.tsv")
    ap.add_argument("--out", default="scripts")
    ap.add_argument("--echo-window", type=float, default=_ECHO_WINDOW,
                    help="echo = another race within this fraction of the "
                         "flagged nt (default 0.05 = 5%%)")
    ap.add_argument("--echoes", type=int, default=_ECHOES_REQ,
                    help="echoes required to KEEP a one-off (default 1)")
    ap.add_argument("--clump-min", type=int, default=_CLUMP_MIN,
                    help="fast rows in one division to call it a CLUMP")
    ap.add_argument("--repeat-min", type=int, default=_REPEAT_MIN,
                    help="distinct meets flagged to call an ident a REPEAT")
    ap.add_argument("--slow-base-lo", type=float, default=_SLOW_LINE_LO[0],
                    help="fast anchor: base seconds (default 840 = 14:00)")
    ap.add_argument("--slow-pct-lo", type=float, default=_SLOW_LINE_LO[1],
                    help="drop line %% at/below the fast anchor (default 35)")
    ap.add_argument("--slow-base-hi", type=float, default=_SLOW_LINE_HI[0],
                    help="slow anchor: base seconds (default 1800 = 30:00)")
    ap.add_argument("--slow-pct-hi", type=float, default=_SLOW_LINE_HI[1],
                    help="drop line %% at/above the slow anchor (default 90)")
    ap.add_argument("--snap-tol", type=float, default=_SNAP_TOL,
                    help="implied distance must sit within this fraction of "
                         "a canonical one to propose it (default 0.06)")
    ap.add_argument("--dry", action="store_true",
                    help="lanes + distance proposals only; skip the DB pass")
    ap.add_argument("--max-drops", type=int, default=_MAX_DROPS,
                    help="drop lists past this size are diverted to a "
                         ".OVER-CAP file apply_triage cannot merge "
                         f"(default {_MAX_DROPS})")
    args = ap.parse_args()
    if not args.dry:
        initPool()
    _run(args.sport, args.in_dir, args.out, args.echo_window, args.echoes,
         args.clump_min, args.repeat_min, args.snap_tol,
         args.slow_base_lo, args.slow_pct_lo, args.slow_base_hi,
         args.slow_pct_hi, args.dry, args.max_drops)


if __name__ == "__main__":
    main()