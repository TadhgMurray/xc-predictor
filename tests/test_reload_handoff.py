"""systemctl reload = gunicorn USR2 + deploy/gunicorn.conf.py's handoff
(2026-11-09): the new master loads the new code while the old one serves,
tells systemd its PID (MAINPID), and stops the old master -- no request
fails or waits on the boot."""
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_usr2_reload_hands_off_without_a_failed_request(tmp_path):
    pytest.importorskip("gunicorn")
    version = tmp_path / "version"
    version.write_text("v1")
    notify_path = str(tmp_path / "notify.sock")
    notify = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    notify.bind(notify_path)
    notify.settimeout(0.2)
    port = _free_port()
    env = dict(os.environ, NOTIFY_SOCKET=notify_path, XCP_PRELOAD="0",
               XCP_WARM_SITE="0", XCP_HANDOFF_DELAY="1",
               HANDOFF_VERSION_FILE=str(version), HANDOFF_BOOT_S="3",
               PYTHONPATH=os.path.join(_ROOT, "tests", "data"))
    master = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "-c", os.path.join(_ROOT, "deploy", "gunicorn.conf.py"),
         "-w", "2", "-b", f"127.0.0.1:{port}", "--preload", "handoff_app:app"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    msgs = []

    def drain():
        try:
            while True:
                msgs.append(notify.recv(4096).decode())
        except socket.timeout:
            pass
    try:
        url = f"http://127.0.0.1:{port}/"
        deadline = time.time() + 30
        while True:
            try:
                urllib.request.urlopen(url, timeout=2).read()
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.2)
        drain()
        assert any(m.startswith(f"MAINPID={master.pid}") for m in msgs), msgs

        fails, seen, stop = [], [], threading.Event()

        def traffic():
            while not stop.is_set():
                t = time.time()
                try:
                    body = urllib.request.urlopen(url, timeout=10).read().decode()
                    seen.append((body, time.time() - t))
                except Exception as exc:                    # noqa: BLE001
                    fails.append(repr(exc))
                time.sleep(0.02)
        th = threading.Thread(target=traffic)
        th.start()
        time.sleep(0.5)
        version.write_text("v2")                            # the deploy's new code
        os.kill(master.pid, signal.SIGUSR2)
        deadline = time.time() + 40
        while master.poll() is None and time.time() < deadline:
            drain()
            time.sleep(0.2)
        time.sleep(1.0)
        stop.set()
        th.join()
        drain()
        assert master.poll() is not None, "the old master did not stop"
        new = [m for m in msgs if m.startswith("MAINPID=") and not m.startswith(f"MAINPID={master.pid}")]
        assert new, msgs
        assert not fails, fails[:3]
        assert seen[-1][0].startswith("v2")
        # the 3 s boot was never a visitor's wait
        assert max(t for _b, t in seen) < 2.0, max(t for _b, t in seen)
        new_pid = int(new[0].split("\n")[0].split("=")[1])
    finally:
        for pid in [master.pid] + ([new_pid] if "new_pid" in locals() else []):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        notify.close()
