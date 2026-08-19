# Project: xc-predictor
# File:    scripts/adjudicate_regressions.py
# Purpose: bulk-adjudicate a flagged-division crop. For each division, gather
#          three candidate distances and let METADATA and PHYSICS -- neither of
#          which depends on brackets -- decide:
#
#            stored : the source's own distance (meets.distance for anet
#                     divisions, the meets_tfrrs blob for tfrrs)
#            ov     : the EFFECTIVE distance the ruler used (triage's ov=
#                     column), or None if it resolved none at all
#            draft  : the classifier's post-fix statistical draft
#
#          Verdict rules, most decisive first:
#            AGREE       stored passes physics AND sits within 8% of draft
#            STORED      stored passes physics, ov FAILS or does not exist
#            DRAFT       no stored; draft passes physics and ov fails
#            AMBIGUOUS   stored and ov BOTH pass physics but disagree ->
#                        physics cannot discriminate -> human queue
#            WORKLIST    everything else -> human queue (page-check)
#
#          Output: adjudicated_overrides_<sport>.py (mergeable via
#          apply_triage) + adjudication_worklist_<sport>.tsv + console census.
#          READ-ONLY against the DB; writes only its own output files.
#
# ============================================================================
# CHANGELOG 2026-07-16 -- THE PHANTOM OVERRIDE FIX (11.1)
#
# THE SYMPTOM
#   loop2 ran 8 cycles overnight. verify_c18 reported:
#       overridden divisions no longer flagging : 2,195
#       overridden divisions STILL flagging     :   176      (2,195+176 = 2,371
#                                                             = EVERY override)
#   The 176 showed the FULL uncorrected gap (-35% to -41%), not a residue. And
#   peek_override said why:
#       (10064, 0) -> 4000.0     ... and the blob already said 4000.0
#       (8025,  0) -> 5000.0     ... and the blob already said 5000.0
#   The override WAS the fallback. Applying it changed nothing. Forever.
#
# HOW A PHANTOM WAS BORN (trace of 10064/0)
#   Cold start: ov=None -> o_ok=False. stored=4000 (blob). s_ok=True, because
#   the physics band is 4.4x wide and waves almost anything through. Rule 2
#   fires -> STORED 4000. Then the no-op test asked:
#       _isNoop(4000, live.get(key))          live = the OVERRIDE DICT
#   The key was not in the override dict, so live_value was None, so it read as
#   NEWS and was written. But the BACKFILL'S FALLBACK IS `stored`. Writing
#   stored as an override is news to corrections.py and a no-op to the ruler.
#   THE NO-OP TEST WAS ASKING THE WRONG QUESTION.
#   Next cycle: ov=4000 -> o_ok=True -> rules 1,2,3,4 all fail -> WORKLIST,
#   forever, at -40%. That is the 176. That is the plateau.
#
# FIX 1 -- diff against the RULER, not the file  (11.1 fix #1)
#   triage's `ov=` column has been the EFFECTIVE distance since 7/15 -- what
#   backfill_normalize._resolveDistanceGender actually used, override or
#   fallback. It was sitting in info["ov"] unused for this. _classify now
#   diffs verdicts against info["ov"]. A cold-start STORED verdict that merely
#   restates the fallback is now correctly a NOOP and is never written.
#
# FIX 2 -- stop feeding rule 1 a corrupted `stored`
#   7.3: tfrrs XC distance was NEVER STORED -- it exists only on the live page.
#   The blob's `distance` field is a CACHED PARSE of its own `div_name`. They
#   are ONE claim, not two witnesses. And the cache is stale:
#       'Men 4 Mile Run CC' -> 6437.376   (119 meets)
#       'Men 4 Mile Run CC' -> 4000.0     ( 29 meets)
#   One string, two numbers. A deterministic parser CANNOT do that; two parser
#   VERSIONS can. The old one read "4 Mile" as 4 km. The fix landed; a re-scrape
#   never finished carrying it everywhere (Q14 -- answered: it did not).
#
#   So _storedTfrrs now re-derives the distance from div_name using the SAME
#   parser the rest of the pipeline uses (event_parse.distanceFromEventShort).
#   THIS IS NOT "TRUSTING THE TITLE OVER THE DATA". The title IS the data here;
#   the distance field is a cached read of it. Re-parsing recovers what the
#   human typed. It says NOTHING about whether the human was right -- 4876's
#   "College Men" still yields no distance and still goes to a human.
#
#   With `stored` honest, RULE 1 ALREADY DOES THE WORK -- no new rule needed:
#       10064/0  stored 6437.376 (title)  draft 6437 (inversion, saw no title)
#                agreement 0.006%  ->  AGREE
#   Two instruments, no shared code, no shared input. That is 14's internal
#   gold standard, and it is what 11.1's REFUTED rule was reaching for.
#
# NOT CHANGED, DELIBERATELY
#   _storedAnet. meets.distance is a genuinely SEPARATE human-entered field
#   from meet_name/division -- there, title-vs-distance really would be two
#   witnesses to one claim, and 0.14 (stored beats draft ~4:1, measured on the
#   600-regression anet census) still governs. That census was never run
#   against a value that had been through a unit-conversion bug, which is why
#   it does not transfer to the tfrrs blob.
# ============================================================================
#
# CHANGELOG 2026-07-15
#   1. Old rule 3 reverted ov -> stored whenever they differed by >10%, WITHOUT
#      checking whether ov also passed physics. With a 4.4x-wide physics band
#      stored almost always passes, so rule 3 was "always revert to metadata".
#      That was the reverter the `s` doc predicted (STORED dominating at 81%).
#      It is now AMBIGUOUS: if physics cannot tell them apart, a human must.
#   2. Verdicts are diffed against what is already live. A verdict that changes
#      nothing is a NOOP and is NOT emitted. The census prints CHANGE vs NOOP,
#      so a crop that cannot move the needle says so in 30 seconds instead of
#      50 minutes.
#   3. ov=None is now PARSED AND SUPPORTED. _ROW_RE required `ov=([\d.]+)`, so
#      every division WITHOUT an override failed to match and was silently
#      dropped -- 496 of 500 on the first XC z-cut crop, and 100% of TF (whose
#      _DISTANCE_OVERRIDES_TF is empty). This is the cold-start path.
#
# USAGE
#   python scripts\adjudicate_regressions.py --sport XC --log triage_xc_z.log
#   ... read the CHANGE line: >0 means a cycle is worth its 49 minutes ...

import argparse
import os
import re
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
from database import getConn, initPool
# The SAME parser the backfill, triage and the fitters use. Importing it (not
# reimplementing it) is deliberate: a producer and its consumer are two callers
# of ONE definition -> import. Mirrors are for verifier-vs-audited
# independence, which this is not. (14: MIRRORS vs IMPORTS.)
from event_parse import distanceFromEventShort

_TABLE = {"XC": "results", "TF": "results_tf"}

# physics rails (s per mile), same as the splitter
_WINNER_FLOOR = 230.0
_TAIL_CEIL = 1020.0
_MILE = 1609.34

# stored-vs-draft agreement gate
_AGREE_TOL = 0.08
# two candidates closer than this are "the same answer", not a disagreement
_SAME_TOL = 0.02
# a verdict within this of the EFFECTIVE distance changes nothing -> NOOP
_NOOP_TOL = 0.001
# a re-derived title distance within this of the cached one is a CONFIRMATION,
# not a repair -- only count the disagreements in the ledger
_REPAIR_TOL = 0.01

# --- the REFUTED rule's constants (2026-07-17). Both MEASURED, not typed. ---

# calibrate_shape.py: p90 of the gap-IQR over 397 unflagged divisions
# (p50=6.3, p90=10.4, p99=16.5). A field TIGHTER than the null's own p90 is
# speaking with one voice, and one voice is what a wrong label sounds like: a
# wrong distance multiplies EVERY time by the same constant, so the
# distribution SLIDES and its shape does not change. 0.11 read forwards --
# "a wall of spikes has a WIDTH; wide spread => suspect the BRACKETS."
_NORMAL_IQR = 10.4

# The classifier verdicts meaning "the WHOLE field moved together". BIMODAL and
# PARTIAL mean the bucket holds more than one population, where a division-wide
# edit is FORBIDDEN (0.10, the 26785 rule) however convincing the median looks.
# FEW_SPIKES is a row-level problem wearing a division's clothes.
#
# Today the inverter only drafts UNIFORM_NEG, so this gate is near-tautological
# and earns nothing. It is here because it is the condition that MAKES the rule
# sound. A silent assumption is one a future classifier change breaks without
# anyone noticing.
_UNIFORM_SHAPES = ("UNIFORM_NEG", "UNIFORM_POS")


# ================================================================== #
# CHUNK 1 -- PARSE THE CLASSIFIER LOG (verdict table + drafted values)
# ================================================================== #

# _ROW_RE
# `ov=` is ([\d.]+|None): triage prints "None" for any division whose ruler
# resolved no distance at all. Requiring digits dropped every such row -- the
# entire cold-start population. The regex deliberately does NOT anchor the end
# of the line, so triage's trailing free-text note does not break the match.
#
# 2026-07-17: group 9 captures `iqr=`. It used to be let through UNREAD -- the
# gap spread was printed on every line and parsed by nobody, so the REFUTED
# rule had no shape to gate on. The group is OPTIONAL, so logs written before
# triage printed iqr still parse and simply carry iqr=None. _refutedShape reads
# None as "unknown spread", which is not "tight spread", so an old log can never
# trigger a REFUTED verdict by accident. NULL IS BETTER THAN STALE.
_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+ov=\s*([\d.]+|None)\s+(\w+)\s+n=\s*(\d+)\s+"
    r"\+(\d+)\s+-(\d+)\s+clean=(\d+)"
    r"(?:\s+iqr=\s*([\d.]+))?"
    r"(?:\s+fan=\s*(FAN|-))?")           # group 10, OPTIONAL: the draft gate
_DRAFT_RE = re.compile(r"^\s*\((\d+),\s*(\d+)\):\s*([\d.]+),")


def _parseOv(text):
    """
    Purpose : "None" -> None, "6437.0" -> 6437.0.
    Why     : a division with no resolved distance is not an error, it is the
              normal state for 9,626 XC divisions and for all of TF.
    """
    return None if text == "None" else float(text)


def _readLog(path):
    """Tee-Object writes UTF-16 on PS 5.1; sniff the BOM rather than assume."""
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else raw.decode("utf-8", "replace")

    rows, drafts = {}, {}
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if m:
            meet, div = int(m.group(1)), int(m.group(2))
            rows[(meet, div)] = {
                "ov": _parseOv(m.group(3)), "cat": m.group(4),
                "n": int(m.group(5)), "pos": int(m.group(6)),
                "neg": int(m.group(7)), "clean": int(m.group(8)),
                # group 9 is None when the optional iqr= group did not
                # participate -- a real None from the regex engine, not a
                # parse failure, so float() only runs when it matched.
                "iqr": float(m.group(9)) if m.group(9) else None,
                # group 10: the draft gate. "FAN" -> refuse a distance draft;
                # "-" or absent (old log) -> not a fan. Absence is NOT "fan",
                # so a pre-fan log degrades to today's behaviour, never worse.
                "fan": (m.group(10) == "FAN")}
            continue
        d = _DRAFT_RE.match(line)
        if d:
            drafts[(int(d.group(1)), int(d.group(2)))] = float(d.group(3))
    return rows, drafts


# ================================================================== #
# CHUNK 2 -- WHAT IS ALREADY LIVE (the no-op instrument)
# ================================================================== #

def _liveOverrides(corr_path, sport):
    """Load corrections.py's CURRENT _DISTANCE_OVERRIDES_<SPORT> by file path.
    importlib runs the module top-to-bottom, so every appended .update() block
    is replayed and we get the true last-wins value.

    NOTE what this is and is NOT used for. It is used ONLY to count how many
    changes land on keys that have no override yet (the _NEW census line). It
    is NOT the no-op baseline -- see _isNoop. Diffing against this dict is
    exactly the bug that minted 2,371 phantoms."""
    import importlib.util
    attr = f"_DISTANCE_OVERRIDES_{sport.upper()}"
    spec = importlib.util.spec_from_file_location("_live_corr", corr_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(getattr(mod, attr))


def _isNoop(new_value, effective_value):
    """
    Purpose   : would applying new_value change what THE RULER uses?
    Arguments : new_value       -- the verdict's value.
                effective_value -- the distance backfill_normalize ACTUALLY
                                   used for this division (triage's ov=
                                   column), whether it came from an override
                                   or from a fallback.
    Output    : bool.
    THE FIX   : this used to take the live OVERRIDE DICT value. But the loader
                falls back to stored metadata when no override exists, so
                "write stored as an override" read as news while changing
                nothing. Measuring against the effective distance asks the
                only question that matters: will the number the ruler uses
                move? (11.1 fix #1.)
    Note      : None means the loader resolved NO distance at all -- any value
                is news. That is what keeps the genuine cold start working.
    """
    if effective_value is None or not effective_value:
        return False
    return abs(new_value - effective_value) / effective_value <= _NOOP_TOL


# ================================================================== #
# CHUNK 3 -- EVIDENCE (stored distances, titles, raw-time range)
# ================================================================== #

def _storedAnet(cur, divs):
    """
    Purpose : anet's own distance -- meets.distance, a human-entered field.
    Output  : dict (meet, div) -> (distance, meet_name, division, "meets").
    Note    : the 4th element is a PROVENANCE TAG. It rides into the worklist
              so a human reading a borderline row can see WHICH number they
              are being asked to judge. Anet is never re-derived: meets.distance
              and meet_name are genuinely separate fields, so 0.14 governs.
    """
    cur.execute("SELECT div_id, meet_id, distance, meet_name, division "
                "FROM meets WHERE div_id = ANY(%s)", (sorted(divs),))
    return {(m, d): (dist, name, division, "meets")
            for d, m, dist, name, division in cur.fetchall()}


def _titleDistance(div_name):
    """
    Purpose   : re-derive a distance from the division TITLE with today's parser.
    Arguments : div_name -- the blob's own div_name, e.g. "Men 4 Mile Run CC".
    Output    : float metres, or None if the title names no distance.
    Note      : distanceFromEventShort returns a (metres, gender) TUPLE. We take
                [0]; the gender half is recovered elsewhere by _blobGender.
                Its range gate [800, 12000] is a free bonus here -- it refuses
                'Individual Results (0 Mile)' -> 0.0, which is sitting in the
                blob on 16 meets right now.
    """
    if not div_name:
        return None
    try:
        res = distanceFromEventShort(div_name)
    except Exception:
        return None                      # an unparseable title is not evidence
    dist = res[0] if isinstance(res, tuple) else res
    return float(dist) if dist else None


def _repairStored(cached, div_name):
    """
    Purpose   : the blob's distance, re-derived from its own title where the
                title names one.
    Arguments : cached   -- the blob's `distance` field (a cached parse, may be
                            stale).
                div_name -- the blob's `div_name` (the string it was parsed FROM).
    Output    : (distance | None, tag) where tag is "title" or "cached".
    THE IDEA  : this is a REPAIR, not an arbitration. 7.3 -- tfrrs XC distance
                was never stored anywhere; the blob's number is a cached read of
                this very string. The title wins whenever it yields anything
                because `cached` is DOWNSTREAM of it. Where the title names no
                distance ("College Men"), we keep the cache and the division
                goes to a human, exactly as before.
    """
    live = _titleDistance(div_name)
    if live is not None:
        return live, "title"
    return cached, "cached"


def _isRepair(cached, fixed):
    """
    Purpose   : did the re-derive actually CHANGE anything?
    Arguments : cached -- the blob's stored number. fixed -- the title's number.
    Output    : bool.
    Why       : a title that reproduces the cache is a CONFIRMATION, not a
                repair, and counting it as one would inflate the ledger into
                meaninglessness. Ledgers only earn trust when they count the
                thing their label claims.
    """
    if not cached or fixed is None:
        return False
    return abs(fixed - cached) / cached > _REPAIR_TOL


def _blobRows(cur, meets):
    """
    Purpose : raw (meet_id, div_str, info) triples out of the jsonb blobs.
    Output  : a generator of (meet_id, div_str, info_dict).
    Note    : jsonb keys are ALWAYS strings (2.4), so div_str is '0','1',...
              and must be int()'d before it can key against a row's div_id.
              Split out from _storedTfrrs so that function can be about the
              REPAIR and not about jsonb shape.
    """
    cur.execute("SELECT meet_id, division_distances FROM meets_tfrrs "
                "WHERE meet_id = ANY(%s) AND division_distances IS NOT NULL",
                (sorted(meets),))
    for meet_id, blob in cur.fetchall():
        for div_str, info in (blob or {}).items():
            if isinstance(info, dict):
                yield meet_id, div_str, info


def _blobEntry(meet_id, div_str, info):
    """
    Purpose   : one blob entry -> (key, distance, div_name, tag, was_repaired).
    Arguments : the triple out of _blobRows.
    Output    : the tuple above, or None if the entry is unusable.
    Note      : the try/except covers BOTH the float() and the int(div_str) --
                a blob written by a future scraper version with a non-numeric
                key should skip the entry, not kill the run.
    """
    try:
        cached = info.get("distance")
        cached = float(cached) if cached is not None else None
        key = (meet_id, int(div_str))
    except (TypeError, ValueError):
        return None

    div_name = info.get("div_name")
    dist, tag = _repairStored(cached, div_name)
    return key, dist, div_name, tag, _isRepair(cached, dist if tag == "title"
                                               else None)


def _storedTfrrs(cur, meets):
    """
    Purpose : tfrrs XC's own distance, with the stale cached parse repaired.
    Output  : dict (meet, div) -> (distance, div_name, None, tag).
    Ledger  : prints how many divisions were carrying a stale parse. THIS LINE
              IS THE TEST. If it reads 0 re-derived across a large crop, the
              import above is not doing what this file assumes and NOTHING
              downstream changes -- a safe failure that announces itself.
    """
    out, repaired, kept = {}, 0, 0
    for meet_id, div_str, info in _blobRows(cur, meets):
        entry = _blobEntry(meet_id, div_str, info)
        if entry is None:
            continue
        key, dist, div_name, tag, was_repaired = entry
        out[key] = (dist, div_name, None, tag)
        if was_repaired:
            repaired += 1
        else:
            kept += 1

    print(f"[stored tfrrs] {repaired:,} distances RE-DERIVED from div_name "
          f"(stale cached parse), {kept:,} unchanged")
    return out


def _timeRanges(cur, table, pairs):
    """min/max raw time per division, one VALUES join (no scans)."""
    values = ",".join(cur.mogrify("(%s,%s)", p).decode() for p in pairs)
    cur.execute(f"""
        SELECT r.meet_id, r.div_id, min(r.time_seconds), max(r.time_seconds)
        FROM {table} r
        JOIN (VALUES {values}) v(m, d) ON v.m = r.meet_id AND v.d = r.div_id
        WHERE r.time_seconds > 0 AND r.time_seconds < 90000
        GROUP BY r.meet_id, r.div_id
    """)
    return {(m, d): (lo, hi) for m, d, lo, hi in cur.fetchall()}


# ================================================================== #
# CHUNK 4 -- PHYSICS
# ================================================================== #

def _physics(lo, hi, dist):
    """
    Purpose : does this distance produce physically possible paces?
    Output  : bool. None/0 distance -> False, which is what makes a missing
              override read as "the current value fails" and lets stored win.
    Caveat  : the rails are WIDE (230..1020 s/mi = a 4.4x band), so PASSING is
              weak evidence. It is precisely why 969 landed AMBIGUOUS, and why
              rule 1's second witness (the draft) is load-bearing.
    """
    if not dist or not lo or not hi:
        return False
    miles = dist / _MILE
    return (lo / miles) >= _WINNER_FLOOR and (hi / miles) <= _TAIL_CEIL


def _sameAnswer(a, b):
    """Two candidates within _SAME_TOL are not in conflict."""
    if not a or not b:
        return False
    return abs(a - b) / a <= _SAME_TOL


# ================================================================== #
# CHUNK 5 -- VERDICTS
# ================================================================== #

def _refutedShape(cat, iqr):
    """
    Purpose   : did the whole field move as ONE common factor?
    Arguments : cat -- triage's classifier verdict (UNIFORM_NEG, PARTIAL, ...).
                iqr -- the division's gap-IQR in percent, or None if the log
                       predates the iqr column.
    Output    : bool.
    Note      : both conditions are required and they are NOT redundant.
                `cat` answers "did everyone move?"; `iqr` answers "did everyone
                move by the SAME AMOUNT?" A field can read UNIFORM while being
                internally sloppy, and a sloppy field is not one factor -- it is
                two populations, or noise. Unknown spread is not tight spread.
    """
    if cat not in _UNIFORM_SHAPES:
        return False
    if iqr is None:
        return False
    return iqr < _NORMAL_IQR


def _fieldRefutes(info, s_dist, draft, d_ok):
    """
    Purpose   : has the field contradicted the label strongly enough that the
                draft should displace it?
    Arguments : info   -- the parsed classifier row (ov, cat, iqr, n, ...).
                s_dist -- the stored distance (metadata).
                draft  -- the inversion's snapped proposal.
                d_ok   -- does the draft pass the physics rails?
    Output    : bool. True means EMIT THE DRAFT.

    THE GATES, each named by what it rules out:

      1. draft exists and passes physics
           W2 rules candidates OUT, never IN. The band is 4.4x wide (11.4), so
           this is a floor, not evidence. NOTE what is deliberately ABSENT: we
           do NOT require stored to FAIL physics. That was the old rule-3 guard
           and it is why 969 landed AMBIGUOUS -- almost nothing fails a 4.4x
           band. Meet 5431's 8000m reads 241 s/mile, clears the 230 rail, and is
           still impossible. Physics may acquit the draft; it may never convict
           the label.

      2. stored ~= effective
           Metadata and ruler tell the SAME story. If they DISAGREE that is rule
           4's job -- a human decides between two live claims. This rule is only
           for when both agree AND the field says both are wrong.

      3. draft differs from the ruler by more than _AGREE_TOL
           Symmetric with rule 1: a draft AGREEING within 8% is a concurrence,
           not a refutation. Below that we are inside where mud and hills live.

      4. the shape is one common factor  -- _refutedShape, the load-bearing gate.

    VALIDATION (7/17): 140140/582873, 139475/579096, 84683/333086 -- stored
    5000, drafted 6000, all three pages say 6K. ln(6000/5000) = 18.2%; the
    cluster measures -17% to -19.5% across ~130 divisions and BOTH sources.
    Attenuation biases the implied distance TOWARD the label (0.14: implieds are
    POINTERS), so the true error is if anything larger. Three pages, one
    mechanism, no shared code with the inversion. W4 -- external, and the only
    witness this rule's own arithmetic could not have manufactured.

    ⚠ SCOPE: the pages validated 5000->6000, ~130 of the ~1,100 this rule will
      emit. The other transitions (2414->3000, 3218->4000, 1609->2000) are the
      same machinery on unverified ground, and 0.14 still says stored beats
      draft ~4:1. spot_check_overrides before applying.
    """
    ov = info["ov"]

    if info.get("fan"):                       # gate 0: anomalous race, not a
        return False                          #   distance error -> worklist
    if not draft or not d_ok:                 # gate 1
        return False
    if not s_dist or not ov:                  # nothing to refute
        return False
    if not _sameAnswer(s_dist, ov):           # gate 2 -> rule 4's territory
        return False
    if abs(draft - ov) / ov <= _AGREE_TOL:    # gate 3
        return False

    return _refutedShape(info["cat"], info.get("iqr"))   # gate 4


def _verdict(info, stored, draft, times):
    """One division -> (verdict, value, note). Rules in order of evidential
    strength. Every branch that ACCEPTS a value must be able to say why the
    rejected candidate is wrong -- not merely that it differs.

    UNCHANGED from 7/15. The rules were never the problem: rule 1 was being
    fed a `stored` that a dead parser had mangled, and the no-op test was
    diffing against the wrong baseline. Fix the inputs, the rules work."""
    lo, hi = times if times else (None, None)
    ov = info["ov"]                       # the EFFECTIVE distance; None = none
    s_dist = stored[0] if stored else None
    s_ok = _physics(lo, hi, s_dist)
    o_ok = _physics(lo, hi, ov)           # None -> False
    d_ok = _physics(lo, hi, draft) if draft else False

    # 1. Metadata and fresh statistics concur -> strongest evidence short of
    #    the page. Two independent sources, neither bracket-dependent. The
    #    draft never saw the title; the title never saw a residual.
    if s_dist and s_ok and draft and abs(s_dist - draft) / s_dist <= _AGREE_TOL:
        return "AGREE", s_dist, "stored+draft concur"

    # 2. Stored is possible and the effective distance is not -- INCLUDING the
    #    case where the ruler resolved nothing at all. That second case is the
    #    genuine cold start. Note this verdict is now frequently filtered as a
    #    NOOP downstream: if stored IS what the ruler already used, restating it
    #    is not a repair. The verdict is honest; the write is what was wrong.
    if s_dist and s_ok and not o_ok:
        why = ("ruler resolved no distance; stored passes physics"
               if ov is None else "stored passes physics; current ov does not")
        return "STORED", s_dist, why

    # 3. No metadata at all; the draft is possible and the current value is not.
    #    The fan gate applies here too: a division whose residuals fan is an
    #    anomalous race, and a distance draft cannot explain per-runner scatter
    #    even when there is no metadata to contradict.
    if not s_dist and draft and d_ok and not o_ok and not info.get("fan"):
        return "DRAFT", draft, "no metadata; post-fix draft passes physics"

    # 3b. THE FIELD REFUTES THE LABEL. (2026-07-17)
    #     Stored and the ruler concur; the whole field, with no spread, says
    #     they are both wrong; the draft describes a race a human could run.
    #     Rule 1 cannot reach this -- AGREE needs stored and draft to CONCUR,
    #     and here their disagreement IS the finding. Placed before rule 4 for
    #     readability only: rule 4 requires stored and ov to DISAGREE, so the
    #     two can never both fire.
    if _fieldRefutes(info, s_dist, draft, d_ok):
        return "REFUTED", draft, (
            f"field ({info['cat']}, iqr={info['iqr']:.1f} < {_NORMAL_IQR}) "
            f"moved as ONE factor against stored {s_dist:.0f}; "
            f"draft {draft:.0f} passes physics")

    # 4. WAS THE REVERTER. Both stored and ov survive the (wide) physics band
    #    but disagree. Physics cannot discriminate, so nothing here justifies
    #    overwriting a deliberate override with metadata. Send it to a human.
    if s_dist and s_ok and o_ok and not _sameAnswer(s_dist, ov):
        return "AMBIGUOUS", None, (
            f"stored {s_dist:.0f} and ov {ov:.0f} BOTH pass physics "
            f"(band is 4.4x) -- physics cannot decide; page-check")

    return "WORKLIST", None, "evidence inconclusive -> page-check"


# ================================================================== #
# CHUNK 6 -- ORCHESTRATION + OUTPUT
# ================================================================== #

def _bump(census, key):
    """One counter, one place. Keeps _classify about logic, not bookkeeping."""
    census[key] = census.get(key, 0) + 1


def _worklistRow(key, info, stored, draft, note):
    """
    Purpose   : one human-queue row.
    Output    : a flat tuple matching _writeWorklist's header.
    Note      : `tag` (stored[3]) rides along so the reader can see whether the
                stored number came from the title, the cache, or meets. A human
                asked to page-check a division needs to know which number is on
                trial -- otherwise they re-check the machine's arithmetic
                instead of the human's claim.
    """
    s_dist = stored[0] if stored else None
    s_name = (stored[1] or "")[:40] if stored else ""
    s_tag = stored[3] if stored and len(stored) > 3 else ""
    return (key, info["cat"], info["ov"], s_dist, s_name, s_tag, draft, note)


def _classify(rows, drafts, stored, times, live):
    """Walk every division -> (changes, noops, worklist, census).
    `changes` is the only thing worth writing: verdicts that move the number
    THE RULER USES."""
    changes, noops, worklist, census = {}, {}, [], {}
    for key, info in sorted(rows.items()):
        v, value, note = _verdict(info, stored.get(key), drafts.get(key),
                                  times.get(key))
        _bump(census, v)

        if v not in ("AGREE", "STORED", "DRAFT", "REFUTED"):
            worklist.append(_worklistRow(key, info, stored.get(key),
                                         drafts.get(key), note))
            continue

        # THE NO-OP TEST. info["ov"] is the EFFECTIVE distance -- what the
        # backfill actually used. Diffing against `live` (the override dict)
        # is what minted 2,371 phantoms: every cold-start STORED verdict read
        # as news while restating the fallback it "replaced". (11.1 fix #1.)
        if _isNoop(value, info["ov"]):
            noops[key] = (value, v)
            _bump(census, "_NOOP")
        else:
            changes[key] = (value, v, note)
            _bump(census, "_CHANGE")
            if key not in live:
                _bump(census, "_NEW")
    return changes, noops, worklist, census


def _writeRepairs(path, changes):
    """Emit ONLY real changes, under the name apply_triage._MERGES expects.
    do_merge.py rewrites this into distance_override_<sport>.py; the variable
    name must stay _DISTANCE_OVERRIDES_ADDITIONS or apply_triage NameErrors and
    rolls back (the bug that hid all day on 7/15)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("# GENERATED by adjudicate_regressions.py -- repairs that\n"
                "# MOVE THE EFFECTIVE DISTANCE. Verdicts that merely restate\n"
                "# what the ruler already used are filtered as no-ops; see the\n"
                "# console census for how many.\n"
                "_DISTANCE_OVERRIDES_ADDITIONS = {\n")
        for (m, d), (value, v, note) in sorted(changes.items()):
            f.write(f"    ({m}, {d}): {value},  # {v}: {note}\n")
        f.write("}\n")


def _writeWorklist(path, worklist):
    with open(path, "w", encoding="utf-8") as f:
        f.write("meet\tdiv\tcategory\teffective\tstored\tmeet_name\t"
                "stored_from\tdraft\treason\n")
        for (m, d), cat, ov, s_dist, name, tag, draft, note in worklist:
            f.write(f"{m}\t{d}\t{cat}\t{ov}\t{s_dist}\t{name}\t{tag}\t"
                    f"{draft}\t{note}\n")


def _report(census, changes):
    verdicts = {k: v for k, v in census.items() if not k.startswith("_")}
    print("\nverdicts:", {k: verdicts[k] for k in sorted(verdicts)})
    print(f"\n  CHANGE : {census.get('_CHANGE', 0):>6,}  <- repairs that MOVE "
          f"the effective distance")
    print(f"    of which NEW keys : {census.get('_NEW', 0):>6,}  <- divisions "
          f"with no override at all")
    print(f"  NOOP   : {census.get('_NOOP', 0):>6,}  <- verdicts that restate "
          f"what the ruler already used")

    if not changes:
        print("\n  !! ZERO real changes. A cycle CANNOT move the ruler on this\n"
              "     crop. Go to the worklist / a wider z-cut.")
    else:
        print(f"\n  a cycle is worth running: {len(changes):,} values will move.")


def _loadEvidence(cur, sport, rows):
    """
    Purpose   : every DB-side input _classify needs, in one place.
    Arguments : cur, sport, rows -- the parsed classifier log.
    Output    : (stored, times).
    Note      : tfrrs .update()s over anet DELIBERATELY. A tfrrs row's div_id is
                a per-meet LOCAL index (0.2) whose integer collides with anet
                div_ids by coincidence; where both resolve, the blob keyed
                (meet, div) is the composite one and wins.
    """
    stored = _storedAnet(cur, [d for _, d in rows])
    stored.update(_storedTfrrs(cur, [m for m, _ in rows]))
    times = _timeRanges(cur, _TABLE[sport], list(rows))
    return stored, times


def main():
    ap = argparse.ArgumentParser(description="Bulk-adjudicate flagged "
                                 "divisions via metadata + physics.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--log", required=True,
                    help="triage_regressions Tee log (verdict table + drafts)")
    ap.add_argument("--dir", default="scripts")
    ap.add_argument("--corrections", default="engine/corrections.py",
                    help="live corrections.py, for the _NEW census line")
    args = ap.parse_args()

    rows, drafts = _readLog(args.log)
    n_no_ov = sum(1 for r in rows.values() if r["ov"] is None)
    print(f"parsed {len(rows):,} divisions, {len(drafts):,} drafts "
          f"from {args.log}")
    print(f"  {n_no_ov:,} have NO effective distance (cold start), "
          f"{len(rows) - n_no_ov:,} the ruler resolved")
    if not rows:
        sys.exit("nothing parsed -- wrong file?")

    live = _liveOverrides(args.corrections, args.sport)
    print(f"live _DISTANCE_OVERRIDES_{args.sport.upper()}: {len(live):,}")

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        stored, times = _loadEvidence(cur, args.sport, rows)
        conn.rollback()                       # read-only, always
    print(f"stored metadata found for {sum(1 for k in rows if k in stored):,}"
          f"/{len(rows):,}; time ranges for {len(times):,}")

    changes, noops, worklist, census = _classify(rows, drafts, stored,
                                                 times, live)
    _report(census, changes)

    out_py = os.path.join(args.dir,
                          f"adjudicated_overrides_{args.sport.lower()}.py")
    _writeRepairs(out_py, changes)
    print(f"\nwrote {out_py}  ({len(changes):,} real repairs)")

    out_tsv = os.path.join(args.dir,
                           f"adjudication_worklist_{args.sport.lower()}.tsv")
    _writeWorklist(out_tsv, worklist)
    print(f"wrote {out_tsv}  ({len(worklist):,} for the human queue)")


if __name__ == "__main__":
    main()