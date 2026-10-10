/*
 * A track race is ONE EVENT of one division (owner, 2026-10-10: "predictions
 * for tf races are majorly messed up"). The race link names the event and
 * the page used to drop it; a track event scores by event points, not by
 * cross country places; track times read to the hundredth.
 *
 * The functions are lifted out of predictions.js and run for real:
 *     node tests/test_predict_tf_event.js
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

// predictions.js's fmtTime delegates to the shared formatter (fmt-time.js)
const { rcFmtTime } = require("../racecast/static/fmt-time.js");

const sb = eval(`(() => {
  const state = { meet: null, perTeam: null };
  const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const schoolWithState = (t, s) => s ? t + " (" + s + ")" : t;
  const schoolCell = (t) => esc(t);
  const pct = (p) => p == null ? null : Math.round(p * 100) + "%";
  const findKey = (parts) => parts.filter(Boolean).join(" ").toLowerCase();
  ${grab("parseMeetLink")}
  ${grab("raceLink")}
  ${grab("isTrack")}
  ${grab("fmtTime")}
  ${grab("fmtRaceTime")}
  ${grab("parsePerTeam")}
  ${grab("ordinal")}
  ${grab("pointsSpreadTip")}
  ${grab("eventPointsTable")}
  ${grab("teamScoreTable")}
  return { state, parseMeetLink, raceLink, fmtRaceTime, parsePerTeam,
           teamScoreTable, ordinal };
})()`);

(function links() {
  const r = sb.parseMeetLink("/race/tf/5501/88/3?alt=1");
  ok(r.sport === "TF" && r.id === "5501" && r.div === "3-88",
     "a track race link keeps its event: /race/tf/<meet>/<event>/<div> -> 3-88");
  ok(sb.parseMeetLink("/race/xc/94686/291").div === "291",
     "a cross country race link is the division, unchanged");
  ok(sb.raceLink("tf", 5501, "3-88") === "/race/tf/5501/88/3",
     "the race key goes back to the race page's link");
  ok(sb.raceLink("xc", 94686, "291") === "/race/xc/94686/291",
     "an XC key's link is unchanged");
})();

(function times() {
  sb.state.meet = { sport: "TF" };
  ok(sb.fmtRaceTime(271.074) === "4:31.07", "track: hundredths (4:31.07)");
  ok(sb.fmtRaceTime(119.996) === "2:00.00", "track: 119.996 carries to 2:00.00");
  ok(sb.parsePerTeam("2") === 2, "track: two entries per school is a real answer");
  sb.state.meet = { sport: "XC" };
  ok(sb.fmtRaceTime(987.84) === "16:27.8", "XC: still tenths (fmtTime)");
  ok(sb.parsePerTeam("2") === null, "XC: seven and under is the rulebook seven");
})();

(function points() {
  sb.state.meet = { sport: "TF" };
  const d = {
    scoring: "points",
    teams: [
      { team: "Alpha", score: 16, score_display: "16",
        runners: [{ person_id: 1, name: "A1", place: 1, seconds: 260, points: "10" },
                  { person_id: 4, name: "A2", place: 4, seconds: 263, points: "6" }] },
      { team: "Beta", score: 8, score_display: "8",
        runners: [{ person_id: 3, name: "B1", place: 3, seconds: 262, points: "8" }] },
    ],
  };
  const html = sb.teamScoreTable(d);
  ok(/Predicted event points/.test(html), "the heading says event points");
  ok(/Most points wins/.test(html) && !/Low score wins/.test(html)
     && !/five scorers/.test(html), "the rule is the track one, not XC's");
  ok(/1st \(10\)/.test(html) && /4th \(6\)/.test(html),
     "each scorer shows the place they took and what it paid");
  ok(!/Dual meet/.test(html) && !/Best seven/.test(html),
     "no cross country tools under a track event");
  ok(sb.ordinal(11) === "11th" && sb.ordinal(22) === "22nd", "ordinals");
})();

console.log(fails ? `\n${fails} check(s) failed` : "\nall track-event checks passed");
process.exit(fails ? 1 : 0);
