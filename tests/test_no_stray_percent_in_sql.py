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

⚠⚠ AND THE SECOND FIX ONLY SAW QUERIES WRITTEN INLINE AT THE execute() CALL.
   2026-09-17, the fourth occurrence: five ordinary percentages in the XC
   corpus comments ("0.0% of tfrrs rows", "100% OF ROWS", ...) made
   model/feature_extraction._XC_SQL want nine format slots where its callers
   pass two or four. IndexError: tuple index out of range -- in
   build_recruit_projection, on the predictions page, and in the XC half of
   extraction. This test was green through all of THAT, because _XC_SQL is a
   module-level constant handed to `cursor.execute(sql, params)` through a
   local variable: args[0] is a Name, the AST could not see a literal, and
   the query was never checked.

★ SO A MODULE-LEVEL QUERY CONSTANT IS CHECKED ON ITS OWN EVIDENCE. A string
  carrying a real `%s` or `%(name)s` IS executed with parameters somewhere --
  that is what a placeholder is for -- so every other `%` in it has to be
  escaped, no matter how it reaches the driver. That needs no call graph.

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


# ! THE ESCAPE CAN ALSO BE APPLIED IN CODE. feature_extraction runs its two
#   corpus queries through escapeLiteralPercent() so the prose in them stays
#   ordinary English; a constant the module rebinds that way is already safe
#   and its literal form is not what the driver sees.
_ESCAPED_IN_CODE = re.compile(
    r"^(\w+)\s*=\s*escapeLiteralPercent\(\s*\1\s*\)", re.M)


def _moduleQueryConstants(path, tree, src):
    """(name, sql) for every module-level string constant that carries a real
    placeholder -- i.e. one that must be executed with parameters."""
    exempt = set(_ESCAPED_IN_CODE.findall(src))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id in exempt:
            continue
        sql = _literal(node.value)
        if not sql or "%" not in sql:
            continue
        probe = sql.replace("%%", "")
        if "%s" not in probe and "%(" not in probe:
            continue                             # no placeholder: not ours
        yield target.id, node.lineno, sql


def moduleQueryConstants():
    for d in DIRS:
        for path in sorted(glob.glob(os.path.join(ROOT, d, "*.py"))):
            src = io.open(path, encoding="utf-8").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            rel = os.path.relpath(path, ROOT)
            for name, lineno, sql in _moduleQueryConstants(path, tree, src):
                yield rel, lineno, name, sql


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


def test_no_module_query_constant_carries_a_stray_percent():
    """The gap the 2026-09-17 IndexError came through: a query constant is
    executed through a variable, so the inline scan above never sees it."""
    offenders = []
    for path, lineno, name, sql in moduleQueryConstants():
        probe = sql.replace("%%", "")
        stray = [m.start() for m in re.finditer(r"%(?![(s])", probe)]
        for i in stray[:2]:
            offenders.append(
                f"{path}:{lineno} {name} stray % in "
                f"{probe[max(0, i - 60):i + 20]!r}")
    assert not offenders, "\n" + "\n".join(offenders)


def test_the_corpus_queries_take_the_params_their_callers_pass():
    """feature_extraction's two corpus queries take exactly two params (the
    weather hour and the minimum normalized time); personResultsSql adds two
    more for the id array, bound twice. Counted on the text, so the check
    needs neither torch nor a database."""
    src = io.open(os.path.join(ROOT, "model", "feature_extraction.py"),
                  encoding="utf-8").read()
    tree = ast.parse(src)
    seen = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("_XC_SQL", "_TF_SQL")):
            lit = _literal(node.value)
            if lit:
                seen[node.targets[0].id] = lit
    assert set(seen) == {"_XC_SQL", "_TF_SQL"}, sorted(seen)
    for name, sql in seen.items():
        # escapeLiteralPercent doubles everything that is not a placeholder,
        # so the placeholder count is what survives stripping %% from it.
        escaped = re.sub(r"%(?![(s%])", "%%", sql)
        n = len(re.findall(r"%[s(]", escaped.replace("%%", "")))
        assert n == 2, f"{name} wants {n} params, not 2"


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
    test_no_module_query_constant_carries_a_stray_percent()
    test_the_corpus_queries_take_the_params_their_callers_pass()
    test_the_athlete_search_keeps_its_notes_in_python()
    print("  no query carries a % psycopg2 cannot read ......... OK")
