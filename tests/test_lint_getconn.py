# Project: xc-predictor / tests
# File:    test_lint_getconn.py
# Purpose: getConn() is a CONTEXT MANAGER. Catch the scripts that forget.
#
# ★ WHY (2026-09-10). Two new diagnostics shipped with
#
#       conn = getConn()
#       cur = conn.cursor()
#
#   which dies at the first cursor with
#
#       AttributeError: '_GeneratorContextManager' object has no
#       attribute 'cursor'
#
#   -- not at import, not in any unit test, but on the server, in front of
#   the owner, after a git pull. It is invisible to every check that does
#   not have a database, which is every check this repo can run offline.
#   A grep-level lint catches it in a second.
#
# ! TWO SPELLINGS ARE FINE. `with getConn() as conn` is the normal one.
#   Several older scripts keep the manager and enter it by hand
#   (`cm = getConn(); conn = cm.__enter__()`), or normalise defensively
#   with hasattr(conn, "cursor") -- both work and both are left alone.
#
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# backfill_normalize_tf.py is SQLite-era dead code -- the line after its
# getConn is conn.execute("PRAGMA journal_mode=WAL"), which no Postgres
# connection has ever answered. It is superseded by backfill_normalize.py.
# Listed rather than fixed: touching it would imply it runs.
_DEAD = {"backfill/backfill_normalize_tf.py"}

_SKIP_DIRS = {".git", "__pycache__", "node_modules", "venv", ".venv"}

_ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*getConn\(\)")
_CURSOR = re.compile(r"\.cursor\(\)")
_ENTER = re.compile(r"__enter__|hasattr\(")
def _pyFiles():
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(root, f)
                yield os.path.relpath(full, _ROOT), full


class GetConnLint(unittest.TestCase):
    def test_getconn_is_entered(self):
        """`conn = getConn()` must be followed by an __enter__ or a hasattr
        normalisation before anything calls .cursor() on it."""
        bad = []
        for rel, full in _pyFiles():
            if rel.replace(os.sep, "/") in _DEAD:
                continue
            try:
                lines = open(full, encoding="utf-8").read().split("\n")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(lines):
                m = _ASSIGN.match(line)
                if not m:
                    continue
                name = m.group(1)
                # look ahead a few lines: is it entered, or used raw?
                window = "\n".join(lines[i + 1:i + 8])
                if _ENTER.search(window):
                    continue
                if _CURSOR.search(window) or f"{name}.execute" in window:
                    bad.append(f"{rel}:{i + 1}: {line.strip()}")
        self.assertEqual(
            bad, [],
            "getConn() returns a context manager, not a connection. Use\n"
            "    with getConn() as conn:\n"
            "Offenders:\n  " + "\n  ".join(bad))

if __name__ == "__main__":
    unittest.main()
