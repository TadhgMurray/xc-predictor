#!/bin/bash
# Project: xc-predictor / scripts
# File:    run_tests.sh
# Purpose: run the WHOLE suite -- the pytest files and the node tests -- and
#          exit non-zero if anything failed.
#
# ⚠⚠ WHY ONE COMMAND. HANDOFF-2026-09-17 recorded the suite as unrunnable as
#    a whole, and what that cost, on 2026-09-19: a new diagnostic shipped
#    with `conn = getConn(); conn.cursor()` -- the exact bug
#    tests/test_lint_getconn.py has caught since 2026-09-10 -- found by the
#    owner, on the server, after a git pull. An unrunnable suite is an absent
#    suite. tests/conftest.py is what makes `pytest tests/` collect in one
#    process (password placeholder, sys.path, one sys.modules view per file);
#    this adds the tests/*.js files, which pytest cannot see.
#
#   Usage:
#       bash scripts/run_tests.sh            # everything
#       bash scripts/run_tests.sh -k logo    # extra args go to pytest
#                                            # (the node tests always run)
set -u

cd "$(dirname "$0")/.." || exit 1
PY="${PY:-$( [ -x /srv/venv/bin/python ] && echo /srv/venv/bin/python || echo python3 )}"

echo "== python (pytest)"
"$PY" -m pytest -q -p no:cacheprovider tests/ "$@"
rc_pytest=$?

echo
echo "== node (tests/*.js)"
rc_node=0
failed=""
if ! command -v node >/dev/null 2>&1; then
    echo "  node is not installed: the JS tests did NOT run"
    rc_node=1
else
    for f in tests/*.js; do
        if out=$(node "$f" 2>&1); then
            printf '  ok    %s\n' "$f"
        else
            printf '  FAIL  %s\n' "$f"
            echo "$out" | tail -15 | sed 's/^/          /'
            failed="$failed $f"
            rc_node=1
        fi
    done
fi

echo
if [ -n "$failed" ]; then
    echo "node failures:$failed"
fi
if [ "$rc_pytest" -ne 0 ] || [ "$rc_node" -ne 0 ]; then
    echo "SUITE FAILED (pytest rc=$rc_pytest, node rc=$rc_node)"
    exit 1
fi
echo "SUITE PASSED"
