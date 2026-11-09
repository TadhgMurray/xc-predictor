"""deploy/lockctl.sh and scripts/jobrun.py (2026-11-09, back-end track;
docs/PIPELINE-ORDER.md): who holds .pipeline.lock and why, queued jobs that
never jump an interactive run, and memory admitted by reservation."""
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBRUN = os.path.join(ROOT, "scripts", "jobrun.py")
LOCKCTL = os.path.join(ROOT, "deploy", "lockctl.sh")


def _avail_gb():
    with open("/proc/meminfo") as fh:
        for ln in fh:
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / 1048576.0
    return 0.0


def _env(tmp_path):
    e = dict(os.environ, XCP_MEMGATE_DIR=str(tmp_path / "gate"), XCP_JOBRUN_POLL_S="0.3")
    return e


def _jobrun(tmp_path, *args, **kw):
    return subprocess.Popen([sys.executable, JOBRUN, *args], env=_env(tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)


def test_jobrun_measures_peak_and_keeps_the_exit_code(tmp_path):
    out = tmp_path / "m"
    p = _jobrun(tmp_path, "--name", "m", "--peak-out", str(out), "--",
                sys.executable, "-c",
                "import sys,time; x=bytearray(150*2**20); time.sleep(1.5); sys.exit(3)")
    p.communicate(timeout=30)
    assert p.returncode == 3
    t = out.read_text()
    peak = float(t.split("peak_gb=")[1].split()[0])
    assert 0.1 < peak < 2, t


def test_two_replays_cannot_both_take_more_than_half(tmp_path):
    g = f"{_avail_gb() * 0.6:.1f}"
    r1 = _jobrun(tmp_path, "--name", "r1", "--gb", g, "--min-free-gb", g,
                 "--sacrificial", "--", "sleep", "4")
    time.sleep(1.0)
    r2 = _jobrun(tmp_path, "--name", "r2", "--gb", g, "--min-free-gb", g,
                 "--sacrificial", "--wait-min", "0.02", "--", "echo", "ran")
    o2, _ = r2.communicate(timeout=30)
    assert r2.returncode == 75 and "ran" not in o2.split("not admitted")[0]
    r1.communicate(timeout=30)
    assert r1.returncode == 0


def test_the_golive_stops_a_replay_in_its_way(tmp_path):
    g = f"{_avail_gb() * 0.6:.1f}"
    r1 = _jobrun(tmp_path, "--name", "replay", "--gb", g, "--min-free-gb", g,
                 "--sacrificial", "--", "sleep", "60")
    time.sleep(1.0)
    t0 = time.time()
    gl = _jobrun(tmp_path, "--name", "golive", "--gb", g, "--min-free-gb", g,
                 "--priority", "--wait-min", "1", "--", "echo", "golive-ran")
    o, _ = gl.communicate(timeout=60)
    o1, _ = r1.communicate(timeout=60)
    assert gl.returncode == 0 and "golive-ran" in o
    assert r1.returncode == 75 and "STOPPING" in o1
    assert time.time() - t0 < 40


def test_a_standing_want_keeps_replays_from_starting(tmp_path):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        subprocess.run([sys.executable, JOBRUN, "--want-standing", str(holder.pid), "--gb", "1"],
                       env=_env(tmp_path), check=True)
        r = _jobrun(tmp_path, "--name", "replay", "--gb", "0.1", "--min-free-gb", "0.1",
                    "--sacrificial", "--wait-min", "0.02", "--", "echo", "ran")
        o, _ = r.communicate(timeout=30)
        assert r.returncode == 75 and "solve is ahead" in o
        subprocess.run([sys.executable, JOBRUN, "--unwant", str(holder.pid)],
                       env=_env(tmp_path), check=True)
        r = _jobrun(tmp_path, "--name", "replay", "--gb", "0.1", "--min-free-gb", "0.1",
                    "--sacrificial", "--wait-min", "0.02", "--", "echo", "ran")
        o, _ = r.communicate(timeout=30)
        assert r.returncode == 0 and "ran" in o
    finally:
        holder.kill()


def _bash(tmp_path, script, timeout=60):
    return subprocess.run(["bash", "-c", f'ROOT="{tmp_path}"; . "{LOCKCTL}"; {script}'],
                          capture_output=True, text=True, timeout=timeout)


def test_a_run_waits_for_a_queued_job_instead_of_failing(tmp_path):
    q = subprocess.Popen(["bash", "-c", f'ROOT="{tmp_path}"; . "{LOCKCTL}"; '
                          'lock_take backup queued 30 "dump"; sleep 3; lock_release'])
    time.sleep(1)
    r = _bash(tmp_path, 'lock_take pipeline exclusive 30 "run" && echo GOT && cat "$LOCK_HOLDER"')
    q.wait(timeout=30)
    assert "GOT" in r.stdout and "job=pipeline" in r.stdout, r.stderr
    assert "queued job 'backup'" in r.stderr


def test_a_run_holding_it_is_still_fatal(tmp_path):
    p = subprocess.Popen(["bash", "-c", f'ROOT="{tmp_path}"; . "{LOCKCTL}"; '
                          'lock_take pipeline exclusive 0 "run"; sleep 3'])
    time.sleep(1)
    r = _bash(tmp_path, 'lock_take nightly exclusive 30 "night" && echo GOT')
    p.wait(timeout=30)
    assert "GOT" not in r.stdout and "FATAL" in r.stderr and "job=pipeline" in r.stderr


def test_a_queued_job_does_not_jump_a_waiting_run(tmp_path):
    # a backup holds it, a run waits (want posted), the race model queues:
    # when the backup lets go the RUN gets it, not the race model
    script = f'''
      ROOT="{tmp_path}"; . "{LOCKCTL}"
      ( lock_take backup queued 30 dump; sleep 2; lock_release ) &
      sleep 0.5
      ( lock_take pipeline exclusive 30 run && echo "pipeline $(date +%s%N)"; sleep 2 ) &
      sleep 0.5
      ( lock_take race_model queued 60 rows && echo "race_model $(date +%s%N)" ) &
      wait
    '''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=90)
    order = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
    assert order == ["pipeline", "race_model"], (r.stdout, r.stderr)


def test_every_lock_taker_goes_through_lockctl():
    for f in ("run_pipeline.sh", "nightly_update.sh", "backup_db.sh", "race_model_weekly.sh"):
        s = open(os.path.join(ROOT, "deploy", f)).read()
        assert "lock_take " in s, f
        assert 'exec 9>"$ROOT/.pipeline.lock"' not in s, f


def test_the_delayed_purge_does_not_hold_the_lock():
    s = open(os.path.join(ROOT, "deploy", "run_pipeline.sh")).read()
    line = next(ln for ln in s.splitlines() if "edge_purge.py --delay" in ln and "nohup" in ln)
    assert "9>&-" in line


def test_the_replay_and_the_golive_go_through_the_ledger():
    rp = open(os.path.join(ROOT, "deploy", "engine_replay.sh")).read()
    assert "--sacrificial" in rp and "jobrun.py" in rp
    pl = open(os.path.join(ROOT, "deploy", "run_pipeline.sh")).read()
    i = pl.index('step 08_golive        "$PY" -u engine/run_joint.py --golive')
    assert "--priority" in pl[i - 300:i]
    assert "--want-standing $$" in pl and "--unwant $$" in pl


def test_who_holds_the_lock_reads_proc_locks():
    s = open(os.path.join(ROOT, "scripts", "who_holds_the_lock.sh")).read()
    assert "/proc/locks" in s and ".holder" in s
