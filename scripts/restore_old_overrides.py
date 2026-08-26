# Project: xc-predictor / scripts
# File:    restore_old_overrides.py
# Purpose: Bring back the distance corrections an old corrections.py knew
#          and the current one lost -- downward-only, fill-only.
#
#     python scripts/restore_old_overrides.py                # report
#     python scripts/restore_old_overrides.py --rev 3801dd1  # pick the era
#     python scripts/restore_old_overrides.py --write        # append
#
# ★ WHY THIS EXISTS. The -Reset wipe clears every regenerable override and
#   trusts the passes to re-propose the right ones; 2026-08-27's 05c_lost
#   counted 2,211 that nothing re-proposed. Owner's call: apply all the old
#   corrections, as long as they tune distance DOWN. This recovers them from
#   git history (default --rev 3801dd1, the last flattened commit -- it
#   carries the July page-verified triage) and emits them as a
#   _DISTANCE_RESTORED_* block.
#
# ★ FILL-ONLY, NEVER A FIGHT. A restored entry reaches the appliers through
#   setdefault: it fills a key nothing currently speaks for and never
#   overrides a live proposal or hand entry. Downward-only is enforced at
#   apply time regardless (backfill's clamp and LEAST in the SQL join), so
#   an old upward entry is inert -- but obviously-absurd values are dropped
#   here anyway.
#
# ⚠ SURVIVES THE WIPE BY NAME. wipe_overrides clears only the _REGENERATED
#   names and its block re-seeds _DISTANCE_RESTORED_* afterwards (see the
#   template in scripts/wipe_overrides.py), so a restoration outlives every
#   future reset until a proposal or hand entry claims the key.

import argparse
import io
import os
import subprocess
import sys

PATH = os.path.join("engine", "corrections.py")
MARK = "# === RESTORED FROM HISTORY (scripts/restore_old_overrides.py) ==="

_SANE = (400.0, 25_000.0)


def _collect(source_text, label):
    """{(meet, div): distance} resolved the way dump_overrides resolves --
    every tuple-keyed dict in the module, named dicts winning conflicts."""
    env = {}
    print(f"  exec {label} ({len(source_text) / 1e6:.0f} MB source)...")
    exec(compile(source_text, label, "exec"), env)     # noqa: S102
    rank = {"DIST_PROPOSED": 0, "_DISTANCE_OVERRIDES_XC": 1}
    found = {}
    for name, obj in env.items():
        if not isinstance(obj, dict):
            continue
        for k, v in obj.items():
            if (isinstance(k, tuple) and len(k) == 2
                    and all(isinstance(x, int) for x in k)
                    and isinstance(v, (int, float)) and v > 0):
                found.setdefault(k, {})[name] = float(v)
    out = {}
    for key, byname in found.items():
        winner = min(byname, key=lambda n: (rank.get(n, 99), n))
        out[key] = byname[winner]
    # the drops too: a key the old file DROPPED must not come back as a
    # distance from an even older layer of itself
    drops = set()
    for name, obj in env.items():
        if isinstance(obj, (set, frozenset)) and "_DISTANCE_DROP" in name:
            drops |= {k for k in obj
                      if isinstance(k, tuple) and len(k) == 2}
    return out, drops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default="3801dd1",
                    help="git revision whose corrections.py to restore from "
                         "(default: the last flattened commit, which carries "
                         "the July page-verified triage)")
    ap.add_argument("--from-snap", action="store_true", dest="from_snap",
                    help="ALSO restore from dist_override_snap -- the exact "
                         "values the last reset's 05c_lost counted, still in "
                         "the database until the next run's snapshot")
    ap.add_argument("--write", action="store_true",
                    help="append the block to engine/corrections.py")
    args = ap.parse_args()

    old_src = subprocess.run(
        ["git", "show", f"{args.rev}:engine/corrections.py"],
        capture_output=True, text=True, check=True).stdout
    old, old_drops = _collect(old_src, f"corrections@{args.rev}")
    cur_src = io.open(PATH, encoding="utf-8").read()
    if MARK in cur_src:
        print("\n  a restored block is already in place -- remove it before "
              "writing another (this tool never stacks two).")
        if args.write:
            return 1
    cur, cur_drops = _collect(cur_src, "corrections@working-tree")

    # ★ THE SNAPSHOT IS THE SHARPEST SOURCE. override_diff's 01c copy holds
    #   the pre-wipe table verbatim -- the exact 2,211 entries 05c_lost
    #   counted -- but only until the NEXT run's snapshot overwrites it.
    #   Git history reaches further back; the snap reaches last night.
    if args.from_snap:
        from database import getConn                 # noqa: PLC0415
        with getConn() as conn, conn.cursor() as c:
            c.execute("SELECT to_regclass('dist_override_snap')")
            if c.fetchone()[0] is None:
                print("  --from-snap: no dist_override_snap table; skipping")
            else:
                c.execute("""SELECT s.meet_id, s.div_id, s.distance
                             FROM dist_override_snap s""")
                n_snap = 0
                for m, d, dist in c.fetchall():
                    key = (int(m), int(d))
                    if key not in old:
                        old[key] = float(dist)
                        n_snap += 1
                print(f"  --from-snap: {n_snap:,} keys added from the "
                      "pre-wipe snapshot")

    restored, skipped_sane, skipped_dropped = {}, 0, 0
    for key, dist in old.items():
        if key in cur or key in cur_drops or key in old_drops:
            skipped_dropped += (key in cur_drops or key in old_drops)
            continue
        if not (_SANE[0] <= dist <= _SANE[1]):
            skipped_sane += 1
            continue
        restored[key] = dist

    print(f"\n  old resolution: {len(old):,} keys   current: {len(cur):,}")
    print(f"  restorable (old knows, current is silent): {len(restored):,}")
    print(f"  skipped: {skipped_sane} outside {_SANE[0]:.0f}-{_SANE[1]:.0f}m,"
          f" {skipped_dropped} dropped divisions")
    for k, v in sorted(restored.items())[:15]:
        print(f"    {k}: {v:g}")
    if len(restored) > 15:
        print(f"    ... and {len(restored) - 15:,} more")

    if not restored:
        print("  nothing to restore.")
        return 0
    if not args.write:
        print("\n  DRY RUN -- pass --write to append. Then:")
        print("    python engine\\dump_overrides.py")
        print("    .\\run_pipeline.ps1 -From 05")
        return 0

    lines = [f"\n{MARK}",
             f"# {len(restored):,} entries recovered from "
             f"corrections.py@{args.rev} (owner's call, 2026-08-27:",
             "# apply all old corrections that tune distance down). "
             "setdefault = fill",
             "# only; a live proposal, hand entry or drop always wins. The "
             "wipe block",
             "# re-seeds this dict, so restorations survive resets.",
             "_DISTANCE_RESTORED_XC = {"]
    for k, v in sorted(restored.items()):
        lines.append(f"    {k}: {v:g},")
    lines += ["}",
              "for _rk, _rv in _DISTANCE_RESTORED_XC.items():",
              "    _DISTANCE_OVERRIDES_XC.setdefault(_rk, _rv)",
              f"# === END RESTORED FROM HISTORY ===\n"]
    io.open(PATH, "a", encoding="utf-8", newline="\n").write("\n".join(lines))
    print(f"\n  appended {len(restored):,} entries to {PATH}")
    print("  now: python engine\\dump_overrides.py, then a -From 05 rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
