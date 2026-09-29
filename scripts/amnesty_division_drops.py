#!/usr/bin/env python3
"""
amnesty_division_drops.py -- lift the division drops a wrong pace bar made.

    /srv/venv/bin/python scripts/amnesty_division_drops.py            # report
    /srv/venv/bin/python scripts/amnesty_division_drops.py --write    # append pardons
    then: /srv/venv/bin/python engine/dump_overrides.py               # refresh dist_drop

Run from the PROJECT ROOT.

★ THE WRONG CONVICTION (owner, 2026-09-29: "it shouldn't be dropped, it's
  NCAA nationals and obv a 10k"). find_dropped_divisions nukes a division
  with nothing rated when its field's MEDIAN pace is "beyond any human" --
  and that bar was 0.18 s/m, 3:00/km, a 30:00 10k. A D1 national final's
  median sits right there. Worse, "nothing rated" was true only because no
  2026-season tfrrs row had a person yet, so no row could be on a board.
  The bar is now the world record at the label (impossible_distance.wrTime).

★ THE RETRIAL IS EXACT, NOT A GUESS. Every such drop line carries its own
  evidence in its comment -- "median pace 0.179 s/m at 10000 -- beyond any
  human" -- so the median time is pace x label, and the question is the one
  the fixed tool asks: is it faster than the men's world record at that
  distance? If not, the conviction rested on the old bar alone and it is
  pardoned. Drops for any other reason (survivors off their own heads, a
  walk) are not touched.

--write appends a `_DISTANCE_DROP_XC.difference_update({...})` block to
engine/corrections.py: append-only, like amnesty_result_drops, so the
conviction and the pardon both stay readable.
"""
import argparse
import datetime
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CORR = os.path.join(_ROOT, "engine", "corrections.py")
LINE = re.compile(r"^\s*\((\d+),\s*(\d+)\),\s*#.*?median pace ([0-9.]+) s/m at "
                  r"([0-9.]+) -- beyond any human")
MARK = "# === amnesty_division_drops"


def convictions(text):
    """[(meet, div, pace s/m, label m, line)] -- every pace-bar drop line."""
    out = []
    for ln in text.splitlines():
        m = LINE.match(ln)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), float(m.group(3)),
                        float(m.group(4)), ln.strip()))
    return out


def pardonable(convs):
    """The convictions whose median is NOT faster than the world record."""
    from impossible_distance import wrTime
    keep, free = [], []
    for meet, div, sm, label, ln in convs:
        med, wr = sm * label, wrTime(label, "M")
        (free if med >= wr else keep).append((meet, div, sm, label, med, wr))
    return free, keep


def alreadyPardoned(text):
    got = set()
    inside = False
    for ln in text.splitlines():
        if ln.startswith(MARK):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s*\((\d+),\s*(\d+)\),", ln)
            if m:
                got.add((int(m.group(1)), int(m.group(2))))
            if ln.startswith("_DISTANCE_DROP_XC.difference_update"):
                inside = False
    return got


def block(free):
    today = datetime.date.today().isoformat()
    lines = ["", f"{MARK} ({today}) ===",
             "# Pace-bar convictions retried against the world record at the",
             "# label (scripts/amnesty_division_drops.py). Each median below is",
             "# slower than the men's WR for its distance: not beyond any human.",
             "_DIVISION_DROP_PARDON = {"]
    for meet, div, sm, label, med, wr in free:
        lines.append(f"    ({meet}, {div}),   # median {med:.0f}s at {label:.0f}m "
                     f"vs WR {wr:.0f}s")
    lines += ["}", "_DISTANCE_DROP_XC.difference_update(_DIVISION_DROP_PARDON)",
              "del _DIVISION_DROP_PARDON", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=40)
    a = ap.parse_args()
    with open(CORR, encoding="utf-8") as f:
        text = f.read()
    done = alreadyPardoned(text)
    convs = [c for c in convictions(text) if (c[0], c[1]) not in done]
    free, keep = pardonable(convs)
    print(f"[amnesty] {len(convs):,} pace-bar drops; {len(free):,} are slower than "
          f"the world record at their label (pardon), {len(keep):,} are not (stay)")
    for meet, div, sm, label, med, wr in free[:a.show]:
        print(f"    pardon ({meet}, {div})  median {med:.0f}s at {label:.0f}m, "
              f"WR {wr:.0f}s")
    if not free:
        return
    if a.write:
        with open(CORR, "a", encoding="utf-8") as f:
            f.write(block(free))
        print(f"[amnesty] appended {len(free):,} pardons to {CORR}; now run "
              f"engine/dump_overrides.py, and the backfill re-rates the rows")
    else:
        print("[amnesty] DRY RUN -- pass --write to append the pardons")


if __name__ == "__main__":
    main()
