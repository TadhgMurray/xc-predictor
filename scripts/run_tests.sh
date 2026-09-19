#!/bin/bash
# Project: xc-predictor / scripts
# File:    run_tests.sh
# Purpose: run the WHOLE suite -- both kinds of test file -- and exit non-zero
#          if anything failed.
#
# ⚠⚠ WHY A RUNNER AND NOT JUST `pytest tests/`. There are two kinds of test
#    file in tests/, and pytest can only run one of them:
#
#      collected   ordinary unittest/pytest modules, ~200 files.
#      script      twenty files that run their checks at import and end with
#                  `sys.exit(1)`. Under pytest, import IS collection, so the
#                  first one kills the run with INTERNALERROR -- the whole
#                  suite, not just that file. tests/conftest.py keeps them out
#                  of collection (detected by AST, not listed), and this script
#                  runs them the way they were written to be run.
#
#    HANDOFF-2026-09-17 recorded the suite as unrunnable as a whole. What that
#    cost, on 2026-09-19: a new diagnostic shipped with `conn = getConn();
#    conn.cursor()` -- the exact bug tests/test_lint_getconn.py has caught
#    since 2026-09-10 -- and it was found by the owner, on the server, after a
#    git pull. An unrunnable suite is an absent suite.
#
# ! COLLECTION ERRORS FROM MISSING OPTIONAL DEPS ARE NOT FAILURES. torch and
#   flask are not installed everywhere; --continue-on-collection-errors keeps
#   the rest of the suite running, and the count is printed so a real import
#   break is still visible.
#
#   Usage:
#       bash scripts/run_tests.sh            # everything
#       bash scripts/run_tests.sh -k logo    # pass args through to pytest
set -u

cd "$(dirname "$0")/.." || exit 1
PY="${PY:-$( [ -x /srv/venv/bin/python ] && echo /srv/venv/bin/python || echo python3 )}"
export XCP_DB_PASSWORD="${XCP_DB_PASSWORD:-x}"

echo "== collected tests (pytest)"
"$PY" -m pytest -q -p no:cacheprovider --continue-on-collection-errors \
      tests/ "$@"
rc_pytest=$?

echo
echo "== script-style tests (run one at a time; see tests/conftest.py)"
rc_scripts=0
failed=""
for f in $("$PY" - <<'EOF'
import os, sys
sys.path.insert(0, "tests")
from conftest import scriptStyle
print("\n".join(scriptStyle()))
EOF
); do
    if out=$("$PY" "tests/$f" 2>&1); then
        printf '  ok    %s\n' "$f"
    else
        printf '  FAIL  %s\n' "$f"
        echo "$out" | sed 's/^/          /' | tail -12
        failed="$failed $f"
        rc_scripts=1
    fi
done

echo
if [ -n "$failed" ]; then
    echo "script-style failures:$failed"
fi
if [ "$rc_pytest" -ne 0 ] || [ "$rc_scripts" -ne 0 ]; then
    echo "SUITE FAILED (pytest rc=$rc_pytest, scripts rc=$rc_scripts)"
    exit 1
fi
echo "SUITE PASSED"
