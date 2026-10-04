#!/usr/bin/env python3
"""
run_report.py -- one scorecard per pipeline run, against the run before.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/run_report.py                 # the latest run's logs
    /srv/venv/bin/python scripts/run_report.py --log-dir logs/20261004_010841

★ WHY (owner, 2026-10-04: "There's like so many moving parts how can we
  capture it all?"). Every change so far was judged by an impression and a
  one-off diagnostic, and two of them moved the owner's numbers the wrong
  way before anyone could see it. This is the fixed list every run is read
  against, three kinds of line:

    FAIR TESTS  does the model predict what it did not see? The held-out
                race error, the cross-sport holdout's bias (a track season
                predicted from cross country alone), LACCTiC's two fair
                tests (finishing order from a runner's other races; XC
                against the actual 5000), and whether the race-day term
                makes an athlete's races agree. These decide.
    HEALTH      board-sanity failures, courses whose days all lean one way,
                the measured field-depth slope, whether the course solve
                settled.
    SENTINELS   the cases the owner has caught, by name, so a fix that
                breaks one shows: SENTINELS below. Add each new one there.

  Each run's numbers are appended to engine/data/run_history.jsonl and the
  table prints this run, the last, the change, and which way is better.
  Reads the run's logs and the database; writes only the history line and
  <log dir>/REPORT.txt.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
HISTORY = os.path.join(_ROOT, "engine", "data", "run_history.jsonl")

# (key, label, log file, regex with one group, better: "low" | "high" | "abs")
LOG_METRICS = [
    ("holdout_race_sd", "held-out race error (sd, log)", "08a_holdout.log",
     r"\[joint\] error sd ([0-9.]+)", "low"),
    ("sport_holdout_bias", "cross-sport holdout bias (track from XC, log)",
     "08a_sport_holdout.log", r"\[joint\] scorecard: bias ([-+0-9.]+)", "abs"),
    ("sport_holdout_sd", "cross-sport holdout error (sd, log)", "08a_sport_holdout.log",
     r"\[joint\] error sd ([0-9.]+)", "low"),
    ("lacctic_order_ours", "LACCTiC order test: ours (%)", "16b_lacctic.log",
     r"ours ([0-9.]+)%\s+theirs [0-9.]+%\s+\(higher", "high"),
    ("lacctic_order_theirs", "LACCTiC order test: theirs (%)", "16b_lacctic.log",
     r"ours [0-9.]+%\s+theirs ([0-9.]+)%\s+\(higher", None),
    ("lacctic_5000_ours", "XC -> actual 5000 spread: ours (%)", "16b_lacctic.log",
     r"ours\s+bias [-+0-9.]+%\s+spread ([0-9.]+)%", "low"),
    ("lacctic_5000_theirs", "XC -> actual 5000 spread: theirs (%)", "16b_lacctic.log",
     r"theirs bias [-+0-9.]+%\s+spread ([0-9.]+)%", None),
    ("day_scatter", "race-day term: race scatter, robust (%)", "08_golive.log",
     r"day in, leave-self-out\s+sd [0-9.]+%\s+robust ([0-9.]+)%", "low"),
    ("sanity_hard", "board-sanity hard findings", "10a_board_sanity.log",
     r"\[sanity\] (\d+) hard", "low"),
    ("lean_courses", "courses whose days lean one way", "08_golive.log",
     r"courses whose days lean one way \(([\d,]+) of", "low"),
    ("field_slope_xc", "field-depth slope, XC (% per point)", "08_golive.log",
     r"\[bracket\] field depth \(XC\): a race's reading moves ([-+0-9.]+)%", None),
    ("bracket_last_change", "course solve: last max change", "08_golive.log",
     r"\[bracket\] iteration \d+: max change ([0-9.]+)", "low"),
]

# ★ THE CASES THE OWNER CAUGHT. (key, label, SQL returning one number, better)
#   Each is a question with a known right answer in the owner's words:
#   comments say what "right" is. Add new ones here.
SENTINELS = [
    # the owner's 3200: 9:01.1 at Arcadia, 2025-04-12 ("138 -> 133" was wrong)
    ("owner_3200", "owner's 9:01.1 3200 (Arcadia 2025) rating",
     "SELECT speed_rating FROM results_tf WHERE result_id = 258164858", None),
    # Hammerand's two 10ks: 29:20.5 and 29:27.0 should rate within ~1 point
    ("hammerand_10k_gap", "Hammerand 10k 29:20 minus 29:27 (points; ~0.5 is right)",
     "SELECT (SELECT speed_rating FROM results_tf WHERE result_id = 277459367)"
     " - (SELECT speed_rating FROM results_tf WHERE result_id = 293998421)", "abs"),
    # Mt. SAC: the 4828 should be only 1-2% easier than the 4715 (owner)
    ("mtsac_4700", "Mt. SAC 4715m difficulty (%)",
     "SELECT 100 * difficulty FROM course_difficulties WHERE canonical_id = 13433 "
     "AND distance_m = 4700 LIMIT 1", None),
    ("mtsac_4800", "Mt. SAC 4828m difficulty (%)",
     "SELECT 100 * difficulty FROM course_difficulties WHERE canonical_id = 13433 "
     "AND distance_m = 4800 LIMIT 1", None),
    ("mtsac_gap", "Mt. SAC 4715 minus 4828 (%; owner: 1-2)",
     "SELECT 100 * ((SELECT difficulty FROM course_difficulties WHERE canonical_id = 13433"
     " AND distance_m = 4700 LIMIT 1) - (SELECT difficulty FROM course_difficulties"
     " WHERE canonical_id = 13433 AND distance_m = 4800 LIMIT 1))", None),
    # Glendoveer (NXN): runners' own read about +6.4% (2026-10-04)
    ("glendoveer", "Glendoveer 5000m difficulty (%; runners read ~6.4)",
     "SELECT 100 * difficulty FROM course_difficulties WHERE canonical_id = 22331 "
     "AND distance_m = 5000 LIMIT 1", None),
]


def latestLogDir():
    dirs = sorted(d for d in glob.glob(os.path.join(_ROOT, "logs", "2*")) if os.path.isdir(d))
    return dirs[-1] if dirs else None


def readLogMetrics(log_dir):
    out = {}
    for key, _label, fname, rx, _better in LOG_METRICS:
        path = os.path.join(log_dir, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            hits = re.findall(rx, f.read())
        if hits:
            out[key] = float(str(hits[-1]).replace(",", ""))
    return out


def readSentinels():
    out = {}
    try:
        from database import getConn
        with getConn() as conn:
            cur = conn.cursor()
            for key, _label, sql, _better in SENTINELS:
                try:
                    cur.execute(sql)
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        out[key] = round(float(row[0]), 3)
                except Exception:                                   # noqa: BLE001
                    conn.rollback()
            conn.rollback()
    except Exception as exc:                                         # noqa: BLE001
        print(f"[report] sentinels skipped ({type(exc).__name__}: {exc})")
    return out


def lastRun(before):
    if not os.path.exists(HISTORY):
        return None
    prev = None
    with open(HISTORY, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("run") != before:
                prev = rec
    return prev


def verdict(better, now, then):
    if now is None or then is None or better is None or now == then:
        return ""
    if better == "low":
        return "better" if now < then else "WORSE"
    if better == "high":
        return "better" if now > then else "WORSE"
    if better == "abs":
        return "better" if abs(now) < abs(then) else "WORSE"
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", default=None)
    ap.add_argument("--no-history", action="store_true", help="print only")
    a = ap.parse_args()
    log_dir = a.log_dir or latestLogDir()
    if not log_dir:
        raise SystemExit("[report] no logs/ run directory")
    run = os.path.basename(os.path.normpath(log_dir))
    now = readLogMetrics(log_dir)
    now.update(readSentinels())
    prev = lastRun(run)
    then = (prev or {}).get("metrics", {})
    lines = [f"== RUN REPORT {run}" + (f"  (against {prev['run']})" if prev else "  (no earlier run)")]

    def section(title, rows):
        lines.append(f"\n  {title}")
        lines.append(f"    {'':<58}{'this run':>11}{'last':>11}{'change':>10}")
        for key, label, better in rows:
            n, t = now.get(key), then.get(key)
            ch = "" if n is None or t is None else f"{n - t:+.3f}"
            lines.append(f"    {label:<58}{'-' if n is None else f'{n:.3f}':>11}"
                         f"{'-' if t is None else f'{t:.3f}':>11}{ch:>10}  {verdict(better, n, t)}")

    fair = {"holdout_race_sd", "sport_holdout_bias", "sport_holdout_sd", "lacctic_order_ours",
            "lacctic_order_theirs", "lacctic_5000_ours", "lacctic_5000_theirs", "day_scatter"}
    section("FAIR TESTS (these decide)",
            [(k, lab, b) for k, lab, _f, _r, b in LOG_METRICS if k in fair])
    section("HEALTH", [(k, lab, b) for k, lab, _f, _r, b in LOG_METRICS if k not in fair])
    section("SENTINELS (the cases the owner caught)", [(k, lab, b) for k, lab, _s, b in SENTINELS])
    missing = [lab for k, lab, *_ in LOG_METRICS if k not in now]
    if missing:
        lines.append(f"\n  not in this run's logs: {', '.join(missing)}")
    text = "\n".join(lines)
    print(text)
    try:
        with open(os.path.join(log_dir, "REPORT.txt"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    if not a.no_history:
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"run": run, "at": datetime.datetime.now().isoformat(timespec="seconds"),
                                "metrics": now}) + "\n")
        print(f"\n[report] appended to {os.path.relpath(HISTORY, _ROOT)}")


if __name__ == "__main__":
    main()
