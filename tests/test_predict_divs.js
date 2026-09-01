/*
 * Issue #85: several divisions at once, each scored as its own race.
 *
 * The selection logic and the per-division edit records are extracted from
 * predictions.js and run for real, so these cannot drift from the shipped
 * code:   node tests/test_predict_divs.js
 */
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

/* Brace-matched, not regex-matched: resetEdits is a one-liner, and a
   non-greedy /\n}/ ran straight past it into the next block. */
function grab(name) {
  const i = SRC.indexOf("function " + name + "(");
  if (i < 0) { console.error(`${name} not found in predictions.js`); process.exit(1); }
  const open = SRC.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(i, j + 1);
  }
  console.error(`${name} is unbalanced`); process.exit(1);
}

/* editsFor closes over a module-level Map, so rebuild that scope here. */
const sandbox = eval(`(() => {
  const _edits = new Map();
  ${grab("toggleDiv")}
  ${grab("divKey")}
  ${grab("editsFor")}
  ${grab("resetEdits")}
  return { toggleDiv, divKey, editsFor, resetEdits, _edits };
})()`);
const { toggleDiv, divKey, editsFor, resetEdits } = sandbox;

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } return c; };
const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b),
                           `${m}: got ${JSON.stringify(a)}`);

/* 1. picking and unpicking */
{
  eq(toggleDiv([], "10"), ["10"], "first pick");
  eq(toggleDiv(["10"], "12"), ["10", "12"], "second pick");
  eq(toggleDiv(["10", "12"], "10"), ["12"], "unpick keeps the other");
  eq(toggleDiv(["10"], "10"), [], "unpicking the last empties");
  console.log("  pick / unpick divisions ........................... OK");
}

/* 2. "All races" is exclusive both ways */
{
  eq(toggleDiv(["10", "12"], null), [], "All races clears the rest");
  // and picking a division while on All races leaves just that division
  eq(toggleDiv([], "12"), ["12"], "a division out of All races");
  console.log("  All races is exclusive with named divisions ....... OK");
}

/* 3. the whole meet is a real key, not a missing one */
{
  ok(divKey(null) === "", "null -> ''");
  ok(divKey(undefined) === "", "undefined -> ''");
  ok(divKey(12) === "12", "number -> string");
  ok(divKey("12") === "12", "string stays");
  console.log("  divKey: the whole meet keys as '' ................. OK");
}

/* 4. ★ THE POINT OF THE ISSUE: edits do not leak between divisions */
{
  resetEdits();
  const d1 = editsFor("10");
  const d2 = editsFor("12");
  ok(d1 !== d2, "two divisions shared one record");

  d1.removed.add("999");
  d1.added.push({ person_id: "555" });
  ok(d2.removed.size === 0, "a removal leaked into the other division");
  ok(d2.added.length === 0, "an addition leaked into the other division");

  /* and coming back to a division finds its edits still there */
  ok(editsFor("10").removed.has("999"), "edits lost on revisit");
  ok(editsFor("10") === d1, "revisiting made a new record");
  console.log("  D1 and D2 keep separate fields and edits .......... OK");
}

/* 5. a new meet forgets everything */
{
  resetEdits();
  editsFor("10").removed.add("1");
  resetEdits();
  ok(editsFor("10").removed.size === 0, "edits survived a meet change");
  console.log("  a new meet clears every division's edits .......... OK");
}

/* 6. each record starts empty and independent */
{
  resetEdits();
  const e = editsFor(null);
  eq([...e.removed], [], "removed starts empty");
  eq(e.added, [], "added starts empty");
  eq(e.droppedTeams, [], "droppedTeams starts empty");
  ok(e.field === null, "field starts null");
  console.log("  a fresh division starts empty ..................... OK");
}

if (failed) { console.error(`\n${failed} assertion(s) failed`); process.exit(1); }
console.log("\nall predict-divs tests passed");
