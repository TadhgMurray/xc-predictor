#!/usr/bin/env python3
"""
pin_division_distance.py -- pin every result of an athletic.net XC division
to the distance it was actually run at.

    /srv/venv/bin/python scripts/pin_division_distance.py 1120240=5000 1120246=5000 1120242=5000
    /srv/venv/bin/python scripts/pin_division_distance.py 1120240=5000 ... --write

★ WHY (owner, 2026-10-08, Midlothian James Smith HS Invitational XC26).
  Three of its eight divisions are stored at 3218 m (2 miles) and were run
  at 5000 m: medians 28:46 (Varsity 1A-4A girls), 25:36 and 21:48 (the two
  JV boys' races), against 18:30 and 21:58 for the JV girls' races that
  really were 2 miles. Their 344 rows read ~40% slow, and because the day
  term is one per VENUE and day (XCP_RACE_KEY=venue), they dragged it to
  "slow 10%" and every correct race there was over-credited ~12%.

! WHY PER-RESULT PINS, NOT _DISTANCE_OVERRIDES. A division override may
  only LOWER a stored distance (backfill_normalize._resolveDistanceGender,
  "DOWNWARD ONLY": inflated labels mint fake elite ratings). A per-result
  pin (_RESULT_OVERRIDE_XC) is checked first and is not clamped, which is
  what a hand-verified raise needs -- and why each one is printed with its
  evidence before anything is written.

The dry run prints, per division: its title, stored distance, results, the
median time, and the median mile pace at the stored and the proposed
distance -- read them before --write. --write appends one block to
engine/corrections.py (backup at engine/corrections.py.bak), live at the
next backfill_normalize --apply.
"""
import argparse
import io
import os
import sys
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
PATH = os.path.join(_ROOT, "engine", "corrections.py")
MILE = 1609.344


def _clock(sec):
    return f"{int(sec // 60)}:{sec % 60:04.1f}" if sec else "-"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pins", nargs="+", help="DIV=METRES, e.g. 1120240=5000")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    pins = {}
    for p in a.pins:
        d, _, m = p.partition("=")
        pins[int(d)] = int(float(m))

    from database import getConn
    rows = {}
    with getConn() as conn, conn.cursor() as cur:
        for d, metres in pins.items():
            cur.execute("SELECT meet_name, division, distance FROM meets WHERE div_id = %s", (d,))
            head = cur.fetchone()
            cur.execute("""SELECT result_id, time_seconds FROM results
                           WHERE div_id = %s AND source = 'anet'""", (d,))
            got = cur.fetchall()
            rows[d] = [r for r, _ in got]
            times = sorted(t for _, t in got if t and 0 < t < 999999)
            med = times[len(times) // 2] if times else None
            name, title, stored = head or ("?", "?", None)
            print(f"div {d}: {name} / {title}")
            print(f"    stored {stored} m -> {metres} m   {len(got)} results   median {_clock(med)}")
            if med and stored:
                print(f"    median mile pace: {_clock(med / (stored / MILE))} at {stored:.0f} m, "
                      f"{_clock(med / (metres / MILE))} at {metres} m")
        conn.rollback()

    n = sum(len(v) for v in rows.values())
    if not a.write:
        print(f"\nDRY RUN: {n:,} results would be pinned; --write to append them")
        return
    text = io.open(PATH, encoding="utf-8", newline="").read()
    io.open(PATH + ".bak", "w", encoding="utf-8", newline="").write(text)
    lines = [f"\n\n# === pin_division_distance ({date.today().isoformat()}) ===",
             "# Hand-verified raises: each division was run at the distance given,",
             "# not the one stored (see scripts/pin_division_distance.py).",
             "_RESULT_OVERRIDE_ADDITIONS = {"]
    for d, rids in rows.items():
        for rid in sorted(rids):
            lines.append(f"    {rid}: ({pins[d]}, None),  # div {d}")
    lines += ["}", "_RESULT_OVERRIDE_XC.update(_RESULT_OVERRIDE_ADDITIONS)",
              "del _RESULT_OVERRIDE_ADDITIONS"]
    io.open(PATH, "a", encoding="utf-8", newline="").write("\n".join(lines) + "\n")
    print(f"\nappended {n:,} pins to {PATH} (backup at {PATH}.bak); "
          "live at the next backfill_normalize --apply")


if __name__ == "__main__":
    main()
