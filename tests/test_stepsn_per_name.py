# Project: xc-predictor / tests
# File:    test_stepsn_per_name.py
# Purpose: stepsN asks --from, --skip and _ALWAYS of EACH name it runs.
#
# ★ WHY (2026-10-25). stepsN tested the first name only, which was right
#   while every group was one step's streams. The people verdicts (04,
#   04b, 04d) and the board waves (10f with 11_teams) mix step numbers and
#   an _ALWAYS step: `--from 11` must still build the teams, `--from 7`
#   must still rebuild wheelchair_person, `--skip 04d_gender` skips gender
#   and nothing else.
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = open(os.path.join(ROOT, "deploy", "run_pipeline.sh"), encoding="utf-8").read()


def _fn(name):
    m = re.search(r"^%s\(\) \{[^\n]*\n.*?^\}\n" % re.escape(name), SH, re.S | re.M)
    assert m, name
    return m.group(0)


def _run(setup, call):
    script = ("DRY=1\n" + setup + "\n" + _fn("skipped") + _fn("_skipCost")
              + _fn("stepsN") + call + "\n")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


PEOPLE = ('stepsN 04_grade_sanity "gs" 04b_wheelchair "wc" 04d_gender "pg"')


def test_a_late_from_still_runs_the_always_step():
    out = _run('FROM=7; SKIP=""; _ALWAYS="04b_wheelchair"', PEOPLE)
    assert "04_grade_sanity skipped (--from 7)" in out
    assert "04d_gender skipped (--from 7)" in out
    assert "04b_wheelchair : wc" in out


def test_skip_takes_the_named_step_only():
    out = _run('FROM=0; SKIP=",04d_gender,"; _ALWAYS=""', PEOPLE)
    assert "04d_gender skipped (--skip)" in out
    assert "04_grade_sanity : gs" in out and "04b_wheelchair : wc" in out


def test_a_skipped_wheelchair_still_says_what_it_costs():
    out = _run('FROM=0; SKIP=",04b_wheelchair,"; _ALWAYS="04b_wheelchair"', PEOPLE)
    assert "IS ON THE --skip LIST" in out


def test_from_11_still_builds_the_teams():
    out = _run('FROM=11; SKIP=""; _ALWAYS=""', 'stepsN 10f_recruits "rc" 11_teams "tm"')
    assert "10f_recruits skipped (--from 11)" in out
    assert "11_teams : tm" in out


def test_nothing_left_runs_nothing():
    out = _run('FROM=12; SKIP=""; _ALWAYS=""', 'stepsN 10f_recruits "rc" 11_teams "tm"')
    assert " : " not in out
