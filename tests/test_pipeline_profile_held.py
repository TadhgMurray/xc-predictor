"""scripts/pipeline_profile.py (2026-11-12): a background step's "held the
run Ns" and a chain's wait line are read; the evidence chain's steps count
as background, not in line."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_held_and_chain_lines(tmp_path):
    run = tmp_path / "20261112_015027"
    run.mkdir()
    (run / "summary.log").write_text(
        "  07_pack ok (2690s, peak 68.6 GB, box down to 55 GB free)\n"
        "  08a_holdout ok (1022s, background, evidence chain, peak 33.6 GB, box down to 55 GB free)\n"
        "  04e_weather_grid ok (5991s, background, held the run 1200s)\n"
        "  evidence_chain held the run 300s\n")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "pipeline_profile.py"), str(run)],
                         capture_output=True, text=True, check=True).stdout
    assert "2,690 s in line" in out and "1,500 s held for background steps" in out
    assert any(l.startswith("08a_holdout") and " bg " in l for l in out.splitlines())
