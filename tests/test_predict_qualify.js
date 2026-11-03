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

ok(/id="qualify"[^>]*checked/.test(HTML), "the Qualifiers box is ticked by default");
ok(/qualify: true,/.test(SRC), "...and the page starts with it on");

const els = { "t-date": { value: "2026-11-21" }, "mc-basis": { hidden: true, textContent: "" } };
const $ = (id) => els[id];
const isTrack = () => false;
let state = { meet: { id: 1, sport: "XC" }, when: "thisyear", qualify: true, pick: "rating" };
const fieldQuery = eval(`(${grab("fieldQuery")})`);
ok(!fieldQuery(5).has("qualify"), "on: nothing extra is sent (the server's default)");
state.qualify = false;
ok(fieldQuery(5).get("qualify") === "0", "off: qualify=0");
state.when = "asran";
ok(!fieldQuery(5).has("qualify"), "as it ran: never sent");

state = { when: "thisyear", pick: "rating", qualify: true };
const fmtDate = (d) => d;
let blocks = [];
const editsFor = () => ({ field: { basis: { kind: "qualified", rounds: 9, predicted: 9 } } });
const activeBlocks = () => [1];
const showBasis = eval(`(${grab("showBasis")})`);
showBasis([1]);
ok(!els["mc-basis"].hidden && /qualifiers/.test(els["mc-basis"].textContent)
   && /9 of its 9 qualifying rounds have not run/.test(els["mc-basis"].textContent),
   "the line under the meet says the field is qualifiers, and how many rounds are predicted");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("all ok");
