/*
 * ★ BACK RETURNS THE PREDICTION AS IT WAS (owner, 2026-10-18). writeOutput
 * keeps the rendered result, the results the tools read and the scroll;
 * restoreOutput puts them back for the same meet and mode, and for no other.
 *     node tests/test_predict_back_restore.js
 */
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

function grab(name) {
  const i = SRC.indexOf("function " + name + "(");
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

function page() {
  const store = {};
  const out = { innerHTML: "" };
  const log = { scrolled: null, champs: 0, shared: 0 };
  const sb = eval(`(() => {
    const SESSION_KEY = "rc-predict-v1";
    const OUTPUT_KEY = SESSION_KEY + ":output";
    const sessionStorage = { getItem: (k) => (k in store ? store[k] : null),
                             setItem: (k, v) => { store[k] = String(v); },
                             removeItem: (k) => { delete store[k]; } };
    const $ = (id) => (id === "output" ? out : null);
    const window = { scrollY: 640, scrollTo: (x, y) => { log.scrolled = y; } };
    const requestAnimationFrame = (f) => f();
    const document = { querySelectorAll: () => [] };
    const state = { meet: { id: "268246" }, who: "team",
                    lastTargets: [["1094606"]], lastResults: [{ teams: [1] }] };
    const showChamps = () => { log.champs++; };
    const applyScale = () => {};
    const headShare = () => { log.shared++; };
    const buildQuery = () => new URLSearchParams("meet_id=268246");
    ${grab("writeOutput")}
    ${grab("writeScroll")}
    ${grab("restoreOutput")}
    return { state, writeOutput, writeScroll, restoreOutput };
  })()`);
  return { sb, out, log, store };
}

const p = page();
p.out.innerHTML = "<div class='team-result'>Clinton 1st</div>";
p.sb.writeOutput();
p.sb.writeScroll();
p.out.innerHTML = "";
p.sb.state.lastResults = [];
ok(p.sb.restoreOutput() === true, "the same meet and mode restore the prediction");
ok(p.out.innerHTML.indexOf("Clinton 1st") >= 0, "...the output drawn as it was");
ok(p.sb.state.lastResults.length === 1, "...and the results the tools read");
ok(p.log.scrolled === 640 && p.log.champs === 1 && p.log.shared === 1,
   "...scrolled back, the winners and the share link restored");

p.out.innerHTML = "";
p.sb.state.meet = { id: "999" };
ok(p.sb.restoreOutput() === false && p.out.innerHTML === "", "another meet does not");
p.sb.state.meet = { id: "268246" };
p.sb.state.who = "individual";
ok(p.sb.restoreOutput() === false, "another mode does not");

const q = page();
q.out.innerHTML = "";
q.sb.writeOutput();
ok(!("rc-predict-v1:output" in q.store), "an empty page keeps nothing");

if (fails) { console.error(`${fails} failed`); process.exit(1); }
console.log("all ok");
