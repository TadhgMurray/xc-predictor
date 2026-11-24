#!/usr/bin/env python3
"""
ablation_ladder.py -- does our model beat a simple one, and do the four
candidate improvements actually improve anything?

    scripts/ablation_ladder.py                    # the full ladder
    scripts/ablation_ladder.py --pct 25           # bigger sample, slower
    scripts/ablation_ladder.py --only base,slaney
    scripts/ablation_ladder.py --dry-run          # print the commands
    scripts/ablation_ladder.py --kind forward     # every rung on the
                                                  #   validation season

Run from the PROJECT ROOT.

★ --kind forward (2026-09-29) is THE VALIDATION SCORECARD: each rung fits on
  the seasons before the pack's validation season and predicts all of it
  (engine/forward_holdout.py), and the table gains its bias, median |error|
  and per-race head-to-head. The sealed test season is refused unless
  --sealed. scripts/scorecard.py is the same run for two flag sets.

★ WHY (2026-09-10). Every rating system in this space that anyone has
  validated has FEWER moving parts than ours and leads with a
  cross-validated number:

    Slaney (California XC)   4 parameters, 1.88% mean prediction error
    Scarff (climbing WHR)    climber x route, 85% under 10-fold CV
    Beyer (horse racing)     par times plus a daily track variant

  We have athlete ability, course difficulty, a race-day effect, a form
  curve, distance offsets, altitude, a tilt and a sport offset -- and until
  today, no held-out number at all. There is no evidence any of the extra
  terms earn their keep. This finds out.

★ HOW IT IS SCORED. One number per rung: the held-out error of a solve that
  never saw the held-out RACES (engine/pair_validate.splitFor). Not rows --
  holding out rows leaves the same race in training and scores
  interpolation. Lower is better and the units are log time, so 0.0450 is
  about 4.5 per cent.

⚠ THE LADDER REPORTS. IT DOES NOT CHOOSE. Nothing here writes a board, a
  difficulty or a config; a human reads the table and decides what the next
  pipeline run carries. Automatic model selection on one number, computed
  once, on a sample, is how you get a model that is excellent at the
  holdout and wrong about Foot Locker.

! EVERY RUNG IS THE SAME SOLVE WITH DIFFERENT FLAGS, on the same athlete
  sample with the same seed, so the differences are the flags and nothing
  else. Rungs 1-6 remove things we already have; 7-10 add things we are
  considering.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# (name, extra flags, one line saying what the number will mean)
LADDER = [
    ("base", ["--altitude"],
     "the model exactly as shipped -- every other rung is read against this"),

    # ---- what happens if we take our own terms away ------------------- #
    ("no-tilt", ["--altitude", "--no-tilt"],
     "is the ability tilt on course difficulty earning its keep?"),
    ("no-race-day", ["--altitude", "--no-race-term"],
     "is the per-race day effect earning its keep?"),
    ("no-curve", ["--altitude", "--no-curve"],
     "is the 13-knot season form curve earning its keep?"),
    ("no-dist-bands", ["--altitude", "--no-dist-bands"],
     "are distance offsets BY RATING BAND earning their keep over one "
     "offset per event?"),
    ("no-altitude", [],
     "altitude off -- the only rung that omits --altitude"),
    ("slaney-shaped", ["--altitude", "--no-tilt", "--no-race-term",
                       "--no-dist-bands", "--no-rust", "--no-slope"],
     "athlete x course x a season trend, close to the published 4-parameter "
     "model. IF THIS IS NEAR base, OUR EXTRA TERMS ARE DECORATION"),

    # ---- and the four things we are considering adding ---------------- #
    ("top-25pct", ["--altitude", "--top-frac", "0.25"],
     "Slaney's filter: keep the fastest quarter of each race"),
    ("ability-weighted", ["--altitude", "--ability-weight"],
     "inverse-variance row weights by rating band -- the same idea without "
     "cutting on the outcome"),
    ("no-sigma-u-floor", ["--altitude", "--sigma-u-floor", "0,0"],
     "drop the stated race-day floor and let the data fit it"),
    ("free-tau", ["--altitude", "--tau-max", "none"],
     "drop the measured difficulty prior caps"),
    ("venue-day", ["--altitude", "--race-key", "venue"],
     "pool the race-day effect across every distance raced at a venue that "
     "day, instead of one per (venue, distance) cell"),

    # ---- the 2026-09-11 terms: each has to earn its keep ---------------- #
    ("diag-var", ["--altitude", "--diag-var"],
     "the old variance E-step (information diagonal) instead of the exact "
     "cell + races block -- does the honest posterior variance help?"),
    ("no-importance", ["--altitude", "--no-importance"],
     "no field-strength term (the race's front, from the model's own "
     "ratings): do the venues that host only stacked fields stay honest "
     "without it?"),
    ("season-end", ["--altitude", "--importance", "season-end"],
     "the taper covariate as the race's season-end share (from the "
     "athletes' calendars) instead of its front strength"),
    ("no-indoor", ["--altitude", "--no-indoor"],
     "no indoor term at all; every indoor cell carries the surface alone"),
    ("fit-indoor", ["--altitude", "--indoor-level", "fit"],
     "the indoor level FITTED instead of asserted at the NCAA factor -- "
     "indoor is season, so read this rung with the winter curve in mind"),
    ("era-2", ["--altitude", "--era-years", "2"],
     "each course split into two-year eras tied by a random walk (drift sd "
     "1%/era): a venue raced often moves, a once-a-year one holds still"),
    ("era-2-loose", ["--altitude", "--era-years", "2", "--era-drift", "0.03"],
     "the same eras with a 3% walk: a once-a-year venue can follow a real "
     "change, and also the weather mean of its few days"),
    ("no-dist-table", ["--altitude", "--no-dist-table"],
     "uncalibrated event offsets keep the zero prior instead of the "
     "published tables' relation"),
    ("stated-level", ["--altitude", "--sport-level", "0.0583"],
     "the XC/TF level ASSERTED at the stated grass cost instead of "
     "estimated. Sport is season, so a held-out RACE cannot score the level "
     "itself -- read this rung for what the free curve does to the rest"),

    # ---- the 2026-09-29 switches (owner: "try to be safe and test it") -- #
    ("season-tie", ["--altitude", "--season-tie"],
     "consecutive athlete-seasons of one person tied by a random walk "
     "fitted per transition: a thin season leans on its neighbours. Read "
     "the 1- and 2-row lines of the rung's log, not only the headline"),
    ("tilt-hs", ["--altitude", "--tilt-scale", "hs"],
     "the course tilt reads the HS-equivalent rating instead of the own-"
     "pool one. Read the by-pool lines of the rung's log"),

    # ---- the tuning grid (owner, 2026-10-07: "are there any hyperparams
    #      to tune?"). The stated knobs the joint solve exposes, one step
    #      either side of what ships (era-2: two-year eras, 1% drift; the
    #      distance walk at its default), every rung on the same sample and
    #      race split, so era-2 is the reference to read them against. A
    #      knob moves only when a neighbour beats era-2 on the race AND the
    #      forward holdout. The bracket engine's knobs (window, voter
    #      weight, priors) are not here: these holdouts score the joint
    #      solve; 08d's "SAME ROWS" scores the bracket.
    ("era-1", ["--altitude", "--era-years", "1"],
     "one-year eras: a venue can change every season"),
    ("era-3", ["--altitude", "--era-years", "3"],
     "three-year eras: more races per era, slower to follow a change"),
    ("era-2-tight", ["--altitude", "--era-years", "2", "--era-drift", "0.005"],
     "two-year eras with half the walk (0.5%/era): eras lean on each other more"),
    ("era-2-dist-0.01", ["--altitude", "--era-years", "2", "--dist-walk", "0.01"],
     "the distance law's walk halved: neighbouring event offsets tied tighter"),
    ("era-2-dist-0.04", ["--altitude", "--era-years", "2", "--dist-walk", "0.04"],
     "the distance law's walk doubled: each event offset freer"),
]

# the tuning set: the reference and its neighbours (--tune)
TUNE = ("era-2", "era-1", "era-3", "era-2-tight", "era-2-loose",
        "era-2-dist-0.01", "era-2-dist-0.04")


# ★ THE RUNGS A RUN NEEDS (2026-09-12, owner: "08b is slow as f and gets
#   stuck"). Twenty-one rungs is twenty-one solves, each a quarter of an hour
#   or more, the era rungs on three times the cells: eight hours, with
#   nothing printed while a rung runs. By default the ladder runs these;
#   --all runs every rung, --only names any subset.
CORE = ("base", "no-importance", "fit-indoor", "no-indoor", "era-2",
        "era-2-loose", "stated-level")


def rungCommand(flags, args):
    """The run_joint command line of one rung. Pure."""
    cmd = [args.python, "-u", os.path.join("engine", "run_joint.py"),
           # ! --holdout-only. With plain --holdout every rung scores its
           #   held-out races and THEN solves the full model on the sample
           #   and writes it over engine/data/joint_difficulty.npz -- 12
           #   solves nobody reads, and the last rung's leftovers under
           #   the diagnostics. The ladder reports; it writes nothing.
           "--holdout-only", "--holdout-kind", args.kind,
           "--sample-pct", str(args.pct), "--sample-seed", str(args.seed),
           "--outer", str(args.outer), "--probes", "0"]
    # ★ THE FORWARD SPLIT'S WINDOW (2026-09-29), the SAME for every rung: the
    #   pack's validation season unless one is named, and the sealed season
    #   only on --sealed (engine/forward_holdout.py)
    if args.kind == "forward":
        if getattr(args, "holdout_from", None):
            cmd += ["--holdout-from", args.holdout_from]
        if getattr(args, "holdout_until", None):
            cmd += ["--holdout-until", args.holdout_until]
        if getattr(args, "sealed", False):
            cmd += ["--sealed"]
    # ! EVERY RUNG CARRIES ITS OWN COMPLETE FLAGS, including --altitude, so
    #   the LADDER table is the whole truth about what each one ran and a
    #   test can parse it. Nothing is added here.
    return cmd + list(flags)


def runRung(name, flags, args):
    cmd = rungCommand(flags, args)
    if args.dry_run:
        print("  " + " ".join(cmd))
        return None
    t0 = time.time()
    # ★ STREAMED, LOGGED, TIMED OUT. The child's lines go to a per-rung log
    #   as they arrive, a heartbeat with the last [joint] line is printed
    #   every few minutes so a silent quarter-hour is visibly a solve and
    #   not a hang, and a rung past --rung-timeout is killed and recorded
    #   rather than holding the whole ladder.
    log_dir = os.path.join(os.path.dirname(args.out) or ".", "ladder_logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{name}.log")
    lines = []
    last_joint = ""
    timed_out = False
    print(f"  > {name}: started, log {log_path}", flush=True)
    with open(log_path, "w") as log:
        # ! ITS OWN PROCESS GROUP, so a timeout kills the solve's worker
        #   processes too; killing the python alone leaves them holding the
        #   pipe open and the read below would wait on them
        # the rung's held-out predictions, row by row, for the bracket
        # engine's comparison on the same rows (run_joint.holdout)
        env = dict(os.environ)
        env["XCP_HOLDOUT_DUMP"] = os.path.join(log_dir, f"{name}_holdout.npz")
        p = subprocess.Popen(cmd, cwd=_ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1,
                             start_new_session=True, env=env)
        next_beat = t0 + args.heartbeat
        try:
            import selectors
            sel = selectors.DefaultSelector()
            sel.register(p.stdout, selectors.EVENT_READ)
            while True:
                ready = sel.select(timeout=5.0)
                if ready:
                    line = p.stdout.readline()
                    if line == "":
                        break
                    lines.append(line)
                    log.write(line)
                    if line.startswith("[joint"):
                        last_joint = line.strip()
                now = time.time()
                if now >= next_beat:
                    print(f"    … {name} running {now - t0:.0f}s"
                          + (f"; last: {last_joint[:110]}" if last_joint else ""),
                          flush=True)
                    next_beat = now + args.heartbeat
                if args.rung_timeout and now - t0 > args.rung_timeout:
                    timed_out = True
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        p.kill()
                    break
        finally:
            if not timed_out:
                rest = p.stdout.read() if p.stdout else ""
                if rest:
                    lines.append(rest)
                    log.write(rest)
            p.wait()
    out = "".join(lines)
    print(f"  < {name}: {'TIMED OUT' if timed_out else 'exit ' + str(p.returncode)} "
          f"after {time.time() - t0:.0f}s", flush=True)
    if timed_out:
        return {"name": name, "error": f"timed out after {args.rung_timeout}s"}
    if p.returncode != 0:
        tail = "\n      ".join(out.strip().split("\n")[-6:])
        print(f"  ! {name} FAILED (exit {p.returncode})\n      {tail}")
        return {"name": name, "error": out.strip().split("\n")[-1] if out.strip() else "no output"}
    m = re.search(r"error sd ([0-9.]+)\s+covered ([0-9.]+)%", out)
    if not m:
        print(f"  ! {name}: no score in the output")
        return {"name": name, "error": "no score line"}
    rec = {"name": name, "sd": float(m.group(1)),
           "covered": float(m.group(2)) / 100.0,
           "secs": round(time.time() - t0, 1), "flags": flags}
    # the per-sport split, when the run printed it
    for code in ("XC", "TF"):
        mm = re.search(rf"^\s+{code}: ([0-9.]+)", out, re.M)
        if mm:
            rec[code.lower()] = float(mm.group(1))
    # the scorecard line (engine/forward_holdout.scoreLines), every split
    ms = re.search(r"\[joint\] scorecard: bias ([-+0-9.na]+)\s+median \|err\| "
                   r"([0-9.na]+)\s+p90 \|err\| ([0-9.na]+)\s+pairwise ([0-9.na]+)", out)
    if ms:
        rec.update(bias=float(ms.group(1)), med=float(ms.group(2)),
                   p90=float(ms.group(3)), pairwise=float(ms.group(4)))
    print(f"  {name:<18} {rec['sd']:.6f}   covered {rec['covered']:.0%}"
          + (f"   median |err| {rec['med']:.5f}  pairwise {rec['pairwise']:.4f}"
             if "med" in rec else "")
          + f"   [{rec['secs']:.0f}s]")
    return rec


# ★ ONE LADDER, TWO LANES (2026-11-24). Run 20261122_220225: the ladder's
#   seven rungs were 10,966 s in lane B after the sport holdout, while lane A
#   (race + distance holdouts, 4,645 s) sat idle for the last 2.7 hours, and
#   the whole run waited 2,908 s for lane B after its in-line steps were
#   done. With --claim-dir the lane-B ladder (the OWNER) and a --helper
#   started in lane A when its holdouts finish pull rungs from one list: a
#   rung belongs to whichever process creates <dir>/<rung>.claim first
#   (O_EXCL), and its record lands in <dir>/<rung>.json. The owner then
#   waits for the helper's rungs and prints the one report, in the ladder's
#   order, as before.
#  ! EACH RUNG IS THE SAME COMMAND, whichever process runs it: same flags,
#    sample, seed, env, thread count and per-rung log and dump, so every
#    number is the one the serial ladder prints (bit-identical: the solve is
#    deterministic at a fixed thread count).
#  ! THE HELPER IS ADMITTED LIKE A HOLDOUT: before it claims a rung it needs
#    --gb (XCP_HOLDOUT_GB, 38) + XCP_MEM_RESERVE_GB free after the memory
#    ledger's reservations (scripts/jobrun.py), and holds that reservation
#    while the rung runs. Without the memory it waits WITHOUT a claim, so the
#    owner simply runs the rest itself; a helper that dies mid-rung is seen
#    (its pid is gone and no record) and the owner reruns that rung.
#  ! Without --claim-dir nothing here runs: the serial loop, as before.
def _claimPath(d, name):
    return os.path.join(d, f"{name}.claim")


def _recPath(d, name):
    return os.path.join(d, f"{name}.json")


def _claim(d, name):
    try:
        fd = os.open(_claimPath(d, name), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))
    return True


def _claimant(d, name):
    try:
        with open(_claimPath(d, name)) as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _record(d, name, rec):
    rec = rec if rec is not None else {"name": name, "error": "no result"}
    tmp = _recPath(d, name) + f".tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(rec, fh)
    os.replace(tmp, _recPath(d, name))


def _readRecord(d, name):
    try:
        with open(_recPath(d, name)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _admitHelper(gb):
    """A ledger entry for this helper (pid = this process, so its rung's
    tree counts against it), or None when the box has no room now."""
    sys.path.insert(0, _HERE)
    import jobrun
    need = gb + float(os.environ.get("XCP_MEM_RESERVE_GB", "16"))
    with jobrun._GateLock():
        jobs, _wants = jobrun._entries()
        free = jobrun.memAvailableGb() - jobrun.unspentGb(jobs)
        if free < need:
            return None, f"{free:.0f} GB free after reservations, needs {need:.0f}"
        path = os.path.join(jobrun.GATE_DIR, f"{os.getpid()}.json")
        jobrun._write(path, {"name": "08b_ladder helper", "pid": os.getpid(),
                             "gb": gb, "since": int(time.time())})
    return path, f"{free:.0f} GB free, needs {need:.0f}"


def _printRec(rec):
    if "sd" in rec:
        print(f"  {rec['name']:<18} {rec['sd']:.6f}   covered {rec['covered']:.0%}"
              + (f"   median |err| {rec['med']:.5f}  pairwise {rec['pairwise']:.4f}"
                 if "med" in rec else "")
              + f"   [{rec.get('secs', 0):.0f}s]", flush=True)
    else:
        print(f"  ! {rec['name']}: {rec.get('error', 'failed')}", flush=True)


def runShared(rungs, args):
    d = args.claim_dir
    os.makedirs(d, exist_ok=True)
    if args.helper:
        last_say = 0.0
        while True:
            if all(os.path.exists(_claimPath(d, n)) for n, _f, _w in rungs):
                return []
            entry, why = _admitHelper(args.gb)
            if entry is None:
                if time.time() - last_say >= 300:
                    print(f"  helper: waiting for memory ({why}); the owner runs "
                          f"the rungs meanwhile", flush=True)
                    last_say = time.time()
                time.sleep(15)
                continue
            try:
                got = next(((n, f) for n, f, _w in rungs if _claim(d, n)), None)
                if got is None:
                    return []
                print(f"  helper: {got[0]} admitted ({why})", flush=True)
                _record(d, got[0], runRung(got[0], got[1], args))
            finally:
                try:
                    os.remove(entry)
                except OSError:
                    pass
    done = {}
    for name, flags, _why in rungs:
        if _claim(d, name):
            done[name] = runRung(name, flags, args)
            _record(d, name, done[name])
    for name, flags, _why in rungs:
        if name in done:
            continue
        t_wait = time.time()
        next_beat = t_wait + args.heartbeat
        while True:
            rec = _readRecord(d, name)
            if rec is not None:
                print(f"  < {name}: run by the helper (lane A)", flush=True)
                _printRec(rec)
                done[name] = rec
                break
            if not _alive(_claimant(d, name)):
                print(f"  ! {name}: its helper is gone without a result -- "
                      f"running it here", flush=True)
                done[name] = runRung(name, flags, args)
                _record(d, name, done[name])
                break
            if time.time() >= next_beat:
                print(f"    … waiting {time.time() - t_wait:.0f}s for {name} "
                      f"(the helper, pid {_claimant(d, name)})", flush=True)
                next_beat = time.time() + args.heartbeat
            time.sleep(5)
    return [done[n] for n, _f, _w in rungs if done.get(n)]


def report(rows, kind):
    ok = [r for r in rows if "sd" in r]
    if not ok:
        print("\nno rung produced a score")
        return
    base = next((r for r in ok if r["name"] == "base"), None)
    print("\n" + "=" * 78)
    print(f"ABLATION LADDER -- held out {kind}s, lower is better")
    print("=" * 78)
    hdr = (f"  {'rung':<18} {'error sd':>10} {'vs base':>10} {'XC':>9} {'TF':>9}"
           f" {'bias':>9} {'med|e|':>8} {'pairwise':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in sorted(ok, key=lambda r: r["sd"]):
        delta = ("" if base is None or r is base
                 else f"{100 * (r['sd'] / base['sd'] - 1):+.2f}%")
        print(f"  {r['name']:<18} {r['sd']:>10.6f} {delta:>10} "
              f"{r.get('xc', float('nan')):>9.5f} "
              f"{r.get('tf', float('nan')):>9.5f} "
              f"{r.get('bias', float('nan')):>+9.5f} "
              f"{r.get('med', float('nan')):>8.5f} "
              f"{r.get('pairwise', float('nan')):>9.4f}")
    print("  (the SE of a change is by race: scripts/switch_scorecard.py --holdout "
          "on two rungs' ladder_logs/<rung>_holdout.npz)")
    for r in rows:
        if "sd" not in r:
            print(f"  {r['name']:<18} {'FAILED':>10}   {r.get('error', '')[:40]}")

    # ★ THE ONE QUESTION THIS EXISTS TO ANSWER.
    sl = next((r for r in ok if r["name"] == "slaney-shaped"), None)
    if base and sl:
        gap = 100 * (sl["sd"] / base["sd"] - 1)
        print(f"\n  Slaney-shaped is {gap:+.2f}% against the full model.")
        if gap < 1.0:
            print("  => OUR EXTRA TERMS ARE NOT EARNING THEIR KEEP. A model "
                  "with a fraction of\n     the parameters is within one per "
                  "cent. Cut something.")
        elif gap < 5.0:
            print("  => the extra terms help, but modestly. Worth asking "
                  "which ones, rung by\n     rung, before adding more.")
        else:
            print("  => the extra terms are doing real work.")
    if base:
        better = [r for r in ok if r["sd"] < base["sd"] - 1e-9
                  and r["name"] != "base"]
        if better:
            print("\n  BEATS THE SHIPPED MODEL: "
                  + ", ".join(f"{r['name']} ({100*(r['sd']/base['sd']-1):+.2f}%)"
                              for r in better))
        else:
            print("\n  Nothing beat the shipped model on this sample.")
    print("\n  ⚠ This table REPORTS. Pick what the next run carries yourself:")
    print("    one number, computed once, on a sample, is not a mandate.")


def main():
    ap = argparse.ArgumentParser(
        description="Score the model and its ablations on one held-out set.")
    ap.add_argument("--pct", type=float, default=15.0,
                    help="percent of ATHLETES per rung (default 15)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--kind", default="race",
                    choices=["row", "race", "athlete", "course", "forward"],
                    help="what to hold out; 'forward' scores every rung on "
                         "the validation season from a fit on the seasons "
                         "before it (engine/forward_holdout.py)")
    ap.add_argument("--holdout-from", default=None, metavar="YYYY-MM-DD",
                    help="--kind forward: the window's first day (default "
                         "the pack's validation season)")
    ap.add_argument("--holdout-until", default=None, metavar="YYYY-MM-DD",
                    help="--kind forward: the window's end (exclusive)")
    ap.add_argument("--sealed", action="store_true",
                    help="--kind forward: score the SEALED test season. Once")
    ap.add_argument("--outer", type=int, default=5)
    ap.add_argument("--only", help="comma list of rung names")
    ap.add_argument("--all", action="store_true",
                    help=f"every rung; the default is the core set {', '.join(CORE)}")
    ap.add_argument("--tune", action="store_true",
                    help=f"the tuning set instead of the core one: {', '.join(TUNE)}")
    ap.add_argument("--rung-timeout", type=float, default=7200.0,
                    help="seconds a rung may run before it is killed and "
                         "recorded (default 7200; 0 = no limit)")
    ap.add_argument("--heartbeat", type=float, default=180.0,
                    help="seconds between progress lines while a rung runs")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default=os.path.join(_ROOT, "engine", "data",
                                                  "ablation_ladder.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--claim-dir", default=None, metavar="DIR",
                    help="share the rungs with a --helper through claim files "
                         "in DIR (one fresh directory per run): each rung is "
                         "run by whichever process claims it first")
    ap.add_argument("--helper", action="store_true",
                    help="with --claim-dir: run unclaimed rungs while there is "
                         "memory for one (--gb), report nothing; the owner (the "
                         "run without --helper) collects every rung and reports")
    ap.add_argument("--gb", type=float,
                    default=float(os.environ.get("XCP_HOLDOUT_GB") or 38),
                    help="--helper: GB reserved per rung in the memory ledger "
                         "(scripts/jobrun.py), as a holdout is (default "
                         "XCP_HOLDOUT_GB, 38)")
    args = ap.parse_args()
    if args.helper and not args.claim_dir:
        ap.error("--helper needs --claim-dir")

    rungs = (LADDER if args.all else
             [r for r in LADDER if r[0] in TUNE] if getattr(args, "tune", False) else
             [r for r in LADDER if r[0] in CORE])
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        rungs = [r for r in LADDER if r[0] in want]
        missing = want - {r[0] for r in rungs}
        if missing:
            sys.exit(f"unknown rung(s): {', '.join(sorted(missing))}")

    print(f"\nablation ladder: {len(rungs)} rungs, {args.pct}% of athletes, "
          f"holding out {args.kind}s\n")
    for _n, _f, why in rungs:
        print(f"  {_n:<18} {why}")
    print()

    rows = []
    t0 = time.time()
    if args.claim_dir and not args.dry_run:
        rows = runShared(rungs, args)
        if args.helper:
            print(f"\n  helper done after {time.time() - t0:.0f}s; the owner reports")
            return 0
    else:
        for name, flags, _why in rungs:
            rec = runRung(name, flags, args)
            if rec:
                rows.append(rec)
    if args.dry_run:
        return 0
    report(rows, args.kind)
    print(f"\n  total {time.time() - t0:.0f}s")
    try:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump({"kind": args.kind, "pct": args.pct,
                       "seed": args.seed, "holdout_from": args.holdout_from,
                       "holdout_until": args.holdout_until,
                       "sealed": bool(args.sealed), "rows": rows}, f, indent=2)
        print(f"  wrote {args.out}")
    except OSError as exc:
        print(f"  ! could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
