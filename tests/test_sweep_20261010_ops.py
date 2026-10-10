# Project: xc-predictor / tests
# File:    test_sweep_20261010_ops.py
# Purpose: the 2026-10-10 sweep's pipeline and site-write fixes:
#          (11) run_pipeline.sh stops before 05 when a people step failed,
#          (12) nightly_update.sh prices nothing over a failed people step
#               and builds the search index only over a swapped board,
#          (13) panels.py upserts homepage_meta (no unscoped DELETE),
#          (15) backfill_normalize: the person_gender verdict outranks the
#               row's own profile,
#          (16) TFRRS decorated times parse.
#
#     python -m pytest -q tests/test_sweep_20261010_ops.py
import os
import re
import subprocess
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _bash(script):
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30)


# ------------------------------------------------------------------ #
# 11. run_pipeline.sh: THE PEOPLE GATE                               #
# ------------------------------------------------------------------ #

def _pipelineGate():
    sh = _read("deploy/run_pipeline.sh")
    start = sh.index("_PEOPLE_FAILED=\"\"")
    end = sh.index("# ! THE BACKFILL RUNS AFTER grade_sanity")
    assert start < end < sh.index("05_backfill_xc \"$PY")
    return sh[start:end]


_STUBS = '''
SUMMARY=/dev/null; LOGDIR=/tmp; DRY=0
failed() { case " $FAILED " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
summarise() { echo "ABORTED"; exit 1; }
'''


@pytest.mark.parametrize("step", ["03_pro_flag", "03b_age_bands",
                                  "04_grade_sanity", "04b_wheelchair",
                                  "04c_twins", "04d_gender"])
def test_a_failed_people_step_aborts_before_the_backfill(step):
    got = _bash(_STUBS + f'FAILED=" {step}"\n' + _pipelineGate()
                + 'echo "REACHED 05"')
    assert "ABORTED" in got.stdout and "REACHED 05" not in got.stdout, got
    assert got.returncode == 1


def test_the_people_gate_passes_a_clean_run_and_honours_the_override():
    clean = _bash(_STUBS + 'FAILED=" 04e_weather_grid"\n' + _pipelineGate()
                  + 'echo "REACHED 05"')
    assert "REACHED 05" in clean.stdout, clean
    forced = _bash(_STUBS + 'FAILED=" 04c_twins"\nXCP_IGNORE_PEOPLE_FAIL=1\n'
                   + _pipelineGate() + 'echo "REACHED 05"')
    assert "REACHED 05" in forced.stdout, forced


# ------------------------------------------------------------------ #
# 12. nightly_update.sh                                              #
# ------------------------------------------------------------------ #

def _nightlyGate():
    sh = _read("deploy/nightly_update.sh")
    start = sh.index("PEOPLE_OK=1")
    end = sh.index("if [ \"$PEOPLE_OK\" -eq 1 ] && step 05_normalize_new")
    return sh[start:end]


@pytest.mark.parametrize("step", ["04a_link_tfrrs", "04a2_link_teamless",
                                  "04b_wheelchair", "04c_twins"])
def test_nightly_prices_nothing_over_a_failed_people_step(step):
    got = _bash(f'SUMMARY=/dev/null; FAILED=" {step}"\n' + _nightlyGate()
                + 'echo "OK=$PEOPLE_OK"')
    assert "OK=0" in got.stdout, got
    ok = _bash('SUMMARY=/dev/null; FAILED=" scrape_anet"\n' + _nightlyGate()
               + 'echo "OK=$PEOPLE_OK"')
    assert "OK=1" in ok.stdout, ok


def test_nightly_search_index_only_after_a_swapped_board():
    sh = _read("deploy/nightly_update.sh")
    block = sh[sh.index("if step 10_rankings_finish"):]
    block = block[:block.index("\n      fi\n")]
    assert "step 13c_search_index" in block
    # the redirect resolve runs whatever publishing did: tomorrow's 01a
    # overwrites the probe it needs
    assert "step 13c0_person_redirects" not in block
    assert sh.count("step 13c0_person_redirects") == 1
    tail = sh[sh.index("step 13c0_person_redirects"):]
    assert "# ---- summary" in tail
    assert _bash(f"bash -n {os.path.join(_ROOT, 'deploy', 'nightly_update.sh')}").returncode == 0
    assert _bash(f"bash -n {os.path.join(_ROOT, 'deploy', 'run_pipeline.sh')}").returncode == 0


# ------------------------------------------------------------------ #
# 13. homepage_meta IS UPSERTED                                      #
# ------------------------------------------------------------------ #

def test_panels_never_deletes_the_other_sports_meta():
    src = _read("racecast/panels.py")
    assert not re.search(r"DELETE FROM homepage_meta\"", src)
    assert "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value" in src


# ------------------------------------------------------------------ #
# 15. person_gender OUTRANKS THE PROFILE                             #
# ------------------------------------------------------------------ #

class _Cur:
    """The three statements _loadGenders runs, answered from lists."""

    def __init__(self, profiles, verdicts):
        self.profiles, self.verdicts, self.rows = profiles, verdicts, []

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self.rows = [("person_gender",)]
        elif "FROM person_gender" in sql:
            self.rows = list(self.verdicts)
        else:
            self.rows = list(self.profiles)

    def fetchone(self):
        return self.rows[0]

    def __iter__(self):
        return iter(self.rows)


@pytest.fixture(scope="module")
def BF():
    for sub in ("backfill", "engine", "scripts"):
        p = os.path.join(_ROOT, sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    if "corrections" not in sys.modules:        # 165 MB, not in the repo
        import types
        _c = types.ModuleType("corrections")
        _c.__getattr__ = lambda name: (  # not dunders: see test_model_one_scale
            {} if not name.startswith("__") else getattr(object(), name))
        sys.modules["corrections"] = _c
    import backfill_normalize
    return backfill_normalize


def test_the_person_verdict_beats_the_rows_own_profile(BF):
    # athlete 11 is a merged profile (profile says F) of person 22, whose
    # rows say M; athlete 33's profile collides with nothing
    g = BF._loadGenders(_Cur([(11, "F"), (22, "F"), (33, "M")], [(22, "M")]))
    assert BF._rowGender(g, 11, 22) == "M"      # was the profile's F
    assert BF._rowGender(g, 22, 22) == "M"
    # no verdict: the row's own profile, then the canonical one
    assert BF._rowGender(g, 33, 44) == "M"
    assert BF._rowGender(g, None, 11) == "F"
    assert BF._rowGender(g, None, None) is None
    # a person id that equals an unrelated athlete id no longer collides:
    # athlete 22's profile is still readable under its own key
    assert g[22] == "F" and g[("pg", 22)] == "M"


# ------------------------------------------------------------------ #
# 16. TFRRS DECORATED TIMES                                          #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def PT():
    p = os.path.join(_ROOT, "tfrrs", "parser")
    if p not in sys.path:
        sys.path.insert(0, p)
    import parse_time
    return parse_time


@pytest.mark.parametrize("cell,want", [
    # valid times: unchanged
    ("9.88", 9.88), ("4:48.76", 288.76), ("16:12.4", 972.4),
    ("23:35.0", 1415.0), ("2:01:09", 7269.0), ("\n   16:12.4  \n", 972.4),
    # decorated: kept
    ("10.8h", 10.8), ("10.84H", 10.84), ("51.2 h", 51.2),
    ("1:52.34@", 112.34), ("8:45.12#", 525.12), ("4:12.88@#", 252.88),
    ("4:05.21 (4:05.203)", 245.21), ("4:05.21\n(4:05.203)", 245.21),
    ("14:02.11@ (14:02.103)", 842.11),
    # not times: None
    ("DNF", None), ("DNS", None), ("DQ", None), ("NT", None), ("FS", None),
    ("-", None), ("", None), (None, None), ("4:05.21 PR", None),
    ("(4:05.203)", None), ("1:2:3:4", None),
])
def test_tfrrs_time_cells(PT, cell, want):
    got = PT.parseTimeToSeconds(cell)
    if want is None:
        assert got is None, (cell, got)
    else:
        assert got == pytest.approx(want), (cell, got)


def test_rejects_are_counted(PT):
    PT.REJECTS.clear()
    PT.parseTimeToSeconds("DNF")
    PT.parseTimeToSeconds("4:05.21 PR")
    PT.parseTimeToSeconds("1:52.34@")
    assert PT.REJECTS == {"placeholder": 1, "malformed": 1, "decorated_kept": 1}
