"""The network's thread count follows the box's load (2026-10-18): an idle
box lends a prediction its spare cores, up to half; a busy one gets the old
one-worker share; XCP_TORCH_THREADS, when set, is the count.

    python -m pytest -q tests/test_torch_threads_now.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "model", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import predict as P                                              # noqa: E402


def test_follows_the_load(monkeypatch):
    monkeypatch.delenv("XCP_TORCH_THREADS", raising=False)
    assert P._torchThreadsNow(cpus=8, load=0.2) == 4      # idle: half the box
    assert P._torchThreadsNow(cpus=8, load=5.5) == 2      # two cores spare
    assert P._torchThreadsNow(cpus=8, load=12.0) == 1     # busy: one share
    assert P._torchThreadsNow(cpus=32, load=1.0) == 16
    assert P._torchThreadsNow(cpus=32, load=40.0) == 4    # never under 32 // 8
    assert P._torchThreadsNow(cpus=1, load=0.0) == 1


def test_the_setting_wins(monkeypatch):
    monkeypatch.setenv("XCP_TORCH_THREADS", "3")
    assert P._torchThreadsNow(cpus=8, load=0.0) == 3
