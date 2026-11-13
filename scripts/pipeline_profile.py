#!/usr/bin/env python3
"""scripts/pipeline_profile.py -- one pipeline run's steps by time and memory,
or two runs side by side.

    python scripts/pipeline_profile.py logs/20261108_155220
    python scripts/pipeline_profile.py logs/20261108_155220 logs/20261110_...   # before, after
    python scripts/pipeline_profile.py --top 25 logs/<run>

"secs" is the summary line's time (for a background step: until the run
COLLECTED it); "ran" is how long the step's own log ran, first stamped line
to last. Reads <run>/summary.log (one line per step: "name ok (123s, in parallel,
peak 4.2 GB, box down to 31 GB free)") and <run>/.mem/<step> (scripts/jobrun.py),
and the first and last stamped line of each step's log for when it ran -- so a
step's wall time can be told from the time it held up the run ("in line"
against "parallel" / "background"). Runs from before 2026-11-09 have no memory
figures; their columns read "-".
"""
import argparse
import os
import re
import sys

LINE = re.compile(r"^\s*(\S+) (ok|FAILED after) \(?(\d+)s(.*)$")
HELD = re.compile(r"^\s*(\S+) held the run (\d+)s")


def load(run):
    steps = {}
    try:
        lines = open(os.path.join(run, "summary.log")).read().splitlines()
    except OSError:
        sys.exit(f"no summary.log in {run}")
    for ln in lines:
        m = LINE.match(ln)
        if not m:
            c = HELD.match(ln)              # "  evidence_chain held the run 812s"
            if c:
                steps[c.group(1)] = {"s": int(c.group(2)), "ok": True, "kind": "wait",
                                     "held": int(c.group(2)), "peak": None, "low": None,
                                     "ran": None}
            continue
        name, verdict, secs, rest = m.groups()
        kind = "bg" if "background" in rest else "par" if "in parallel" in rest else "line"
        pk = re.search(r"peak ([0-9.]+) GB", rest)
        lo = re.search(r"down to ([0-9.]+) GB", rest)
        hd = re.search(r"held the run (\d+)s", rest)
        e = {"s": int(secs), "ok": verdict == "ok", "kind": kind,
             "held": int(hd.group(1)) if hd else None,
             "peak": float(pk.group(1)) if pk else None,
             "low": float(lo.group(1)) if lo else None}
        mem = os.path.join(run, ".mem", name)
        if e["peak"] is None and os.path.exists(mem):
            t = open(mem).read()
            m2 = re.search(r"peak_gb=([0-9.]+) min_avail_gb=([0-9.]+)", t)
            if m2:
                e["peak"], e["low"] = float(m2.group(1)), float(m2.group(2))
        e["ran"] = _ran(os.path.join(run, f"{name}.log"))
        steps[name] = e
    return steps


_STAMP = re.compile(r"^(\d\d):(\d\d):(\d\d) ")


def _ran(path):
    """Seconds between a step log's first and last stamped line -- how long
    the step itself ran. A background step's summary time is when the run
    COLLECTED it, which can be hours after it finished (2026-11-10)."""
    try:
        with open(path, errors="replace") as fh:
            stamps = [m.groups() for m in map(_STAMP.match, fh) if m]
    except OSError:
        return None
    if not stamps:
        return None
    secs = [int(h) * 3600 + int(m) * 60 + int(x) for h, m, x in stamps]
    total, prev = 0, secs[0]
    for t in secs[1:]:
        total += (t - prev) % 86400              # past midnight
        prev = t
    return total


def _wall(run):
    """Seconds from the run's start (its folder's name) to the summary's
    last write -- the run's wall clock, stalls and all."""
    import datetime as dt
    m = re.search(r"(\d{8}_\d{6})", os.path.basename(run.rstrip("/")))
    try:
        t0 = dt.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").timestamp()
        return int(os.path.getmtime(os.path.join(run, "summary.log")) - t0)
    except (AttributeError, ValueError, OSError):
        return None


def fmt(e):
    if not e:
        return f"{'':>7} {'':>7} {'':>6} {'':>5}"
    pk = f"{e['peak']:.1f}" if e.get("peak") is not None else "-"
    lo = f"{e['low']:.0f}" if e.get("low") is not None else "-"
    flag = "" if e["ok"] else " FAIL"
    ran = f"{e['ran']:>7,}" if e.get("ran") is not None else f"{'-':>7}"
    return f"{e['s']:>7,} {ran} {pk:>6} {lo:>5}{flag}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--top", type=int, default=0, help="only the N slowest")
    ap.add_argument("--gaps", type=int, default=0, metavar="SECS",
                    help="also list, for every step that ran this long or longer, "
                         "the longest pauses inside its log (the last run given)")
    a = ap.parse_args()
    runs = [load(r) for r in a.runs[:2]]
    names = list(dict.fromkeys(n for r in runs for n in r))
    key = runs[-1] if len(runs) > 1 else runs[0]
    # by the time a step really took: what it ran for, else its summary time
    names.sort(key=lambda n: -max((r.get(n) or {}).get("ran") or (r.get(n) or {}).get("s", 0)
                                   for r in runs))
    if a.top:
        names = names[:a.top]
    hdr = f"{'step':<28} {'kind':<4} " + " | ".join(
        f"{'secs':>7} {'ran':>7} {'peakGB':>6} {'free':>5}" for _ in runs)
    print("  ".join(os.path.basename(r.rstrip('/')) for r in a.runs[:2]))
    print(hdr)
    print("-" * len(hdr))
    for n in names:
        kind = (key.get(n) or next((r[n] for r in runs if n in r), {})).get("kind", "")
        print(f"{n[:28]:<28} {kind:<4} " + " | ".join(fmt(r.get(n)) for r in runs))
    for r, path in zip(runs, a.runs):
        inline = sum(e["s"] for e in r.values() if e["kind"] == "line")
        held = sum(e["held"] or 0 for e in r.values() if e["kind"] in ("bg", "wait"))
        wall = _wall(path)
        fails = [n for n, e in r.items() if not e["ok"]]
        print(f"\n{os.path.basename(path.rstrip('/'))}: {len(r)} steps, {inline:,} s in line"
              f"{f', {held:,} s held for background steps' if held else ''}"
              f"{f', {wall / 3600:.1f} h start to last summary line' if wall else ''}"
              f"{', FAILED: ' + ' '.join(fails) if fails else ''}")
        try:
            fin = [ln for ln in open(os.path.join(path, "summary.log")) if "finished in" in ln]
        except OSError:
            fin = []
        if fin:
            print("  " + fin[-1].strip())
    if a.gaps:
        _printGaps(a.runs[min(len(a.runs), 2) - 1], runs[-1], a.gaps)
    return 0


def _printGaps(run, steps, min_ran, per_step=6):
    """The longest silences inside each long step's log, with the line that
    ended each one -- what the step was doing when the time went. A step
    whose time is spread evenly shows few; then its own phase lines say it."""
    print(f"\nInside the steps that ran {min_ran:,} s or more ({os.path.basename(run.rstrip('/'))}):")
    for name, e in sorted(steps.items(), key=lambda kv: -((kv[1].get("ran") or kv[1]["s"]))):
        if (e.get("ran") or e["s"]) < min_ran or e["kind"] == "wait":
            continue
        try:
            with open(os.path.join(run, f"{name}.log"), errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        gaps, prev = [], None
        for ln in lines:
            m = _STAMP.match(ln)
            if not m:
                continue
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if prev is not None:
                gaps.append(((t - prev) % 86400, ln[:150]))
            prev = t
        gaps.sort(key=lambda g: -g[0])
        print(f"  == {name} ({e.get('ran') or e['s']:,} s)")
        for g, ln in gaps[:per_step]:
            if g >= 30:
                print(f"     {g:6,} s before: {ln}")


if __name__ == "__main__":
    sys.exit(main())
