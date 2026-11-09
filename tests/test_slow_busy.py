"""The slow-request notice names what was busy (app_errors.busyProcesses,
2026-11-09): past three quarters of the cores, the R and D state processes
grouped by what they are, read from /proc."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

import app_errors as E  # noqa: E402


def _proc(tmp_path, procs):
    for pid, (state, cmd) in procs.items():
        d = tmp_path / str(pid)
        d.mkdir()
        comm = os.path.basename(cmd.split()[0])[:15]
        (d / "stat").write_text(f"{pid} ({comm}) {state} 1 1 1 0 -1")
        (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")
    return str(tmp_path)


def test_busy_groups_running_and_disk_waiting_processes(tmp_path):
    root = _proc(tmp_path, {
        101: ("R", "postgres: postgres xc_predictor 127.0.0.1(4411) SELECT"),
        102: ("D", "postgres: postgres xc_predictor 127.0.0.1(4412) SELECT"),
        103: ("D", "postgres: parallel worker for PID 101"),
        104: ("R", "/srv/venv/bin/python -u model/race_rows.py --out x"),
        105: ("S", "/srv/venv/bin/gunicorn -w 24 app:app"),
        106: ("R", "/srv/venv/bin/gunicorn -w 24 app:app"),
    })
    out = E.busyProcesses(proc=root, now=0)
    assert out.startswith("postgres SELECT 2 (D 1)")
    assert "postgres parallel worker 1 (D 1)" in out
    assert "python race_rows.py 1" in out and "gunicorn 1" in out


def test_quiet_box_names_nothing(tmp_path):
    root = _proc(tmp_path, {7: ("S", "/usr/sbin/nginx")})
    assert E.busyProcesses(proc=root, now=0) == ""


def test_the_context_adds_it_only_under_load(monkeypatch):
    monkeypatch.setattr(E, "busyProcesses", lambda **k: "pg_dump 3")
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    monkeypatch.setattr(os, "getloadavg", lambda: (39.3, 20.8, 10.0))
    assert "busy: pg_dump 3" in E.serverContext(log_root="/nonexistent")
    monkeypatch.setattr(os, "getloadavg", lambda: (3.0, 2.0, 1.0))
    assert "busy" not in E.serverContext(log_root="/nonexistent")
