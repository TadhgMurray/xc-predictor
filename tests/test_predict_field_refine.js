/*
 * ★ THE FIELD AT ONCE, THEN BY PREDICTION (owner, 2026-10-18: "can we fix
 * the field loading speed?"). Under "Pick runners by: Prediction" the field
 * is asked for by rating first (fast, no model) and drawn; the predicted
 * order lands after and replaces it, keeping hand-added runners. loadField,
 * setField, refineFields, fieldQuery and fetchField run for real:
 *     node tests/test_predict_field_refine.js
 */
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");
function grab(name, kw) {
  const i = SRC.indexOf((kw || "function ") + name + "(");
  if (i < 0) { console.error(name + " missing"); process.exit(1); }
  const open = SRC.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(i, j + 1);
  }
}
let fails = 0;
const ok = (c, m) => { console.log((c ? "  ok  " : "  FAIL") + " " + m); if (!c) fails++; };

const pending = [];
let renders = 0;
const sb = eval(`(() => {
  const $ = () => null;
  const state = { meet: { id: "268246", sport: "XC", div: "7" }, when: "thisyear",
                  perTeam: null, skipTop: 0, pick: "predicted", course: null, divs: [] };
  const _edits = new Map();
  const editsFor = (d) => { if (!_edits.has(d)) _edits.set(d, { added: [], removed: new Set() });
                            return _edits.get(d); };
  const activeBlocks = () => ["7"];
  const renderField = () => { renders++; };
  const showBasis = () => {};
  const saveState = () => {};
  const setStatus = () => {};
  const isTrack = () => false;
  const applyTeamSkip = () => {};
  const readJson = async (r) => r.body;
  const fetch = (url) => new Promise((res) => pending.push({ url, res }));
  ${grab("fieldQuery")}
  ${grab("fetchField", "async function ")}
  ${grab("setField")}
  ${grab("refineFields", "async function ")}
  ${grab("loadField", "async function ")}
  return { state, editsFor, loadField };
})()`);

const tick = () => new Promise((r) => setTimeout(r, 0));
const team = (ids, extra) => ({ teams: [{ school: "WPI",
  runners: ids.map((i) => ({ person_id: String(i), name: "R" + i })), dropped: [] }], ...extra });

(async () => {
  const load = sb.loadField();
  await tick();
  ok(pending.length === 1 && !/pick=predicted/.test(pending[0].url), "first ask: by rating, no model");
  pending[0].res({ ok: true, body: team([1, 2, 3], { pick: "rating" }) });
  await load;
  await tick();
  const f = sb.editsFor("7").field;
  ok(f && f.quick && f.teams[0].runners.length === 3, "the rating field is drawn at once, marked quick");
  ok(pending.length === 2 && /pick=predicted/.test(pending[1].url), "...and the predicted order is asked beside it");

  // a hand-add while the prediction is out
  const e = sb.editsFor("7");
  f.teams[0].runners.push({ person_id: "9", name: "Added", added: true, rating: 101 });
  e.added.push({ person_id: "9", name: "Added", school: "WPI" });

  pending[1].res({ ok: true, body: team([3, 1, 2], { pick: "predicted" }) });
  await sb.state.refine;
  const g = sb.editsFor("7").field;
  ok(!g.quick && g.pick === "predicted", "the predicted field replaces it");
  ok(g.teams[0].runners.map((r) => r.person_id).join() === "3,1,2,9",
     "...in predicted order, the hand-added runner kept");
  ok(g.teams[0].runners[3].rating === 101, "...with the row it had");

  // a ten-runner ask in between drops the stale prediction
  pending.length = 0;
  sb.editsFor("7").field = null;
  const l2 = sb.loadField();
  await tick();
  pending[0].res({ ok: true, body: team([1, 2], {}) });
  await l2;
  await tick();
  sb.state.perTeam = 10;
  sb.editsFor("7").field = null;
  const l3 = sb.loadField();
  await tick();
  pending[1].res({ ok: true, body: team([5, 6], { pick: "predicted", stale: true }) });   // the old prediction
  await tick();
  ok(!sb.editsFor("7").field || !sb.editsFor("7").field.stale, "a prediction for the old size is dropped");
  pending[2].res({ ok: true, body: team([1, 2, 3, 4], {}) });
  await l3;
  await tick();
  ok(sb.editsFor("7").field && sb.editsFor("7").field.quick, "the new size's quick field is drawn");

  console.log(fails ? `\n${fails} FAILED` : "\nall passed");
  process.exit(fails ? 1 : 0);
})();
