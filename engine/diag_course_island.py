"""
diag_course_island.py -- READ ONLY. Are two hard courses only hard RELATIVE TO
EACH OTHER, with their shared level dragged toward the average course?

Writes nothing.

    python engine/diag_course_island.py --list 40
    python engine/diag_course_island.py --course balboa --course glendoveer
    python engine/diag_course_island.py --course balboa --course glendoveer --sport XC

WHY THIS EXISTS
    Owner, 2026-09-19: "footlocker may be getting underdifficulty because the
    race it's mostly being compared to is NXN, which is already hard, but I
    have a feeling we don't actually take that into account. They should be
    equally hard, but comparing the two makes footlocker look easier, because
    glendoveer has high difficulty."

    The mechanism is real and it is already written down in the engine, one
    line above the damping constant:

        "Two cells whose runners' only other races are at each other form an
         island: D_A = c + D_B and D_B = D_A - c, so a full step swaps them
         back and forth for ever, period two. A half step keeps the island's
         SUM WHERE IT STARTED (the group mean, its prior) and lets the
         difference settle."

    Read that again for what it means about levels rather than convergence:
    for an island pair, the DIFFERENCE between the two courses is identified
    and their SUM IS NOT. The sum stays wherever the prior put it -- and the
    prior is "you are an ordinary course", g_mean, which for XC is about zero.
    So two genuinely hard courses that mostly see each other's athletes are
    shrunk toward ordinary TOGETHER, and no amount of comparing them to each
    other can escape it.

★ WHICH REFINES THE HYPOTHESIS RATHER THAN CONFIRMING IT AS STATED. It is not
  that NXN's hardness makes Foot Locker look easy -- a mutual comparison is
  symmetric and cannot favour one side. What breaks the symmetry is OUTSIDE
  ANCHORING: whichever course has more voters who also raced somewhere else
  keeps its own level, and the other absorbs the shrinkage. Glendoveer is a
  public Portland course that hosts ordinary meets all season; a one-weekend
  national final at a venue that hosts little else has far fewer outside
  witnesses. If that asymmetry is present, the effect lands exactly where the
  owner expects it, by a different route.

WHAT IT MEASURES, per named course
    races, voters      how much evidence the cell has at all
    own weight         w / (w + k), the share of the published number that is
                       the cell's OWN evidence rather than "an ordinary
                       course". This is the shrinkage, as a fraction.
    mutual             the share of its athlete-seasons' OTHER races that are
                       at the other named course(s). High = island.
    outside            the share at courses outside the named set. This is the
                       number that breaks the tie, and the one to compare
                       between the two courses.

⚠ AN UPPER BOUND ON INDEPENDENCE, like diag_connectivity: this keys athletes
  on the pack's athlete code, which is (person_id, pool). Rows of one person
  in two pools count as two athletes, so real outside anchoring is no better
  than what prints here.
"""

import argparse
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                            # noqa: E402
import bracket_engine as be                                     # noqa: E402


# _defaultPack / _defaultState
# Purpose:   The paths run_joint itself uses, asked of run_joint rather than
#            retyped.
# ⚠ I HARD-CODED engine/data/pack.npz IN TWO NEW SCRIPTS AND IT DOES NOT EXIST
#   (2026-09-19). The pack is `packed_XC_TF.npz` -- five other scripts spell it
#   that way and run_joint's own --pack default is authoritative -- so both
#   scripts failed at once and told the reader to rebuild a pack that was
#   already on disk under its real name. Asking the parser removes the chance
#   of a sixth spelling.
def _defaultPack():
    try:
        import run_joint as rj
        return rj.buildParser().get_default("pack")
    except Exception:                                            # noqa: BLE001
        return os.path.join(_ROOT, "engine", "data", "packed_XC_TF.npz")


def _defaultState():
    return os.path.join(_ROOT, "engine", "data",
                        "joint_difficulty_state.npz")


# _matchCells
# Purpose:   The cell indices whose key contains any of the patterns, matched
#            case-insensitively on the key string.
# Arguments: keys -- the course key strings; patterns -- lowercase needles.
# Output:    {pattern: [indices]}.
# ! MATCHED ON THE KEY, NOT ON A MEET NAME. The pack carries course keys, not
#   meet names, so a course is addressed by whatever its key spells. Run with
#   --list first to see the keys; that is why --list exists.
def _matchCells(keys, patterns):
    out = {}
    low = [k.lower() for k in keys]
    for pat in patterns:
        p = pat.lower()
        out[pat] = [i for i, k in enumerate(low) if p in k]
    return out


def _sportMask(cols, sport):
    if sport is None:
        return np.ones(len(np.asarray(cols["course"])), dtype=bool)
    sp = np.asarray(cols["sport"])
    want = 0 if str(sport).upper() == "XC" else 1
    return sp == want


def main():
    ap = argparse.ArgumentParser(
        description="Is a pair of courses an island whose shared level is "
                    "shrunk toward the average course?")
    ap.add_argument("--pack", default=_defaultPack())
    ap.add_argument("--npz", default=_defaultState(),
        help="the solve state, for races_per_cell and the fitted priors")
    ap.add_argument("--course", action="append", default=[],
                    help="a substring of the course KEY; repeatable")
    ap.add_argument("--sport", choices=["XC", "TF"], default=None)
    ap.add_argument("--list", type=int, default=0, metavar="N",
                    help="print the N busiest course keys and exit -- use it "
                         "to find the spelling to pass to --course")
    args = ap.parse_args()

    # ! A MISSING PACK IS A SENTENCE, NOT A TRACEBACK. The default path is the
    #   production pack; on any other machine it simply is not there, and a
    #   FileNotFoundError from three frames inside loadCols tells the reader
    #   nothing about what to do.
    if not os.path.exists(args.pack):
        print(f"\n  no pack at {args.pack}\n"
              f"  This reads the SOLVE'S OWN pack, so it has to run where the\n"
              f"  pipeline runs (or be pointed at a copy with --pack). Build\n"
              f"  one with:  python engine/speed_ratings.py --sport merged "
              f"--cache --pack-only")
        return 1
    cols, npz = bk.loadInputs(args.pack, args.npz)
    keys = [str(k) for k in cols["course_keys"]]
    course = np.asarray(cols["course"]).astype(np.int64)
    ath = np.asarray(cols["athlete"]).astype(np.int64)
    m_sport = _sportMask(cols, args.sport)

    n_by_cell = np.bincount(course[m_sport & (course >= 0)],
                            minlength=len(keys))

    if args.list or not args.course:
        top = np.argsort(-n_by_cell)[:max(args.list, 25)]
        print(f"\n  the busiest course keys"
              f"{'' if args.sport is None else f' in {args.sport}'}:\n")
        print(f"  {'rows':>9}  key")
        print(f"  {'-' * 9}  {'-' * 60}")
        for i in top:
            if n_by_cell[i] == 0:
                continue
            print(f"  {int(n_by_cell[i]):>9,}  {keys[i]}")
        if not args.course:
            print("\n  pass two of them with --course to test the island "
                  "hypothesis, e.g.\n"
                  "      python engine/diag_course_island.py "
                  "--course balboa --course glendoveer")
        return 0

    found = _matchCells(keys, args.course)
    named = sorted({i for v in found.values() for i in v})
    print(f"\n  === the named courses ===")
    for pat, idx in found.items():
        if not idx:
            print(f"  ⚠ '{pat}' matched NO course key. Run --list to see the "
                  f"spellings; the pack carries keys, not meet names.")
            continue
        for i in idx:
            print(f"  '{pat}' -> [{i}] {keys[i]}  ({int(n_by_cell[i]):,} rows)")
    if len(named) < 2:
        print("\n  need at least two matched courses to measure an island.")
        return 1

    # ---- the athlete sets, and who else they raced --------------------
    # ! ONE ATHLETE CODE COUNTS ONCE PER CELL, not once per row. A runner with
    #   four races at a course is one witness to it, which is also how the
    #   engine's own voter count behaves after race_sat saturates.
    in_cell = {}
    for i in named:
        in_cell[i] = set(np.unique(ath[m_sport & (course == i)]).tolist())

    named_set = set(named)
    print(f"\n  === shared athletes ===")
    print(f"  {'':>6} " + " ".join(f"{i:>8}" for i in named))
    for i in named:
        row = " ".join(f"{len(in_cell[i] & in_cell[j]):>8,}" for j in named)
        print(f"  {i:>6} {row}")
    print(f"  (the diagonal is each course's own athlete count)")

    # ---- mutual dependence vs outside anchoring ----------------------
    print(f"\n  === where else do this course's athletes race? ===")
    print(f"  {'cell':>6} {'voters':>8} {'other races':>12} "
          f"{'mutual':>8} {'outside':>8}  key")
    print(f"  {'-' * 6} {'-' * 8} {'-' * 12} {'-' * 8} {'-' * 8}  {'-' * 40}")
    stats = {}
    for i in named:
        who = in_cell[i]
        if not who:
            continue
        m_them = m_sport & np.isin(ath, list(who)) & (course != i) & (course >= 0)
        other = course[m_them]
        n_other = int(other.size)
        n_mutual = int(np.isin(other, list(named_set - {i})).sum())
        n_outside = n_other - n_mutual
        stats[i] = dict(voters=len(who), other=n_other, mutual=n_mutual,
                        outside=n_outside)
        f_mut = (n_mutual / n_other) if n_other else 0.0
        f_out = (n_outside / n_other) if n_other else 0.0
        print(f"  {i:>6} {len(who):>8,} {n_other:>12,} "
              f"{f_mut:>7.1%} {f_out:>7.1%}  {keys[i][:40]}")

    # ---- the shrinkage itself ----------------------------------------
    # ★ THE NUMBER THAT SAYS WHETHER THE PRIOR IS WINNING. A cell's published
    #   difficulty is (own evidence * w + group mean * k) / (w + k), so
    #   w / (w + k) is the share of it that is the course's own measurement.
    #   Below about a half, the published number is mostly "an ordinary
    #   course" -- which for XC is about zero, i.e. UNDER-difficultied.
    pri = dict(be.PRIOR_GROUP_BY)
    w_src = None
    if npz is not None and "races_per_base" in npz and "base_of_cell" in npz:
        try:
            rpb = np.asarray(npz["races_per_base"], dtype=np.float64)
            boc = np.asarray(npz["base_of_cell"], dtype=np.int64)
            w_src = ("the solve state's races_per_base", rpb, boc)
        except Exception:                                        # noqa: BLE001
            w_src = None
    print(f"\n  === how much of each published number is the COURSE, and how "
          f"much is 'an ordinary course'? ===")
    if w_src is None:
        print(f"  (no solve state at {args.npz} -- falling back to the pack's "
              f"own race counts, which over-state w because they do not "
              f"saturate in voters the way race_sat does)")
    print(f"  ! the prior k is in RACES, per group: {pri}")
    print(f"  {'cell':>6} {'races w':>9} {'k':>6} {'own share':>10}  reading")
    print(f"  {'-' * 6} {'-' * 9} {'-' * 6} {'-' * 10}  {'-' * 34}")
    for i in named:
        grp = "XC" if (args.sport or "XC").upper() == "XC" else "TF:out"
        k = float(pri.get(grp, 1.0))
        if w_src is not None:
            _lbl, rpb, boc = w_src
            w = float(rpb[boc[i]]) if i < boc.size and boc[i] < rpb.size else 0.0
        else:
            w = float(n_by_cell[i])
        share = w / (w + k) if (w + k) > 0 else 0.0
        note = ("mostly the course" if share >= 0.75 else
                "half the prior" if share >= 0.5 else
                "MOSTLY THE PRIOR — pulled toward ordinary")
        print(f"  {i:>6} {w:>9,.1f} {k:>6.2f} {share:>9.1%}  {note}")

    print(f"\n  HOW TO READ IT. The island effect needs BOTH: a high `mutual` "
          f"share, so the\n  pair mainly sees itself, and a low `own share`, "
          f"so the prior is what fills the\n  gap. When both hold, the two "
          f"courses' DIFFERENCE is measured and their SUM is\n  not -- the sum "
          f"sits where the prior put it, which is an ordinary course.\n"
          f"\n  AND THE ASYMMETRY IS IN `outside`, not in either course's own "
          f"hardness. A\n  mutual comparison is symmetric and cannot make one "
          f"side look easier. Whichever\n  course has MORE outside races keeps "
          f"its level from that outside evidence; the\n  one with fewer "
          f"absorbs the shrinkage. If the national-final venue's `outside` is\n"
          f"  well below the public course's, that is the mechanism, and the "
          f"fix is a prior\n  that knows a championship field is not an "
          f"ordinary field — not more passes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
