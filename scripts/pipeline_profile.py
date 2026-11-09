#!/usr/bin/env python3
"""scripts/pipeline_profile.py -- one pipeline run's steps by time and memory,
or two runs side by side.

    python scripts/pipeline_profile.py logs/20261108_155220
    python scripts/pipeline_profile.py logs/20261108_155220 logs/20261110_...   # before, after
    python scripts/pipeline_profile.py --top 25 logs/<run>

Reads <run>/summary.log (one line per step: "name ok (123s, in parallel,
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


def load(run):
    steps = {}
    try:
        lines = open(os.path.join(run, "summary.log")).read().splitlines()
    except OSError:
        sys.exit(f"no summary.log in {run}")
    for ln in lines:
        m = LINE.match(ln)
        if not m:
            continue
        name, verdict, secs, rest = m.groups()
        kind = "bg" if "background" in rest else "par" if "in parallel" in rest else "line"
        pk = re.search(r"peak ([0-9.]+) GB", rest)
        lo = re.search(r"down to ([0-9.]+) GB", rest)
        e = {"s": int(secs), "ok": verdict == "ok", "kind": kind,
             "peak": float(pk.group(1)) if pk else None,
             "low": float(lo.group(1)) if lo else None}
        mem = os.path.join(run, ".mem", name)
        if e["peak"] is None and os.path.exists(mem):
            t = open(mem).read()
            m2 = re.search(r"peak_gb=([0-9.]+) min_avail_gb=([0-9.]+)", t)
            if m2:
                e["peak"], e["low"] = float(m2.group(1)), float(m2.group(2))
        steps[name] = e
    return steps


def fmt(e):
    if not e:
        return f"{'':>7} {'':>6} {'':>5}"
    pk = f"{e['peak']:.1f}" if e.get("peak") is not None else "-"
    lo = f"{e['low']:.0f}" if e.get("low") is not None else "-"
    flag = "" if e["ok"] else " FAIL"
    return f"{e['s']:>7,} {pk:>6} {lo:>5}{flag}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--top", type=int, default=0, help="only the N slowest")
    a = ap.parse_args()
    runs = [load(r) for r in a.runs[:2]]
    names = list(dict.fromkeys(n for r in runs for n in r))
    key = runs[-1] if len(runs) > 1 else runs[0]
    names.sort(key=lambda n: -max(r.get(n, {}).get("s", 0) for r in runs))
    if a.top:
        names = names[:a.top]
    hdr = f"{'step':<28} {'kind':<4} " + " | ".join(
        f"{'secs':>7} {'peakGB':>6} {'free':>5}" for _ in runs)
    print("  ".join(os.path.basename(r.rstrip('/')) for r in a.runs[:2]))
    print(hdr)
    print("-" * len(hdr))
    for n in names:
        kind = (key.get(n) or next((r[n] for r in runs if n in r), {})).get("kind", "")
        print(f"{n[:28]:<28} {kind:<4} " + " | ".join(fmt(r.get(n)) for r in runs))
    for r, path in zip(runs, a.runs):
        inline = sum(e["s"] for e in r.values() if e["kind"] == "line")
        fails = [n for n, e in r.items() if not e["ok"]]
        print(f"\n{os.path.basename(path.rstrip('/'))}: {len(r)} steps, {inline:,} s in line"
              f"{', FAILED: ' + ' '.join(fails) if fails else ''}")
        try:
            fin = [ln for ln in open(os.path.join(path, "summary.log")) if "finished in" in ln]
        except OSError:
            fin = []
        if fin:
            print("  " + fin[-1].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
