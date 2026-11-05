/*
 * ★ QUALIFIERS AS A PREDICTIONS SETTING (owner, 2026-11-03: "a setting for
 * predictions that is on by default"). fieldQuery and showBasis run for
 * real:
 *     node tests/test_predict_qualify.js
 */
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");
const HTML = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "templates", "predictions.html"), "utf8");
function grab(name) {
  const i = SRC.indexOf("function " + name + "(");
  const open = SRC.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(i, j + 1);
  }
}
let fails = 0;
const ok = (c, m) => { console.log((c ? "  ok  " : "  FAIL") + " " + m); if (!c) fails++; };

ok(/<select id="qualify"/.test(HTML) && /<option value="1" selected>This season's qualifiers/.test(HTML),
   "a select, on This season's qualifiers by default");
ok(/qualify: true,/.test(SRC), "...and the page starts with it on");

const els = { "t-date": { value: "2026-11-21" }, "mc-basis": { hidden: true, textContent: "" } };
const $ = (id) => els[id];
const isTrack = () => false;
let state = { meet: { id: 1, sport: "XC" }, when: "thisyear", qualify: true, pick: "rating" };
const seasonOf = eval(`(${grab("seasonOf")})`);
const fieldQuery = eval(`(${grab("fieldQuery")})`);
// the season a date is in (Aug 15, as engine/season_year.py): a later one
// is a new field, this year's seniors gone (owner, 2026-11-04)
ok(seasonOf("2026-11-21") === 2026 && seasonOf("2027-08-14") === 2026 &&
   seasonOf("2027-08-15") === 2027 && seasonOf("") === null, "seasonOf: the Aug 15 seam");
ok(!fieldQuery(5).has("qualify"), "on: nothing extra is sent (the server's default)");
state.qualify = false;
ok(fieldQuery(5).get("qualify") === "0", "off: qualify=0");
ok(fieldQuery(5).get("date") === "2026-11-21",
   "the date goes with every 'this year' field ask (last year's meet on this year's date)");
state.when = "asran";
ok(!fieldQuery(5).has("qualify"), "as it ran: never sent");
ok(!fieldQuery(5).has("date"), "...nor the date");

state = { when: "thisyear", pick: "rating", qualify: true };
const fmtDate = (d) => d;
let blocks = [];
const editsFor = () => ({ field: { basis: { kind: "qualified", rounds: 9, predicted: 9 } } });
const activeBlocks = () => [1];
const showBasis = eval(`(${grab("showBasis")})`);
showBasis([1]);
ok(!els["mc-basis"].hidden && /this season's qualifiers/.test(els["mc-basis"].textContent)
   && /None of the 9 qualifying meets have been run yet/.test(els["mc-basis"].textContent),
   "the line under the meet says the field is qualifiers, and that none of the rounds have run");
// said like a person would (owner, 2026-11-05): some run, some predicted
editsFor.basis = null;
const showSome = eval(`((editsFor) => (${grab("showBasis")}))`)(
  () => ({ field: { basis: { kind: "qualified", rounds: 9, predicted: 4 } } }));
showSome([1]);
ok(/5 of the 9 qualifying meets have been run; for the other 4, we've predicted who gets through/
     .test(els["mc-basis"].textContent), "...or how many have run and how many are our guess");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("all ok");
