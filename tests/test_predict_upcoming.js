/*
 * A meet not yet run, opened from /meets?view=upcoming (owner, 2026-10-06:
 * "Make predicting an UPCOMING meet work"). The Coming-up link names the
 * feed that posted the meet (&src=tfrrs) -- with no results there is
 * nothing else to pick it by -- so every request carries it; and the field
 * the server borrowed from the meet's last edition is said so under the
 * meet (`basis`).
 *
 * The functions are lifted out of predictions.js and run for real:
 *     node tests/test_predict_upcoming.js
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

const requests = [];
const basisEl = { textContent: "", hidden: true };
const sb = eval(`(() => {
  const _edits = new Map();
  const state = { meet: null, when: "thisyear", who: "team", course: null,
                  coalesce: false, divs: [], groups: [] };
  const $ = (id) => id === "mc-basis" ? basisEl : ({ value: "2026-10-10" });
  const setStatus = () => {};
  const fetch = async (url) => {
    requests.push(url);
    return { ok: true, status: 200,
             text: async () => JSON.stringify({ teams: [] }) };
  };
  ${grab("readJson", "async function ")}
  ${grab("divKey")}
  ${grab("editsFor")}
  ${grab("activeBlocks")}
  ${grab("parseMeetLink")}
  ${grab("buildQuery")}
  ${grab("fetchField", "async function ")}
  ${grab("squadCtx")}
  ${grab("squadParams")}
  ${grab("showBasis")}
  const _divGender = new Map();
  return { state, parseMeetLink, buildQuery, fetchField, squadCtx,
           squadParams, showBasis, editsFor };
})()`);

(async () => {
  /* 1. the Coming-up link's feed rides into state.meet, and only the two */
  {
    ok(sb.parseMeetLink("/meet/xc/800?src=tfrrs").src === "tfrrs",
       "a link naming tfrrs keeps it");
    ok(sb.parseMeetLink("/race/xc/800/0?alt=1&src=tfrrs").src === "tfrrs",
       "beside an ?alt=");
    ok(sb.parseMeetLink("/meet/xc/800").src === null,
       "a search link names no feed");
    ok(sb.parseMeetLink("/meet/xc/800?src=evil").src === null,
       "and nothing else is taken");
  }

  /* 2. every request says it: the field, the prediction, the squads */
  {
    sb.state.meet = { id: "800", sport: "XC", div: "0", alt: null,
                      src: "tfrrs" };
    ok(sb.buildQuery("0").get("src") === "tfrrs",
       "the prediction request carries ?src=");
    await sb.fetchField("0");
    const fq = new URLSearchParams(requests[requests.length - 1].split("?")[1]);
    ok(fq.get("src") === "tfrrs", "the field request carries it");
    ok(sb.squadParams(sb.squadCtx("0")).get("src") === "tfrrs",
       "the squad request carries it");
    sb.state.meet.src = null;
    ok(!sb.buildQuery("0").has("src"), "a meet with no feed sends none");
  }

  /* 3. one line says where the field came from */
  {
    sb.state.meet = { id: "500", sport: "XC", div: "5001", alt: null };
    sb.editsFor("5001").field = { teams: [], basis: {
      kind: "last_edition", meet_id: 400, date: "2025-10-11",
      meet_name: "2025 Lakeside Invitational" } };
    sb.showBasis(["5001"]);
    ok(!basisEl.hidden && /last year's edition \(2025-10-11\)/.test(
         basisEl.textContent) && /Edit it below/.test(basisEl.textContent),
       "a borrowed field names the edition's date");
    sb.editsFor("5001").field = { teams: [], basis: { kind: "none" } };
    sb.showBasis(["5001"]);
    ok(!basisEl.hidden && /no earlier edition/.test(basisEl.textContent),
       "and says when there was nothing to borrow");
    sb.editsFor("5001").field = { teams: [], basis: null };
    sb.showBasis(["5001"]);
    ok(basisEl.hidden && basisEl.textContent === "",
       "a meet that ran says nothing");
  }

  /* 4. the wiring the sandbox cannot run */
  {
    const restore = grab("restoreFromQuery", "async function ");
    ok(/p\.get\("src"\)/.test(restore) && /rq\.set\("src", src\)/.test(restore),
       "a Coming-up link's feed reaches the race list");
    const loadRaces = grab("loadRaces", "async function ");
    ok(/q\.set\("src", state\.meet\.src\)/.test(loadRaces),
       "the race list asks for that feed's meet");
    ok(/data\.upcoming/.test(loadRaces),
       "and does not say 'ran' of a meet that has not");
    ok(/id="mc-basis"/.test(grab("renderChosenMeet")),
       "the line sits under the meet");
    ok(/showBasis\(/.test(grab("loadField", "async function ")),
       "drawn whenever the field loads");
  }

  if (fails) { console.error(`${fails} check(s) failed`); process.exit(1); }
  console.log("all upcoming-meet checks passed");
})();
