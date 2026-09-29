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
  the fixed tool asks, held to what a whole FIELD has really run: is it
  faster than the deepest real field of its feed (DEEPEST, below)? If not,
  the conviction rested on the old bar alone and it is pardoned. Drops for any other reason (survivors off their own heads, a
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


# ★ A MEDIAN IS NOT A LEADER (owner's dry run, 2026-09-29). The world record
#   bounds the fastest runner in a race, and it is what the fixed tool uses
#   to call a field impossible. But to PARDON a field on its median, the
#   median has to be one a real field has run: the first dry run would have
#   freed an 8,047 m division whose median was 21:11 (4:14/mile for the
#   middle of the field) and anet 5k fields with 14:50 medians. So a drop is
#   pardoned only when its median is no faster than the DEEPEST REAL FIELD
#   of its feed, as a multiple of the men's world record at the label,
#   measured on the live site the same day:
#     tfrrs (college)  NCAA DI men 2025, 10000 m, 259 finishers, median
#                      29:49 -> 1789 / 1571 = 1.139
#     anet             Foot Locker Nationals boys 2019, 5000 m, 40
#                      finishers, median 15:45 -> 945 / 755 = 1.252 (the
#                      fastest of FL 2019/2023/2024 and NXN 2019-2025;
#                      NXN's boys medians run 1.29-1.32)
#   An anet COLLEGE division is held to the anet bar -- stricter, so it
#   stays dropped, which is the safe direction.
DEEPEST = {"tfrrs": 1789.0 / 1571.0, "anet": 945.0 / 755.36}


def feedOf(key, sources=None):
    """'tfrrs' or 'anet' for a (meet, div) key: from the rows when known;
    otherwise by the id itself -- a tfrrs div_id is a per-meet 0, 1, 2 and
    an anet one a global id in the hundreds of thousands."""
    if sources and key in sources:
        return "anet" if "anet" in sources[key] else "tfrrs"
    return "tfrrs" if key[1] < 1000 else "anet"


def pardonable(convs, sources=None):
    """The convictions whose median is no faster than the deepest real field
    of their feed (DEEPEST x the men's world record at the label)."""
    from impossible_distance import wrTime
    keep, free = [], []
    for meet, div, sm, label, ln in convs:
        med, wr = sm * label, wrTime(label, "M")
        bar = DEEPEST[feedOf((meet, div), sources)] * wr
        (free if med >= bar - 0.5 else keep).append((meet, div, sm, label, med, bar))
    return free, keep


def loadSources(keys):
    """{(meet, div): {sources}} from results, or None without a database."""
    try:
        from database import getConn
        from psycopg2.extras import execute_values
    except Exception:                                 # noqa: BLE001
        return None
    try:
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE ad_keys (meet_id bigint, div_id bigint)")
            execute_values(cur, "INSERT INTO ad_keys VALUES %s", list(keys))
            cur.execute("""SELECT DISTINCT r.meet_id, r.div_id, r.source
                           FROM ad_keys k JOIN results r
                             ON r.meet_id = k.meet_id AND r.div_id = k.div_id""")
            out = {}
            for m, d, src in cur.fetchall():
                out.setdefault((int(m), int(d)), set()).add(src)
            conn.rollback()
            return out
    except Exception as exc:                          # noqa: BLE001
        print(f"[amnesty] no database ({type(exc).__name__}); feeds read off the ids")
        return None


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
             "# Pace-bar convictions retried against the deepest real field of",
             "# their feed (scripts/amnesty_division_drops.py, DEEPEST). Each",
             "# median below is one a real field has run: not beyond any human.",
             "_DIVISION_DROP_PARDON = {"]
    for meet, div, sm, label, med, bar in free:
        lines.append(f"    ({meet}, {div}),   # median {med:.0f}s at {label:.0f}m, "
                     f"deepest real field {bar:.0f}s")
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
    sources = loadSources({(c[0], c[1]) for c in convs}) if convs else None
    free, keep = pardonable(convs, sources)
    print(f"[amnesty] {len(convs):,} pace-bar drops; {len(free):,} have a median a "
          f"real field has run (pardon), {len(keep):,} are faster than the deepest "
          f"real field of their feed (stay)")
    for meet, div, sm, label, med, bar in free[:a.show]:
        print(f"    pardon ({meet}, {div}) {feedOf((meet, div), sources):5}  median "
              f"{med:.0f}s at {label:.0f}m, deepest real field {bar:.0f}s")
    for meet, div, sm, label, med, bar in keep[:min(a.show, 15)]:
        print(f"    stays  ({meet}, {div}) {feedOf((meet, div), sources):5}  median "
              f"{med:.0f}s at {label:.0f}m, deepest real field {bar:.0f}s")
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
