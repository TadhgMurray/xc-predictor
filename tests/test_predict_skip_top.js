/*
 * ★ SKIP A TEAM'S TOP N (owner, 2026-10-18: "for jv races you remove the
 * top 7 that are gonna run in the varsity race ... for an entire race, and
 * for individual teams"). applyTeamSkip runs for real:
 *     node tests/test_predict_skip_top.js
 */
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");
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
const applyTeamSkip = eval(`(${grab("applyTeamSkip")})`);
const ids = (rs) => rs.map((r) => r.person_id);

// the field as the server sends it: lineup of 7, the rest on the bench,
// one who stopped racing, one original runner with no race this season
const team = () => ({
  school: "S",
  runners: [1, 2, 3, 4, 5, 6, 7].map((i) => ({ person_id: i })),
  dropped: [...[8, 9, 10, 11, 12, 13, 14, 15].map((i) => ({ person_id: i, bench: true })),
            { person_id: 99, bench: true, stopped: 4 },
            { person_id: 50 }],
});

let t = team();
applyTeamSkip(t, 7);
ok(ids(t.runners).join() === "8,9,10,11,12,13,14", "skip 7: the next seven run");
ok(ids(t.dropped.filter((r) => r.skipped)).join() === "1,2,3,4,5,6,7", "...the best seven wait, marked skipped");
ok(t.dropped.some((r) => r.person_id === 99 && r.stopped) && t.dropped.some((r) => r.person_id === 50),
   "...who stopped racing and the absent original stay where they were");
ok(!t.runners.some((r) => r.person_id === 99), "...and who stopped racing is never fielded");

applyTeamSkip(t, 0);
ok(ids(t.runners).join() === "1,2,3,4,5,6,7", "skip 0 puts the team back as it was");
ok(!t.dropped.some((r) => r.skipped), "...nobody marked skipped");

t = team();
applyTeamSkip(t, 3);
applyTeamSkip(t, 5);
ok(ids(t.runners).join() === "6,7,8,9,10,11,12", "a second number replaces the first (5, not 3 + 5)");

t = team();
t.runners.push({ person_id: 77, added: true });
applyTeamSkip(t, 7);
ok(ids(t.runners).includes(77) && t.runners.length === 8, "a hand-added runner stays on the card");

t = team();
applyTeamSkip(t, 12);
ok(ids(t.runners).join() === "13,14,15", "past the squad's depth: whoever is left");

// a field the server already skipped 7 on (skipped first in dropped)
t = { school: "S",
      runners: [8, 9, 10, 11, 12, 13, 14].map((i) => ({ person_id: i })),
      dropped: [...[1, 2, 3, 4, 5, 6, 7].map((i) => ({ person_id: i, bench: true, skipped: true })),
                { person_id: 15, bench: true }] };
applyTeamSkip(t, 0);
ok(ids(t.runners).join() === "1,2,3,4,5,6,7", "one team back to its best on a race-wide skip");

console.log(fails ? `\n${fails} FAILED` : "\nall passed");
process.exit(fails ? 1 : 0);
