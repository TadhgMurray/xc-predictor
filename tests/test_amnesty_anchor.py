"""The result-drop retrial compares careers on ONE anchor (owner,
2026-10-08, Soheib Dissa: his first college 8k, nt 1480.5 on the college
men's 8000 m anchor, was convicted as 60% slower than his high-school
5K-equivalents and could never be echoed by them)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "backfill"):
    sys.path.insert(0, os.path.join(ROOT, d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")


def test_the_stream_moves_every_value_to_the_common_anchor():
    src = open(os.path.join(ROOT, "scripts", "amnesty_result_drops.py"), encoding="utf-8").read()
    assert "value = value * _anchorShift(trace[5], args.sport)" in src


def test_soheib_college_8k_is_echoed_once_on_one_anchor():
    import amnesty_result_drops as A
    from normalize_distance import anchorShift
    hs = [(i, 100 + i, f"2024-10-{10 + i:02d}", nt)
          for i, nt in enumerate((922.1, 886.5, 955.7, 883.6, 938.7, 908.9))]
    raw = (66538053, 254166, "2025-10-18", 1480.5)
    _, islands, _ = A._judge(hs + [raw], {66538053}, A.ECHO_WINDOW, A.ECHOES_REQ)
    assert islands == [66538053]                       # the July verdict
    moved = raw[:3] + (raw[3] * anchorShift("college_m", "XC"),)
    pardons, _, _ = A._judge(hs + [moved], {66538053}, A.ECHO_WINDOW, A.ECHOES_REQ)
    assert pardons == [66538053]
