"""The pipeline's first step reads every page (scripts/integrity_check.py)
and a damaged one stops the run before any other step reads it, on every
--from (2026-09-30: block 491889 of results_tf). Text checks: the script
needs a database to import."""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_integrity_runs_first_on_every_from_and_aborts():
    s = _src("deploy", "run_pipeline.sh")
    always = re.search(r'^_ALWAYS="([^"]*)"', s, re.M).group(1).split()
    assert "00_integrity" in always
    first_step = re.search(r"^(step|steps2|stepsN|bgstep) (\S+)", s, re.M).group(2)
    assert first_step == "00_integrity"
    after = s[s.index("step 00_integrity"):s.index("step 00_meet_dates")]
    assert "if failed 00_integrity" in after and "summarise 1" in after


def test_a_block_that_errors_is_bisected_not_fatal():
    s = _src("scripts", "integrity_check.py")
    body = s[s.index("def checkRange("):s.index("\ndef main(")]
    assert "verify_heapam(" in body and "startblock := %s, endblock := %s" in body
    # a lost connection is not damage; anything else in the data is split down
    assert body.index("OperationalError") < body.index("except psycopg2.DatabaseError")
    assert "checkRange(cur, name, start, mid) + checkRange(cur, name, mid + 1, end)" in body
