#!/usr/bin/env python3
"""scripts/jobrun.py -- run one job: measure its memory, and when asked,
admit it only when the box has the memory for it.

    jobrun.py [--name N] [--peak-out FILE] -- cmd ...         # measure only
    jobrun.py --name replay_2025-10-01 --gb 60 --min-free-gb 70 \
              --sacrificial --cap-gb 80 --wait-min 1440 -- cmd ...
    jobrun.py --status                                         # the ledger

★ WHY (2026-11-09, the back-end track). Two things the pipeline could not do:
  1. SAY WHAT A STEP COST IN MEMORY. summary.log had seconds, never bytes.
     Every step now runs under this wrapper, which follows the step's whole
     process tree (the step's python, its sh -c, anything it starts) and
     writes the peak RSS and the lowest MemAvailable seen while it ran to
     --peak-out. Postgres backends are not in the tree; MemAvailable covers
     them.
  2. KEEP MEMORY-HEAVY JOBS FROM PILING UP. engine_replay.sh checked
     "70 GB free" and then started a ~60 GB solve -- and two sessions
     checking at the same moment both saw 70 GB and both started (about
     1.5 GB left beside the site and the pipeline, owner, 2026-11-09). A
     check is not a reservation. Here admission is decided under one flock
     and written to a ledger ($ROOT/.memgate/<pid>.json) before the job
     starts; the next job's free memory is MemAvailable minus what admitted
     jobs have reserved and not yet grown into.

THE RULES (docs/PIPELINE-ORDER.md says the same in prose):
  * --gb G           reserve G GB for this job while it runs.
  * --min-free-gb F  start only when (MemAvailable - others' unspent
                     reservations) >= F. Default G + XCP_MEM_RESERVE_GB (16).
  * --priority       a pipeline solve. While it waits it posts an URGENT want:
                     sacrificial jobs that are running and in its way stop
                     themselves (exit 75), and none start. After --wait-min
                     it runs anyway, saying so.
  * --sacrificial    an engine replay: restartable, worth nothing half-done.
                     It never starts while any want (urgent or standing) is
                     posted; it stops itself (exit 75, "preempted") when an
                     urgent want cannot be met, or when MemAvailable falls
                     under XCP_MEM_FLOOR_GB (8) -- the site's floor.
  * --cap-gb C       stop the job (exit 137) if its tree passes C GB.
  * a plain job (no --priority, no --sacrificial) waits up to --wait-min and
    then exits 75 without running.
  * --want-standing PID  post a standing want for PID (the pipeline, from its
    start until its last solve): no sacrificial job starts meanwhile. Removed
    with --unwant PID, and ignored once PID is gone.

Exit code: the job's own (128+signal when it was killed by one), or 75 when
it was not admitted or was preempted.
"""
import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.environ.get("XCP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_DIR = os.environ.get("XCP_MEMGATE_DIR") or os.path.join(ROOT, ".memgate")
EX_PREEMPTED = 75
GIB = 1024 ** 3
PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def say(msg):
    print(f"[jobrun] {time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def memAvailableGb():
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1048576.0
    except OSError:
        pass
    return float("inf")


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _procTable():
    """{pid: (ppid, rss_bytes)} from /proc/*/stat -- nothing to install."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat", "rb") as fh:
                s = fh.read().decode("ascii", "replace")
        except OSError:
            continue
        # comm can hold spaces and parens: split after the LAST ')'
        rest = s[s.rfind(")") + 2:].split()
        try:
            out[int(d)] = (int(rest[1]), int(rest[21]) * PAGE)
        except (IndexError, ValueError):
            continue
    return out


def treeRss(root_pid, table=None):
    """Bytes resident in root_pid and every descendant."""
    table = table if table is not None else _procTable()
    kids = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    total, stack, seen = 0, [root_pid], set()
    while stack:
        p = stack.pop()
        if p in seen or p not in table:
            continue
        seen.add(p)
        total += table[p][1]
        stack.extend(kids.get(p, ()))
    return total


# ---- the ledger -------------------------------------------------------- #

def _entries():
    """Live ledger entries and wants; dead ones are removed as found."""
    jobs, wants = [], []
    for f in glob.glob(os.path.join(GATE_DIR, "*.json")):
        try:
            with open(f) as fh:
                e = json.load(fh)
        except (OSError, ValueError):
            continue
        if not alive(e.get("pid")):
            try:
                os.remove(f)
            except OSError:
                pass
            continue
        e["_file"] = f
        (wants if os.path.basename(f).startswith("want.") else jobs).append(e)
    return jobs, wants


def unspentGb(jobs, table=None, skip_pid=None):
    """What admitted jobs have reserved and not yet grown into."""
    table = table if table is not None else _procTable()
    t = 0.0
    for e in jobs:
        if e.get("pid") == skip_pid:
            continue
        rss = treeRss(int(e.get("child") or e["pid"]), table) / GIB
        t += max(0.0, float(e.get("gb", 0)) - rss)
    return t


def _write(path, obj):
    os.makedirs(GATE_DIR, exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


class _GateLock:
    """flock on $GATE_DIR/.lock: admission is decided by one job at a time."""

    def __enter__(self):
        import fcntl
        os.makedirs(GATE_DIR, exist_ok=True)
        self.fh = open(os.path.join(GATE_DIR, ".lock"), "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        self.fh.close()


def describe(jobs, wants, table=None):
    table = table if table is not None else _procTable()
    parts = []
    for e in jobs:
        rss = treeRss(int(e.get("child") or e["pid"]), table) / GIB
        parts.append(f"{e.get('name')} (pid {e['pid']}, {rss:.0f}/{float(e.get('gb', 0)):.0f} GB"
                     f"{', sacrificial' if e.get('sacrificial') else ''})")
    for w in wants:
        parts.append(f"WANT {w.get('name')} (pid {w['pid']}, {float(w.get('gb', 0)):.0f} GB, "
                     f"{'urgent' if w.get('urgent') else 'standing'})")
    return "; ".join(parts) or "nothing"


def admit(a, me_file, want_file):
    """Wait for room; write the ledger entry. True when admitted."""
    need = a.min_free_gb if a.min_free_gb is not None else \
        a.gb + float(os.environ.get("XCP_MEM_RESERVE_GB", "16"))
    deadline = time.time() + a.wait_min * 60
    last_say = 0.0
    posted_want = False
    while True:
        with _GateLock():
            table = _procTable()
            jobs, wants = _entries()
            other_wants = [w for w in wants if w.get("pid") != os.getpid()]
            free = memAvailableGb() - unspentGb(jobs, table)
            blocked_by_want = a.sacrificial and other_wants
            if not blocked_by_want and free >= need:
                _write(me_file, {"name": a.name, "pid": os.getpid(), "gb": a.gb,
                                 "sacrificial": a.sacrificial, "priority": a.priority,
                                 "since": int(time.time())})
                if posted_want:
                    try:
                        os.remove(want_file)
                    except OSError:
                        pass
                say(f"{a.name}: admitted ({free:.0f} GB free after reservations, needs {need:.0f})")
                return True
            if a.priority and not posted_want:
                _write(want_file, {"name": a.name, "pid": os.getpid(), "gb": need,
                                   "urgent": True, "since": int(time.time())})
                posted_want = True
            why = "a pipeline solve is ahead" if blocked_by_want else \
                f"{free:.0f} GB free after reservations, needs {need:.0f}"
            holders = describe(jobs, wants, table)
        now = time.time()
        if now >= deadline:
            if posted_want:
                try:
                    os.remove(want_file)
                except OSError:
                    pass
            if a.priority:
                say(f"{a.name}: waited {a.wait_min} min for memory ({why}; holding: {holders}) "
                    f"-- RUNNING ANYWAY, it is the pipeline's own solve")
                with _GateLock():
                    _write(me_file, {"name": a.name, "pid": os.getpid(), "gb": a.gb,
                                     "priority": True, "since": int(time.time())})
                return True
            say(f"{a.name}: not admitted after {a.wait_min} min ({why}; holding: {holders})")
            return False
        if now - last_say >= 300:
            say(f"{a.name}: waiting for memory -- {why}; holding: {holders}")
            last_say = now
        time.sleep(min(15, max(1, deadline - now)))


def _killTree(proc, grace=30):
    try:
        proc.terminate()
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
    except OSError:
        pass
    # anything it left behind under it
    table = _procTable()
    kids = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    stack = list(kids.get(proc.pid, ()))
    while stack:
        p = stack.pop()
        stack.extend(kids.get(p, ()))
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass


def run(a, cmd):
    me_file = os.path.join(GATE_DIR, f"{os.getpid()}.json")
    want_file = os.path.join(GATE_DIR, f"want.{os.getpid()}.json")
    gated = a.gb is not None
    if gated and not admit(a, me_file, want_file):
        return EX_PREEMPTED
    floor = float(os.environ.get("XCP_MEM_FLOOR_GB", "8"))
    interval = float(os.environ.get("XCP_JOBRUN_POLL_S", "2"))
    t0 = time.time()
    peak = 0
    min_avail = memAvailableGb()
    stop_reason = None
    try:
        proc = subprocess.Popen(cmd)
    except OSError as exc:
        say(f"{a.name}: cannot start {cmd[0]}: {exc}")
        _cleanup(me_file, want_file)
        return 127

    def _fwd(sig, _frm):
        try:
            proc.send_signal(sig)
        except OSError:
            pass
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, _fwd)

    if gated:
        with _GateLock():
            try:
                with open(me_file) as fh:
                    e = json.load(fh)
                e["child"] = proc.pid
                _write(me_file, e)
            except (OSError, ValueError):
                pass
    warned_low = False
    try:
        while True:
            try:
                rc = proc.wait(timeout=interval)
                break
            except subprocess.TimeoutExpired:
                pass
            table = _procTable()
            rss = treeRss(proc.pid, table)
            peak = max(peak, rss)
            avail = memAvailableGb()
            min_avail = min(min_avail, avail)
            if a.cap_gb and rss / GIB > a.cap_gb:
                stop_reason = (137, f"its processes reached {rss / GIB:.1f} GB, over its cap of "
                                    f"{a.cap_gb:.0f} GB")
            elif a.sacrificial and avail < floor:
                stop_reason = (EX_PREEMPTED, f"the box is down to {avail:.1f} GB available "
                                             f"(floor {floor:.0f} GB): it yields to the site")
            elif a.sacrificial:
                _, wants = _entries()
                urgent = [w for w in wants if w.get("urgent")]
                if urgent:
                    jobs, _ = _entries()
                    # the waiter's own arithmetic (admit): this job's
                    # unspent reservation counts against it too
                    free = avail - unspentGb(jobs, table)
                    w = urgent[0]
                    if free < float(w.get("gb", 0)):
                        stop_reason = (EX_PREEMPTED, f"{w.get('name')} needs {float(w['gb']):.0f} GB "
                                                     f"and only {free:.0f} GB is free: it goes first")
            elif avail < floor and not warned_low:
                say(f"{a.name}: ! the box is down to {avail:.1f} GB available "
                    f"(this step: {rss / GIB:.1f} GB)")
                warned_low = True
            if stop_reason:
                say(f"{a.name}: STOPPING -- {stop_reason[1]}")
                _killTree(proc)
                rc = stop_reason[0]
                break
    finally:
        _cleanup(me_file, want_file)
    if rc is not None and rc < 0:
        rc = 128 - rc
    el = time.time() - t0
    if a.peak_out:
        try:
            os.makedirs(os.path.dirname(a.peak_out) or ".", exist_ok=True)
            with open(a.peak_out, "w") as fh:
                fh.write(f"peak_gb={peak / GIB:.2f} min_avail_gb={min_avail:.1f} "
                         f"seconds={el:.0f} rc={rc}\n")
        except OSError:
            pass
    return rc


def _cleanup(*files):
    for f in files:
        try:
            os.remove(f)
        except OSError:
            pass


def status():
    jobs, wants = _entries()
    print(f"MemAvailable {memAvailableGb():.1f} GB; unspent reservations "
          f"{unspentGb(jobs):.1f} GB")
    print("ledger: " + describe(jobs, wants))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default=None)
    ap.add_argument("--peak-out", default=None)
    ap.add_argument("--gb", type=float, default=None)
    ap.add_argument("--min-free-gb", type=float, default=None)
    ap.add_argument("--wait-min", type=float, default=120)
    ap.add_argument("--priority", action="store_true")
    ap.add_argument("--sacrificial", action="store_true")
    ap.add_argument("--cap-gb", type=float, default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--want-standing", type=int, default=None, metavar="PID")
    ap.add_argument("--unwant", type=int, default=None, metavar="PID")
    a = ap.parse_args(argv)
    if a.status:
        return status()
    if a.want_standing is not None:
        with _GateLock():
            _write(os.path.join(GATE_DIR, f"want.{a.want_standing}.json"),
                   {"name": a.name or "pipeline (solve ahead)", "pid": a.want_standing,
                    "gb": a.gb or 0, "urgent": False, "since": int(time.time())})
        return 0
    if a.unwant is not None:
        _cleanup(os.path.join(GATE_DIR, f"want.{a.unwant}.json"))
        return 0
    if not cmd:
        ap.error("no command after --")
    a.name = a.name or os.path.basename(cmd[0])
    return run(a, cmd)


if __name__ == "__main__":
    sys.exit(main())
