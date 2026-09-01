/*
 * A school's SEARCH LABEL is not its NAME: the index stores "DeWitt (MI)",
 * the database stores "DeWitt". Every place the two are confused has been a
 * live bug, so the rule is pinned on both sides of the wire.
 *   node tests/test_school_label.js
 */
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } return c; };

/* 1. The client keys identity on .value, never .label. */
{
  const js = fs.readFileSync(
    path.join(root, "racecast", "static", "predictions.js"), "utf8");
  const i = js.indexOf("function renderSchoolsFor(");
  ok(i > 0, "renderSchoolsFor exists");
  const body = js.slice(i, js.indexOf("\n}", i));
  ok(/const school = schoolValue\(r\)/.test(body),
     "the row's school comes from schoolValue");
  ok(/data-school="\$\{esc\(school\)\}"/.test(body),
     "and data-school carries it");
  ok(!/teamInDiv\(div, r\.label\)/.test(body),
     "nothing matches on the label");

  const pick = js.slice(js.indexOf("function bindSchoolPicker("));
  ok(/d\.school \|\| d\.label/.test(pick.slice(0, 600)),
     "the picker reads data-school first");
  console.log("  the client sends the name, not the label ........... OK");
}

/* 2. The server does not trust it to. An unresolvable label falls back to
      the bare name rather than reporting an empty squad. */
{
  const py = fs.readFileSync(path.join(root, "racecast", "app.py"), "utf8");
  const i = py.indexOf("def api_predict_squad(");
  ok(i > 0, "api_predict_squad exists");
  const body = py.slice(i, py.indexOf("\n@app.route", i));
  ok(/if not out\.get\("runners"\)/.test(body),
     "an empty squad is retried");
  ok(/search_index\.bareSchool\(school\)/.test(body),
     "with the bare name");
  ok(body.indexOf("schoolSquad(cur, school") < body.indexOf("bareSchool"),
     "exact is tried FIRST -- a real parenthesised name is not renamed");
  console.log("  the server falls back to the bare name ............ OK");
}

/* 3. bareSchool's rule is still two capitals in trailing parens, anchored.
      Loosen it and "Academy (Old)" loses its suffix. */
{
  const si = fs.readFileSync(
    path.join(root, "racecast", "search_index.py"), "utf8");
  ok(/_STATE_SUFFIX = re\.compile\(r"\\s\+\\\(\(\[A-Z\]\{2\}\)\\\)\$"\)/.test(si),
     "the state-suffix pattern is unchanged");
  console.log("  the label rule itself has not drifted ............. OK");
}

if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
console.log("\nall school-label checks passed");
