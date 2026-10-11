"""Polite mode (owner, 2026-10-11: athletic.net rate-limited the server).
With NO_VPN=1 the first 429 or block stops the whole run instead of
moving on to the next meet at the same pace.

    python -m pytest -q tests/test_polite_stop.py
"""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def fresh(monkeypatch, **env):
    for k in ("NO_VPN", "ANET_POLITE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import polite_stop
    return importlib.reload(polite_stop)


def test_on_with_no_vpn_and_stops_everything(monkeypatch):
    P = fresh(monkeypatch, NO_VPN="1")
    assert P.active() and not P.stopped()
    assert P.trip("[S1]", "429 on meet 1")
    assert P.stopped()
    try:
        P.check()
        raise AssertionError("check() should raise once stopped")
    except P.PoliteStop as e:
        assert "429" in str(e)


def test_off_without_no_vpn_or_when_turned_off(monkeypatch):
    P = fresh(monkeypatch)
    assert not P.active() and not P.trip("[S1]", "429") and not P.stopped()
    P = fresh(monkeypatch, NO_VPN="1", ANET_POLITE="0")
    assert not P.active()


def test_the_scraper_trips_it_on_a_429():
    src = open(os.path.join(ROOT, "scripts", "scrape_results.py")).read()
    assert src.count("polite_stop.trip(") == 2
    launcher = open(os.path.join(ROOT, "scripts", "launcher.py")).read()
    assert "polite_stop.PoliteStop" in launcher and "polite_stop.check()" in launcher
