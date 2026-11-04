import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def test_drift_reads_the_since_normalised_step():
    import drift_audit as D
    assert D.driftOf({"steps": [{"key": "weather", "pct": 0.2}, {"key": "refit", "pct": 4.48}]}) == 4.48
    assert D.driftOf({"steps": [{"key": "weather", "pct": 0.2}]}) == 0.0
    assert D.driftOf(None) == 0.0
