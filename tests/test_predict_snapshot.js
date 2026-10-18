/*
 * ★ A SHARED LINK SHOWS THE PREDICTION THAT WAS SHARED (owner, 2026-10-18:
 * "snapshot."). A ?s= link whose row carries a snapshot is drawn at once,
 * dated, with a button to run it again -- the prediction is NOT re-run on
 * open. A link without one (an old link, an individual prediction) restores
 * and predicts exactly as before. restoreState, restoreFromQuery,
 * showSnapshot and snapNote run for real, the page around them stubbed:
 *     node tests/test_predict_snapshot.js
 */
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

function grab(name, kw) {
  const i = SRC.indexOf((kw || "function ") + name + "(");
  if (i < 0) { console.error(`${name} not found in predictions.js`); process.exit(1); }
  const open = SRC.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(i, j + 1);
  }
  console.error(`${name} is unbalanced`); process.exit(1);
}

let fails = 0;
const ok = (cond, msg) => {
  console.log((cond ? "  ok  " : "  FAIL") + " " + msg);
  if (!cond) fails++;
};

const SNAP = { runners: [{ name: "A Runner", seconds: 900 }],
               teams: [{ team: "Jesuit", score: 41 }] };

function page(share) {
  const els = {};
  const el = (id) => (els[id] = els[id] || {
    id, value: "", checked: false, hidden: false, open: false, innerHTML: "",
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
  });
  const log = { predicted: 0, fieldLoaded: 0, applied: 0, champs: null, wrote: 0 };
  const sb = eval(`(() => {
    const $ = (id) => el(id);
    const document = { querySelectorAll: () => [] };
    const window = {};
    const location = { search: "?s=abcdefabcdef" };
    const SESSION_KEY = "rc-predict-v1";
    const sessionStorage = { getItem: () => null, setItem() {} };
    const state = { meet: null, when: "thisyear", who: "team", divs: [],
                    raceMode: "separate", groups: [], coalesce: false,
                    course: null, perTeam: null, athletes: [] };
    const fetch = async (url) => ({ ok: true, json: async () =>
      url.indexOf("/api/predict/share/") === 0 ? share
        : { meet_name: "2025 Clovis Invitational", date: "2025-10-11" } });
    const esc = (s) => String(s);
    const showWhen = (w) => { state.when = w; };
    const showWho = (w) => { state.who = w; };
    const raceLink = (s, id, d) => "/race/" + s + "/" + id + "/" + d;
    const chooseMeet = async (d) => { state.meet = { id: "91", label: d.label }; };
    const normalizeGroups = (d, g) => g;
    const syncPerTeam = () => {};
    const syncPick = () => {};
    const loadRaces = async () => {};
    const loadField = async () => { log.fieldLoaded++; };
    const applySharedField = () => { log.applied++; };
    const renderField = () => {};
    const renderAthletes = () => {};
    const saveState = () => {};
    const setStatus = () => {};
    const openOpts = () => {};
    const parsePerTeam = () => null;
    const parseSkip = () => 0;
    const syncSkip = () => {};
    const renderTeam = (d) => "<table>" + d.teams[0].team + " " + d.teams[0].score + "</table>";
    const showChamps = (d) => { log.champs = d; };
    const applyScale = () => {};
    const writeOutput = () => { log.wrote++; };
    const predict = () => { log.predicted++; };
    ${grab("snapNote")}
    ${grab("showSnapshot")}
    ${grab("restoreFromQuery", "async function ")}
    ${grab("restoreState")}
    return { state, restoreState };
  })()`);
  return { sb, el, log };
}

const settle = () => new Promise((r) => setTimeout(r, 20));

(async () => {
  // ---- a link with its snapshot: drawn, dated, not re-run
  let t = page({ query: "mode=rerun&meet_id=91&sport=XC&div_id=3&date=2026-10-17",
                 extra: { names: {}, snapshot: SNAP,
                          snapshot_at: "2026-10-18T14:05:00+00:00" } });
  t.sb.restoreState();
  await settle();
  const html = t.el("output").innerHTML;
  ok(t.log.predicted === 0, "a snapshot link does not run the prediction again");
  ok(html.indexOf("<table>Jesuit 41</table>") >= 0, "...it draws the shared result");
  ok(/as it was shared, made Oct 18, 2026/.test(html), "...says when it was made");
  ok(html.indexOf("data-snap-rerun") >= 0, "...and offers to run it again");
  ok(t.sb.state.lastResults[0] === SNAP, "...the Best 7 / head-to-head tools read it");
  ok(String(t.sb.state.lastTargets[0]) === "3", "...for the race the link named");
  ok(t.log.champs === SNAP, "...the header names its winners");
  ok(t.log.fieldLoaded === 1 && t.log.applied === 1,
     "...and the cards still fill in with the shared lineup");
  ok(t.log.wrote === 1, "...and Back keeps it (writeOutput)");

  // ---- an old link (no snapshot): restored and predicted, as before
  t = page({ query: "mode=rerun&meet_id=91&sport=XC&div_id=3", extra: { names: {} } });
  t.sb.restoreState();
  await settle();
  ok(t.log.predicted === 1, "a link without a snapshot predicts, as before");
  ok(t.el("output").innerHTML === "", "...and draws nothing of its own");

  // ---- an individual link never takes the snapshot path
  t = page({ query: "mode=rerun&meet_id=91&sport=XC&person_id=5",
             extra: { names: { 5: "Ann" }, snapshot: SNAP } });
  t.sb.restoreState();
  await settle();
  ok(t.log.predicted === 1, "an individual prediction's link predicts");

  console.log(fails ? `\n${fails} FAILED` : "\nall passed");
  process.exit(fails ? 1 : 0);
})();
