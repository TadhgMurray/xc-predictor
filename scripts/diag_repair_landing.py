#!/usr/bin/env python3
# ============================================================================
# diag_repair_landing.py  --  did the pass-4 override repairs land, and what
#                             are the 317 still-flagging divisions?
# ----------------------------------------------------------------------------
# THE IDEA (read this before the code)
#
#   The pass-4 apply printed "_DISTANCE_OVERRIDES_XC 2,372 -> 2,372 (+0)".
#   A +0 is AMBIGUOUS: it happens both when every repair overwrote a key in
#   place (good) and when repairs were appended as a second copy (bad). A grep
#   proved a key exists TWICE. But corrections.py turns out to be a small BASE
#   LITERAL plus programmatic `.update()` merges, so "which value wins" is a
#   runtime question a grep can't answer. This script loads corrections.py the
#   way the engine does and reads the values the engine truly sees.
#
#   FOUR read-only questions (imports corrections.py, reads one TSV):
#     Q1  For known repaired keys, is the LIVE value the repair or the stale
#         one?  (Also: is 33116/143724 still an OVERRIDE when it should be a
#         DROP -- a leftover-key check.)
#     Q2  Which keys are physically DUPLICATED in the base literal, and what is
#         each one's LIVE value?  (A stale twin whose value survives is a bug.)
#     Q3  Of the still-flagging overridden divisions (~317), how many are
#         div_id == 0 (NODIV / mixed -- unfixable by a division override) vs
#         genuinely wrong-valued?
#     Q4  Which keys sit in BOTH the override dict AND the distance-drop set?
#         (= move-to-drop that never stripped the override -- cause of cause 1.)
#
#   Touches no database. Safe to run anytime.
# ============================================================================

import argparse
import ast
import importlib.util
import re
from pathlib import Path


# ----------------------------------------------------------------------------
# LOADING THE LIVE STRUCTURES  --  "what the engine sees"
# ----------------------------------------------------------------------------
def _loadModuleFaithful(path):
    """
    Purpose : import corrections.py by path and run it, so what we read
              includes the base literal PLUS any later .update() merges --
              byte-for-byte what the engine gets.
    Arguments: path -- str/Path to engine/corrections.py.
    Output  : module object, or None if executing it raised (caller falls
              back to the AST reader).
    """
    try:
        spec = importlib.util.spec_from_file_location("corrections_live", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)          # runs the file once
        return module
    except Exception as exc:
        print(f"  [warn] faithful import failed ({exc!r}); AST fallback")
        return None


def _attrOrEmpty(module, name, default):
    """
    Purpose : fetch an attribute if the module exposes it, else a default --
              lets Q4 tolerate a drop set that may be named differently.
    Arguments:
      module  -- imported module (or None).
      name    -- attribute name to try.
      default -- what to return when absent.
    Output  : the attribute value or `default`.
    """
    if module is None:
        return default
    return getattr(module, name, default)


def _liveOverridesAst(path, sport):
    """
    Purpose : fallback that reads only the `_DISTANCE_OVERRIDES_<sport> = {...}`
              literal without executing the module.
    Arguments: path, sport.
    Output  : dict (meet, div) -> distance. Reproduces last-wins because
              literal_eval builds the dict in source order.
    """
    name = f"_DISTANCE_OVERRIDES_{sport.upper()}"
    tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if name in names and isinstance(node.value, ast.Dict):
                return ast.literal_eval(node.value)
    raise KeyError(f"{name} not found as a top-level dict literal")


# ----------------------------------------------------------------------------
# Q1  SPOT-CHECK KNOWN KEYS
# ----------------------------------------------------------------------------
def _checkSampleKeys(liveDict, sampleKeys):
    """
    Purpose : print the LIVE value for each known key.
    Arguments:
      liveDict   -- override dict.
      sampleKeys -- list of (meet, div, expected). expected=None means the key
                    should be ABSENT (e.g. moved to a DROP list).
    Output  : count of surprises.
    """
    print("Q1  live value of known keys")
    surprises = 0
    for meet, div, expected in sampleKeys:
        present = (meet, div) in liveDict
        live = liveDict.get((meet, div))
        if expected is None:
            ok = not present
            note = "absent (correct)" if ok else f"STILL PRESENT = {live}  <- leftover key"
        else:
            ok = present and live == expected
            note = f"live={live}  expected={expected}"
        surprises += 0 if ok else 1
        print(f"    {'ok ' if ok else '!! '}({meet}, {div}): {note}")
    print(f"    -> {surprises} surprise(s)\n")
    return surprises


# ----------------------------------------------------------------------------
# Q2  DUPLICATED KEYS IN THE BASE LITERAL, WITH THEIR LIVE VALUES
# ----------------------------------------------------------------------------
def _blockLines(path, dictName):
    """
    Purpose : yield only the lines INSIDE `dictName = { ... }` (brace-depth
              scan), so the key count ignores other dicts.
    Arguments: path, dictName.
    Output  : generator of source lines between the opening `{` and its match.
    """
    opener = re.compile(rf"^\s*{re.escape(dictName)}\s*=\s*\{{")
    inside, depth = False, 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not inside:
            if opener.match(line):
                inside, depth = True, line.count("{") - line.count("}")
            continue
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            break
        yield line


def _reportDuplicateKeys(path, dictName, liveDict, flagged):
    """
    Purpose : list keys that appear >1 time in the base literal, and for each
              print its LIVE value and whether it is in the flagging set -- a
              duplicate whose surviving value is wrong is a real bug.
    Arguments:
      path, dictName -- as above.
      liveDict       -- override dict (for the live value).
      flagged        -- set of flagging (meet, div), or None if unavailable.
    Output  : list of duplicated keys.
    """
    keyLine = re.compile(r"^\s*\((-?\d+),\s*(-?\d+)\)\s*:")
    counts = {}
    for line in _blockLines(path, dictName):
        m = keyLine.match(line)
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            counts[key] = counts.get(key, 0) + 1
    dupes = [k for k, n in counts.items() if n > 1]
    print(f"Q2  duplicate keys inside {dictName}")
    print(f"    {len(counts)} distinct base-literal keys, {len(dupes)} duplicated")
    for k in dupes:
        live = liveDict.get(k)
        tag = ""
        if flagged is not None:
            tag = "  <- STILL FLAGGING" if k in flagged else "  (not flagging)"
        print(f"      {k}  x{counts[k]}  live={live}{tag}")
    print()
    return dupes


# ----------------------------------------------------------------------------
# Q3  BUCKET THE STILL-FLAGGING OVERRIDDEN DIVISIONS
# ----------------------------------------------------------------------------
def _findCol(header, *candidates):
    """
    Purpose : return the index of the first header cell matching any candidate
              name (case-insensitive), so column layout changes don't break us.
    Arguments:
      header     -- list of header cell strings.
      candidates -- names to try in order, e.g. "meet_id", "meet".
    Output  : int index, or None if none match.
    """
    lowered = [h.strip().lstrip("# ").lower() for h in header]
    for cand in candidates:
        if cand in lowered:
            return lowered.index(cand)
    return None


def _readFlaggedDivs(tsvPath):
    """
    Purpose : read (meet, div) of every flagged division from the diag
              worksheet, tolerant of meet_id/div_id or meet/div naming.
    Arguments: tsvPath -- scripts/suspects_div_xc.tsv.
    Output  : set of (meet, div), or None if unreadable (Q3 then skipped).
    """
    p = Path(tsvPath)
    if not p.exists():
        return None
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return None
    header = lines[0].split("\t")
    mi = _findCol(header, "meet_id", "meet")
    di = _findCol(header, "div_id", "div")
    if mi is None or di is None:
        print(f"  [warn] header={[h.strip() for h in header]}; no meet/div -> skip Q3")
        return None
    flagged = set()
    for row in lines[1:]:
        cells = row.split("\t")
        if len(cells) <= max(mi, di):
            continue
        meetRaw, divRaw = cells[mi].strip(), cells[di].strip()
        if not meetRaw:
            continue
        div = 0 if divRaw in ("", "-", "None", "NODIV") else int(divRaw)
        flagged.add((int(meetRaw), div))
    return flagged


def _bucketRegressions(flaggedDivs, overrideKeys):
    """
    Purpose : the ~317 = flagged AND overridden. Split by div_id == 0
              (unfixable mixed bucket) vs the rest (candidate wrong-values).
    Arguments: flaggedDivs (set), overrideKeys (iterable of (meet, div)).
    Output  : None (prints the split + examples).
    """
    regression = sorted(flaggedDivs & set(overrideKeys))
    divZero = [k for k in regression if k[1] == 0]
    divReal = [k for k in regression if k[1] != 0]
    print("Q3  still-flagging overridden divisions (the ~317)")
    print(f"    total {len(regression)}")
    print(f"    div 0 / NODIV (unfixable by a division override): {len(divZero)}")
    for k in divZero[:8]:
        print(f"      {k}")
    print(f"    real div (candidate wrong-value, per-div work): {len(divReal)}")
    for k in divReal[:8]:
        print(f"      {k}")
    print()


# ----------------------------------------------------------------------------
# Q4  OVERRIDE / DROP COLLISIONS  (move-to-drop that never stripped override)
# ----------------------------------------------------------------------------
def _overrideDropCollisions(overrideKeys, dropSet):
    """
    Purpose : keys present in BOTH the override dict and the distance-drop set;
              each is a division that should be dropped but is still being
              re-valued by a stale override (e.g. 33116/143724).
    Arguments:
      overrideKeys -- iterable of (meet, div).
      dropSet      -- set of (meet, div) to be dropped (may be empty).
    Output  : sorted list of colliding keys (printed).
    """
    collisions = sorted(set(overrideKeys) & set(dropSet))
    print("Q4  override / distance-drop collisions (strip these from overrides)")
    print(f"    {len(collisions)} collision(s)")
    for k in collisions[:20]:
        print(f"      {k}")
    print()
    return collisions


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corrections", default="engine/corrections.py")
    ap.add_argument("--sport", default="XC", choices=["XC", "TF"])
    ap.add_argument("--suspects-div", default="scripts/suspects_div_xc.tsv")
    args = ap.parse_args()
    S = args.sport.upper()

    sampleKeys = [
        (11185, 51469, 5000.0),   # STORED repair; canonical check
        (22041, 1,     3000),     # worklist: 'Girls 3k'
        (26787, 1,     8000),     # worklist: '8K Men'
        (19910, 0,     8000),     # worklist: page 'hard 8k' (legit div-0 fix)
        (33116, 143724, None),    # hand-moved to _DISTANCE_DROP_XC -> expect gone
    ]

    module = _loadModuleFaithful(args.corrections)
    if module is not None:
        overrides = getattr(module, f"_DISTANCE_OVERRIDES_{S}")
        faithful = True
    else:
        overrides = _liveOverridesAst(args.corrections, S)
        faithful = False
    how = "executed module (faithful)" if faithful else "AST base-literal only"
    print(f"loaded _DISTANCE_OVERRIDES_{S}: {len(overrides):,} keys [{how}]\n")

    flagged = _readFlaggedDivs(args.suspects_div)

    _checkSampleKeys(overrides, sampleKeys)
    _reportDuplicateKeys(args.corrections, f"_DISTANCE_OVERRIDES_{S}",
                         overrides, flagged)

    if flagged is not None:
        _bucketRegressions(flagged, overrides.keys())
    else:
        print("Q3  skipped (worksheet missing or unrecognised header)\n")

    # Drop set may be named a few ways; try the likely ones, else empty.
    dropSet = _attrOrEmpty(module, f"_DISTANCE_DROP_{S}",
              _attrOrEmpty(module, "_DISTANCE_DROP", set()))
    _overrideDropCollisions(overrides.keys(), dropSet)


if __name__ == "__main__":
    main()