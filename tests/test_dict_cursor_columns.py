"""Two columns with one name, under the cursor the website uses.

    python -m pytest -q tests/test_dict_cursor_columns.py

★ EVERY PREDICTION DIED ON "IndexError: list index out of range" (owner,
  2026-09-16). forecast.normalAt selected nine aggregates and read the row
  by position -- and Postgres labels an unaliased avg() as just "avg", so
  EIGHT of the nine came back with the same name. Under RealDictCursor the
  row is a dict, the eight collapse into one key, and list(r.values()) has
  two entries where the code wanted nine.

  Proved on a real Postgres: nine columns selected, dict keys ['avg',
  'count'].

⚠ IT PASSES UNDER A TUPLE CURSOR, which is how a script or a pipeline step
  reaches the same function -- so it could only ever fail through the
  website, and only where nothing caught it. /api/predict/weather catches
  everything and reports "no weather", so this read as a missing forecast
  for weeks rather than as a bug.

! SCOPED TO THE REQUEST PATH. racecast/build_*.py has the same shape in four
  places and is fine: those are pipeline steps reading tuple cursors, by
  `n, buckets = cur.fetchone()`. They are a hazard only if someone changes
  the cursor, and failing a test on them today would be noise.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGG = re.compile(r"\b(avg|sum|count|min|max|stddev|var_samp|percentile_cont)\s*\(",
                 re.I)


def topLevelParts(select_list):
    """Split a SELECT list on its top-level commas."""
    depth, parts, cur = 0, [], ""
    for ch in select_list:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def unaliasedAggregates(path):
    src = io.open(path, encoding="utf-8").read()
    out = []
    for m in re.finditer(r"SELECT\b(.*?)\bFROM\b", src, re.S | re.I):
        sel = m.group(1)
        if len(sel) > 1500:                 # not a literal column list
            continue
        bare = [p.strip() for p in topLevelParts(sel)
                if AGG.search(p)
                and not re.search(r"\bAS\s+\w+\s*$", p.strip(), re.I)]
        if len(bare) >= 2:
            out.append((src[:m.start()].count("\n") + 1, len(bare), bare[0][:60]))
    return out


def test_no_request_path_query_selects_two_bare_aggregates():
    bad = []
    d = os.path.join(ROOT, "racecast")
    for name in sorted(os.listdir(d)):
        if not name.endswith(".py") or name.startswith("build_"):
            continue
        for line, n, first in unaliasedAggregates(os.path.join(d, name)):
            bad.append(f"{name}:{line} has {n} unaliased aggregates ({first!r})")
    assert not bad, (
        "under RealDictCursor these columns share a name and collapse:\n  "
        + "\n  ".join(bad))


def test_normalAt_reads_its_row_by_name():
    """! ALIASING ALONE WOULD FIX TODAY'S BUG and leave the next column added
    in the middle to silently shift every field after it. The row is read by
    key, and the tuple-cursor path is rebuilt into the same shape."""
    src = io.open(os.path.join(ROOT, "racecast", "forecast.py"),
                  encoding="utf-8").read()
    i = src.index("def normalAt(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert "AS temp_c" in body and "AS n_hours" in body, body[:400]
    # ! THE COMMENT QUOTES THE OLD CODE TO EXPLAIN IT, so the check is on
    #   the code. (Third time this has caught me in one session.)
    code = re.sub(r"#.*", "", body)
    assert 'r.get("n_hours")' in code
    # ⚠ the positional read is what broke; it must not come back
    assert "r[8]" not in code and "list(r.values())" not in code
    # a plain cursor still works, by being turned into the same mapping
    assert "dict(zip(cols, r))" in code
