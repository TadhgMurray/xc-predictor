/*
 * ★ A SLOW FIELD ANSWER NEVER OVERWRITES A NEWER ASK (owner, 2026-10-18:
 * "set 10 runners per school, it goes for a second and then unapplies and
 * goes back to 7. But the text box still says 10"). The field takes
 * seconds; the box changed while the first (seven-runner) ask was out, and
 * whichever answer came back last was kept. fieldQuery, fetchField and
 * loadField run for real, the page around them stubbed:
 *     node tests/test_predict_field_race.js
 */
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

function grab(name, kw) {
  const i = SRC.indexOf((kw || "function ") + name + "(");
  if (i < 0) { console.error(`${name} not found`); process.exit(1); }
  const open = SRC.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(i, j + 1);
  }
  process.exit(1);
}

let fails = 0;
const ok = (c, m) => { console.log((c ? "  ok  " : "  FAIL") + " " + m); if (!c) fails++; };

const pending = [];
const sb = eval(`(() => {
  const $ = () => null;
  const state = { meet: { id: "268246", sport: "XC", div: "7" }, when: "thisyear",
                  perTeam: null, pick: "rating", course: null, divs: [] };
  const _edits = new Map();
  const editsFor = (d) => { if (!_edits.has(d)) _edits.set(d, {}); return _edits.get(d); };
  const activeBlocks = () => ["7"];
  const renderField = () => {};
  const showBasis = () => {};
  const setStatus = () => {};
  const isTrack = () => false;
  const readJson = async (r) => r.body;
  const fetch = (url) => new Promise((res) => pending.push({ url, res }));
  ${grab("fieldQuery")}
  ${grab("fetchField", "async function ")}
  ${grab("loadField", "async function ")}
  return { state, editsFor, loadField };
})()`);

const answer = (p, n) => p.res({ ok: true, body: { teams: [], per_team: n } });
const tick = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  const first = sb.loadField();                     // the page's own load: seven
  await tick();
  sb.state.perTeam = 10;                            // the box, while it is out
  sb.editsFor("7").field = null;
  const second = sb.loadField();
  await tick();
  ok(pending.length === 2 && /per_team=10/.test(pending[1].url) && !/per_team/.test(pending[0].url),
     "two asks out: seven, then ten");
  answer(pending[1], 10);                           // ten comes back first
  await second;
  answer(pending[0], 7);                            // the slow seven lands last
  await first;
  ok(sb.editsFor("7").field.per_team === 10, "the field stays at ten");

  // and the other way round: the old answer first, dropped; the new one kept
  pending.length = 0;
  sb.state.perTeam = null;
  sb.editsFor("7").field = null;
  const a = sb.loadField();
  await tick();
  sb.state.perTeam = 10;
  sb.editsFor("7").field = null;
  const b = sb.loadField();
  await tick();
  answer(pending[0], 7);
  await a;
  ok(!sb.editsFor("7").field, "a seven-runner answer to a page now asking ten is dropped");
  answer(pending[1], 10);
  await b;
  ok(sb.editsFor("7").field.per_team === 10, "...and the ten is kept");

  console.log(fails ? `\n${fails} FAILED` : "\nall passed");
  process.exit(fails ? 1 : 0);
})();
