/*
 * (owner, 2026-10-13: "you remove an athlete and they get added back
 * automatically by something else"). A removed runner is recorded in
 * e.removed; every re-render of the cards must draw the roster WITHOUT
 * them, count without them, and the removal must be saved.
 *     node tests/test_predict_removed_stay_off.js
 */
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

function body(name) {
  const i = SRC.indexOf("function " + name + "(");
  const open = SRC.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(i, j + 1);
  }
  throw new Error(name + " unbalanced");
}

let failed = 0;
function ok(cond, msg) { if (!cond) { failed++; console.error("FAIL", msg); } }

const r = body("renderFieldBlock");
ok(/\.removed/.test(r), "the card renderer reads the removed set");
ok(/live\(t\)\.map\(/.test(r), "the runner rows come from the live (not removed) list");
ok(!/\$\{t\.runners\.map\(/.test(r), "no row is drawn from t.runners whole");
ok(/team-n">\$\{live\(t\)\.length\}/.test(r), "the card count leaves removed runners out");

const k = SRC.indexOf('const rm = e.target.closest("[data-remove]")');
const handler = SRC.slice(k, SRC.indexOf("return;", k));
ok(/removed\.add\(/.test(handler) && /saveState\(\)/.test(handler),
   "removing records the id and saves state");

if (failed) process.exit(1);
console.log("ok");
