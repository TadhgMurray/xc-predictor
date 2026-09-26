/*
 * A shared prediction's lineups go back on the cards (owner, 2026-09-26:
 * "do the shared prediction links next"). applySharedField runs for real,
 * lifted out of predictions.js with addRunner and editsFor:
 *     node tests/test_predict_share_restore.js
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

const sb = eval(`(() => {
  const _edits = new Map();
  const state = { meet: { div: "7" } };
  const activeBlocks = () => ["7"];
  ${grab("divKey")}
  ${grab("editsFor")}
  ${grab("addRunner")}
  ${grab("applySharedField")}
  return { editsFor, applySharedField };
})()`);

let fails = 0;
const check = (ok, msg) => { console.log((ok ? "  ok  " : "  FAIL") + " " + msg); if (!ok) fails++; };

const e = sb.editsFor("7");
e.field = { teams: [
  { school: "Jesuit", runners: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => ({ person_id: i, name: "J" + i })) },
  { school: "Loyola", runners: [11, 12, 13, 14, 15].map((i) => ({ person_id: i, name: "L" + i })) },
  { school: "Gone HS", runners: [21, 22].map((i) => ({ person_id: i, name: "G" + i })) },
] };
const field = JSON.stringify([["Jesuit", ["1", "2", "3", "4", "5", "6", "9"], 7],
                              ["Loyola", ["11", "12", "13", "14", "15"], 5]]);
sb.applySharedField(new URLSearchParams({ field }), { "9": "New Kid" });

check(e.removed.has("7") && e.removed.has("8"), "Jesuit's 7th and 8th come off the card");
check(!e.removed.has("1") && !e.removed.has("6"), "Jesuit's kept runners stay");
const jes = e.field.teams[0].runners.map((r) => String(r.person_id));
check(jes.includes("9"), "the added runner goes on Jesuit's card");
check(e.added.some((a) => a.person_id === "9" && a.name === "New Kid"),
      "under the name the link carried");
check(e.removed.has("21") && e.removed.has("22"), "a team not in the shared field comes off whole");
check(!["11", "12", "13", "14", "15"].some((i) => e.removed.has(i)), "Loyola unchanged");

if (fails) { console.error(`${fails} check(s) failed`); process.exit(1); }
console.log("all checks passed");
