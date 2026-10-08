/*
 * Owner, 2026-10-08: "if you are predicting multiple races and you press add
 * from squad it adds to the first squad in a different race". A card's squad
 * list, its add button and its remove button act on THAT card's race.
 *     node tests/test_predict_card_race.js
 */
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");
let fails = 0;
function check(ok, msg) { if (!ok) { console.error("FAIL: " + msg); fails++; } }

// the squad list is looked up inside the clicked card first
check(/card && card\.querySelector\(sel\)/.test(SRC),
      "the squad list is found inside the card, not page-wide");
// the add button writes to the card's race
const add = SRC.slice(SRC.indexOf('const add = e.target.closest("[data-add]")'));
check(/addRunner\(add\.dataset\.school[\s\S]{0,200}cardDiv,/.test(add.slice(0, 900)),
      "add passes the card's race (cardDiv) to addRunner");
// remove writes to the card's race too
check(/editsFor\(cardDiv === undefined \? state\.meet\.div : cardDiv\)\.removed\.add/.test(SRC),
      "remove writes to the card's race");
// and the open squad list filters against that race's own field
check(/const ed = editsFor\(cardDiv === undefined \? state\.meet\.div : cardDiv\);\s*const team = \(ed\.field/.test(SRC),
      "the open squad list compares against the card race's field");

if (fails) process.exit(1);
console.log("ok: a card acts on its own race");
