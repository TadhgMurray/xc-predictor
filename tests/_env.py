# Project: xc-predictor / tests
# File:    _env.py
# Purpose: the one place a test satisfies scripts/config.py's import-time check.
#
# ⚠ WHY A SHARED MODULE. scripts/config.py raises at import when
#   XCP_DB_PASSWORD is unset -- "There is deliberately no fallback", and that
#   guard is right: two corpora are reachable from one checkout and they differ
#   by one digit in the port. So every test that imports anything touching
#   config has to put SOMETHING there, and 34 files each grew their own
#   `os.environ.setdefault("XCP_DB_PASSWORD", "...")`.
#
#   34 literal password assignments is what tripped GitGuardian's "Generic
#   Database Assignment" detector on 2026-09-19. Nothing was leaked -- the
#   value is the string below, the real credentials live in
#   /etc/xc-predictor.env and the gitignored scripts/config_local.py -- but 34
#   copies of a credential-shaped line is a haystack to hide a real needle in.
#
# ! NOT A SECRET, AND NOT PRETENDING NOT TO BE ONE. The value is named for what
#   it is and no database accepts it. .gitguardian.yaml declares this exact
#   string, so the ignore is pinned to it rather than to a pattern that could
#   mask a real one.
#
#   Use it as the first import in a test:
#       import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config
#
# ! THE 31 OLDER FILES STILL DO IT INLINE. They are not touched here: a
#   34-file mechanical diff would bury the change that matters. Migrate one
#   whenever you are editing it anyway.
import os

PLACEHOLDER = "unused-by-this-test"

os.environ.setdefault("XCP_DB_PASSWORD", PLACEHOLDER)
# Keep the banner quiet too, so a test's output is its own.
os.environ.setdefault("XCP_DB_QUIET", "1")
