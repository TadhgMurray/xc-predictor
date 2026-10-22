"""scripts/diag_tfrrs_course_cells: the bucket rule (2026-10-22)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import diag_tfrrs_course_cells as D                              # noqa: E402


def test_bucket_is_the_first_missing_link():
    assert D.bucket(None, None, None, None) == "no gps"
    assert D.bucket(0.0, -95.0, None, None) == "no gps"
    assert D.bucket(44.4, -93.1, None, None) == "no canonical"
    assert D.bucket(44.4, -93.1, 812, None) == "no cell"
    assert D.bucket(44.4, -93.1, 812, 0.051) == "cell"
