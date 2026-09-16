"""psycopg2 reads every % in a query as a placeholder, SQL comments included.

    python -m pytest -q tests/test_no_stray_percent_in_sql.py

This has now broken the site THREE times:

  * 2026-09-03 -- a comment saying "+18%" took every athlete page down.
  * 2026-09-16 -- a comment in /api/predict/athletes saying `ILIKE '%tok%'`
    made "add anyone" fail on every keystroke. psycopg2 read %t as a
    POSITIONAL placeholder, saw %(prefix)s beside it, and refused the
    statement: "argument formats can't be mixed". Owner: "the add anyone box
    says it could not search for anything I write into it."

⚠ THE FIRST FIX ONLY GUARDED get_races, which is the function that broke the
  first time. That is a guard against the incident, not against the bug --
  the second one happened in a different function, six hundred lines away,
  and the test was green through all of it.

★ SO THIS WALKS THE AST AND FINDS EVERY QUERY THAT IS EXECUTED WITH
  PARAMETERS, anywhere in the repo. That is the exact set psycopg2 parses %
  in: a query passed with no parameters is sent verbatim and its % are
  literal, which is why `LIKE 'XC:%'` is fine in one place and fatal in
  another.

! %% IS THE ESCAPE and is stripped before checking, so a query that really
  does need a literal percent still passes.
"""
import ast
import glob
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ("racecast", "scripts", "engine", "model", "backfill")
EXECUTORS = ("execute", "executemany", "execute_values", "mogrify")


def _literal(node):
    """The literal text of a str / f-string / implicit or + concatenation.

    ! ONLY THE LITERAL PARTS OF AN f-STRING. What an interpolation evaluates
      to is not knowable here -- but a stray % almost always sits in prose
      the author typed, and that prose IS a literal part.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant)
                       and isinstance(v.value, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _literal(node.left), _literal(node.right)
        return None if a is None or b is None else a + b
    return None


def parameterisedQueries():
    """(path, lineno, sql) for every query executed WITH parameters."""
    for d in DIRS:
        for path in sorted(glob.glob(os.path.join(ROOT, d, "*.py"))):
            src = io.open(path, encoding="utf-8").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:                  # not ours to police
                continue
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                name = (n.func.attr if isinstance(n.func, ast.Attribute)
                        else getattr(n.func, "id", ""))
                if name not in EXECUTORS:
                    continue
                args = n.args
                if name == "execute_values":
                    args = args[1:]              # (cur, sql, rows)
                if len(args) < 2:
                    # no parameters: psycopg2 sends it verbatim, % is literal
                    continue
                sql = _literal(args[0])
                if sql:
                    yield os.path.relpath(path, ROOT), n.lineno, sql


def test_there_are_queries_to_check():
    """A scanner that silently matches nothing passes forever."""
    found = list(parameterisedQueries())
    assert len(found) > 100, len(found)


def test_no_query_carries_a_percent_psycopg2_cannot_read():
    offenders = []
    for path, lineno, sql in parameterisedQueries():
        if "%" not in sql:
            continue
        probe = sql.replace("%%", "")            # %% is the escape
        named = re.findall(r"%\(", probe)
        positional = re.findall(r"%s", probe)
        stray = [m.start() for m in re.finditer(r"%(?![(s])", probe)]
        if stray:
            for i in stray[:2]:
                offenders.append(
                    f"{path}:{lineno} stray % in "
                    f"{probe[max(0, i - 50):i + 20]!r}")
        elif named and positional:
            offenders.append(f"{path}:{lineno} mixes %(name)s and %s")
    assert not offenders, "\n" + "\n".join(offenders)


def test_the_athlete_search_keeps_its_notes_in_python():
    """The one that broke: its explanation is a Python comment now, above the
    function, where the driver can never see it."""
    app = io.open(os.path.join(ROOT, "racecast", "app.py"),
                  encoding="utf-8").read()
    i = app.index("def _athleteSearchRows(")
    body = app[i:app.index("\ndef ", i + 10)]
    sql = body[body.index('f"""'):body.index('""", params)')]
    assert "--" not in sql, sql
    assert "%" not in sql.replace("%(prefix)s", ""), sql


if __name__ == "__main__":
    test_there_are_queries_to_check()
    test_no_query_carries_a_percent_psycopg2_cannot_read()
    test_the_athlete_search_keeps_its_notes_in_python()
    print("  no query carries a % psycopg2 cannot read ......... OK")
