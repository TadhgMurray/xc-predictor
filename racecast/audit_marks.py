"""
audit_marks.py -- the field-mark census. READS ONLY, WRITES NOTHING.

    python racecast/audit_marks.py

Run from the project root, like panels.py.

★ THIS RUNS BEFORE THE MARKS BOARD GETS BUILT, on purpose. The parser in
  marks.py makes three refusable decisions -- grammar, event, sanity -- and
  each refusal is invisible until it is counted. This prints the counts:
  how much of results_tf parses, under which grammar, what the unparsed
  tail actually says, which event spellings map nowhere, and how many
  parsed marks fail their event's physical range. The board is designed
  against these numbers, not against guesses.

★ GROUPED IN SQL, PARSED IN PYTHON. results_tf's field rows are millions,
  but the DISTINCT (event_short, mark) pairs are a tiny fraction of that --
  the same "normalise ~500 spellings, not 225M calls" observation that
  fixed normGrade. Each distinct pair is parsed once and weighted by its
  count.

! metric_bare GETS A DISAMBIGUATION LINE. A bare '12.19' is presumed
  metres; the census cross-checks that presumption by asking, per event,
  how many bare values are plausible as metres versus plausible as
  feet-converted. If the metres column dominates, the presumption stands;
  if not, the parser needs a per-source unit decision before any board.
"""

import sys
from collections import Counter, defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")
from database import getConn
from marks import parseMark, normalizeFieldEvent, saneMark, _FOOT


def census(pairs):
    """[(event_text, mark_text, n)] -> a printable census dict.

    Pure -- no database -- so it is testable on synthetic rows.
    """
    kinds = Counter()
    unparsed = Counter()
    unmapped_events = Counter()
    per_event = defaultdict(lambda: Counter())
    bare_as_m = Counter()
    bare_as_ft = Counter()
    insane_examples = defaultdict(list)

    for event_text, mark_text, n in pairs:
        metres, kind = parseMark(mark_text)
        kinds[kind or "unparsed"] += n
        if kind is None:
            unparsed[str(mark_text)[:40]] += n
            continue

        key = normalizeFieldEvent(event_text)
        if key is None:
            unmapped_events[str(event_text)[:40]] += n
            continue
        if metres is None:                       # sentinel: counted, done
            per_event[key]["no_mark"] += n
            continue

        per_event[key]["parsed"] += n
        if saneMark(key, metres):
            per_event[key]["sane"] += n
        else:
            per_event[key]["insane"] += n
            if len(insane_examples[key]) < 5:
                insane_examples[key].append(f"{mark_text!r} -> {metres:.2f} m")

        if kind == "metric_bare":
            bare_as_m[key] += n if saneMark(key, metres) else 0
            bare_as_ft[key] += n if saneMark(key, metres * _FOOT) else 0

    return {"kinds": kinds, "unparsed": unparsed,
            "unmapped_events": unmapped_events, "per_event": per_event,
            "bare_as_m": bare_as_m, "bare_as_ft": bare_as_ft,
            "insane_examples": insane_examples}


def report(c):
    total = sum(c["kinds"].values())
    print(f"\n[marks] {total:,} field rows\n")

    print("  GRAMMARS")
    for kind, n in c["kinds"].most_common():
        print(f"    {kind:<12} {n:>12,}  {100 * n / total:5.1f}%")

    print("\n  TOP UNPARSED SPELLINGS (each needs a rule or stays off the board)")
    for text, n in c["unparsed"].most_common(30):
        print(f"    {n:>10,}  {text!r}")

    print("\n  TOP UNMAPPED EVENT SPELLINGS")
    for text, n in c["unmapped_events"].most_common(30):
        print(f"    {n:>10,}  {text!r}")

    print("\n  PER EVENT (parsed / sane / out-of-range / sentinels)")
    for key in sorted(c["per_event"]):
        e = c["per_event"][key]
        print(f"    {key:<13} parsed {e['parsed']:>10,}  sane {e['sane']:>10,}"
              f"  insane {e['insane']:>8,}  no_mark {e['no_mark']:>10,}")
        for ex in c["insane_examples"].get(key, []):
            print(f"        ⚠ {ex}")

    print("\n  BARE-NUMBER UNIT CHECK (plausible as metres vs as feet)")
    for key in sorted(set(c["bare_as_m"]) | set(c["bare_as_ft"])):
        print(f"    {key:<13} metres {c['bare_as_m'][key]:>10,}"
              f"   feet {c['bare_as_ft'][key]:>10,}")


def main():
    with getConn() as conn:
        with conn.cursor() as cur:
            # Distinct spellings with weights -- see the module docstring.
            cur.execute("""
                SELECT event_short, mark, count(*)
                FROM   results_tf
                WHERE  is_field = 1
                GROUP  BY 1, 2
            """)
            pairs = cur.fetchall()
    print(f"[marks] {len(pairs):,} distinct (event, mark) spellings")
    report(census(pairs))


if __name__ == "__main__":
    main()
