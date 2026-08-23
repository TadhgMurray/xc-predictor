# Project: xc-predictor / scripts
# File:    peek_override.py
# Purpose: READ ONLY. For one or more (meet_id, div_id) keys, print what EVERY
#          corrections table holds for them -- not just the distance one.
#
#   python scripts/peek_override.py 268748/1068877
#   python scripts/peek_override.py 268748/1068877 62885/273791 --sport XC
#   python scripts/peek_override.py --meet 268748          # every div at a meet
#
# ★ ALL FIVE TABLES, BECAUSE "IS THERE AN OVERRIDE ON IT" IS FIVE QUESTIONS.
#   A meet can be wrong in ways a distance cannot fix: the wrong distance, a
#   division that should not be rated at all, individual results that should
#   not, the wrong gender, or a bad time on one row. Each has its own table,
#   and the old version of this file read one of them against six keys that
#   were hardcoded in 2026. Asking about a meet and being told only about its
#   distance is how "there is no override on it" gets said about a meet that
#   has three.
#
# ⚠ THIS READS corrections.py, NOT THE DATABASE. It answers "what did we
#   decide", not "what did the backfill do with it". For the second question
#   -- an override that is set and not reaching the rows -- use
#   diag_override_check.py, which backs the effective distance out of the
#   ratings themselves.

import argparse
import sys

sys.path.insert(0, "engine")


def _tables(sport):
    """Every (name, dict) a (meet_id, div_id) key can appear in."""
    import corrections as C
    xc = sport == "XC"
    return [
        ("distance override",
         C._DISTANCE_OVERRIDES_XC if xc else C._DISTANCE_OVERRIDES_TF),
        ("distance DROP",
         C._DISTANCE_DROP_XC if xc else C._DISTANCE_DROP_TF),
        ("result DROP",
         C._RESULT_DROP_XC if xc else C._RESULT_DROP_TF),
        ("gender override",
         C._GENDER_OVERRIDES_XC if xc else C._GENDER_OVERRIDES_TF),
        ("result override",
         C._RESULT_OVERRIDE_XC if xc else C._RESULT_OVERRIDE_TF),
    ]


def _parseKey(text):
    """'268748/1068877' or '268748,1068877' -> (268748, 1068877)."""
    parts = text.replace(",", "/").split("/")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"{text!r}: want MEET/DIV, e.g. 268748/1068877")
    return tuple(int(p) for p in parts)


def _lookup(table, key):
    """The value under `key`, tolerating int/str key shapes.

    Some tables were written with string div_ids and some with ints; a miss
    on one shape and a hit on the other is the difference between "no
    override" and "an override you could not see".
    """
    meet, div = key
    for k in ((meet, div), (str(meet), str(div)), (meet, str(div)),
              (str(meet), div)):
        try:
            if k in table:
                return table[k], k
        except TypeError:
            pass
    return None, None


def main():
    ap = argparse.ArgumentParser(
        description="What every corrections table holds for a meet/div. "
                    "Reads corrections.py; writes nothing.")
    ap.add_argument("keys", nargs="*", type=_parseKey,
                    help="MEET/DIV, e.g. 268748/1068877")
    ap.add_argument("--meet", type=int, action="append",
                    help="every division of this meet that appears anywhere")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    args = ap.parse_args()

    tables = _tables(args.sport)

    keys = list(args.keys)
    for meet in args.meet or []:
        found = set()
        for _name, table in tables:
            for k in table:
                try:
                    if int(k[0]) == meet:
                        found.add((int(k[0]), int(k[1])))
                except (TypeError, ValueError, IndexError):
                    continue
        if not found:
            print(f"\n  meet {meet}: no division of it appears in any "
                  f"{args.sport} corrections table.")
        keys.extend(sorted(found))

    if not keys:
        if args.meet:
            # --meet already said which meets came up empty; that IS the
            # answer, not a usage error.
            return 0
        ap.error("give at least one MEET/DIV or --meet")

    for key in keys:
        print(f"\n  {key[0]}/{key[1]}  ({args.sport})")
        hits = 0
        for name, table in tables:
            value, shape = _lookup(table, key)
            if value is None:
                continue
            hits += 1
            note = "" if shape == key else f"   [stored as {shape}]"
            print(f"    {name:<18} {value}{note}")
        if not hits:
            print("    nothing. No table in corrections.py mentions this "
                  "meet/div.")

    print("\n  Reads corrections.py only -- this is what was DECIDED, not "
          "what\n  the backfill did with it. For the second question use\n"
          "  scripts/diag_override_check.py.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
