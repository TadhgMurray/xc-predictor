"""A failed crest load is retried, never cached empty; unit_logos never holds
the table locked for a whole run (owner, 2026-11-02: "All logos on the site
are gone")."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

import school_logo  # noqa: E402


def test_a_failed_load_keeps_the_old_map_and_retries(monkeypatch):
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(school_logo, "_CRESTS",
                        {"loaded": False, "map": {"Old": []}, "factory": None, "retry_at": 0.0})
    school_logo.loadCrests(broken)
    assert not school_logo.crestLoaded()
    assert school_logo._CRESTS["map"] == {"Old": []}
    school_logo._retryCrests()                 # too soon: no second try
    assert len(calls) == 1
    school_logo._CRESTS["retry_at"] = 0.0
    school_logo._retryCrests()
    assert len(calls) == 2


def test_unit_logos_commits_the_set_up_and_each_logo():
    src = open(os.path.join(_ROOT, "scripts", "unit_logos.py")).read()
    i = src.index("S.ensureLevelKey(cur)")
    assert "conn.commit()" in src[i:i + 60]
    pin = src[src.index("def pin("):]
    assert "conn.commit()" in pin[:400] and "conn.rollback()" in pin[:400]
    assert src.count("S.setLogo(cur, manners, key") == 1
