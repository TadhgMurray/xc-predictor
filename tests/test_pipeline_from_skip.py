"""--from and --skip for every kind of step (complexity review, 2026-10-24):
only step() read _ALWAYS, so `--from 7` skipped 05b_anchor_repair (run
through steps2), and steps2 / stepsN honoured --skip for the batch's first
name only. The runner functions are lifted out of deploy/run_pipeline.sh
and run dry in bash."""
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = os.path.join(ROOT, "deploy", "run_pipeline.sh")
FUNCS = ("skipped", "beforeFrom", "step", "steps2", "stepsN", "shards", "bgstep")


def _lib():
    text = open(SH, encoding="utf-8").read()
    always = re.search(r'^_ALWAYS="[^"]*"$', text, re.M).group(0)
    parts = [always]
    for f in FUNCS:
        m = re.search(r"^%s\(\) \{.*?^\}$" % f, text, re.M | re.S)
        assert m, f
        parts.append(m.group(0))
    return "\n".join(parts)


def _run(body, frm=7, skip=""):
    if not os.path.exists("/bin/bash"):
        pytest.skip("no bash")
    script = (f"DRY=1; FROM={frm}; SKIP=',{skip},'; LOGDIR=/tmp; SUMMARY=/dev/null; NICE=; "
              f"BG_PIDS=(); BG_NAMES=(); BG_T0=()\n{_lib()}\n{body}\n")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout


def test_from_seven_still_runs_the_anchor_repair():
    out = _run('steps2 05b_anchor_repair_xc "x" 05b_anchor_repair_tf "y"')
    assert "05b_anchor_repair_xc : x" in out and "skipped" not in out


def test_from_skips_a_pair_before_it_and_not_one_after():
    assert "skipped (--from 7)" in _run('steps2 05_backfill_xc "x" 05_backfill_tf "y"')
    assert "13_panels_xc : x" in _run('steps2 13_panels_xc "x" 13_panels_tf "y"')


def test_skip_is_per_name_in_pairs_and_batches():
    out = _run('steps2 13_panels_xc "x" 13_panels_tf "y"', skip="13_panels_tf")
    assert "13_panels_tf skipped (--skip)" in out and "13_panels_xc" in out and "13_panels_tf :" not in out
    out = _run('stepsN 10_a "a" 10_b "b" 10_c "c"', skip="10_b")
    assert "10_a : a" in out and "10_c : c" in out and "10_b skipped (--skip)" in out


def test_shards_and_background_steps_read_the_always_list_and_skip():
    assert "02_drop_old :" in _run('shards 02_drop_old 4 python x')
    assert "skipped (--skip)" in _run('shards 12_x 4 python x', frm=0, skip="12_x")
    assert "skipped (--from 7)" in _run('bgstep 04e_weather_grid python x')
    assert "in the background" in _run('bgstep 10f2_projection python x')
