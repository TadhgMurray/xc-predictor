/*
 * The meet the picker chose is ONE meet, and every request says which
 * (owner, 2026-10-05: "NCAA Division III Cross Country Championships 2025"
 * read "ran 2009-10-13 · Warinanco Park" -- a 2009 New Jersey meet that
 * shares its meet_id). The anet and tfrrs id spaces collide; the search
 * results and the meet pages tell the two apart with an opaque ?alt=N, and
 * the predictions page dropped it.
 *
 * The functions are lifted out of predictions.js and run for real:
 *     node tests/test_predict_meet_alt.js
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
function body(name, kw) { return grab(name, kw); }

let fails = 0;
const ok = (cond, msg) => {
  console.log((cond ? "  ok  " : "  FAIL") + " " + msg);
  if (!cond) fails++;
};

const requests = [];
const sb = eval(`(() => {
  const _edits = new Map();
  const state = { meet: null, when: "asran", who: "team", course: null,
                  coalesce: false, divs: [], groups: [] };
  const $ = () => ({ value: "2026-11-21" });
  const setStatus = () => {};
  const fetch = async (url) => {
    requests.push(url);
    return { ok: true, status: 200,
             text: async () => JSON.stringify({ teams: [] }) };
  };
  ${grab("readJson", "async function ")}
  ${grab("divKey")}
  ${grab("editsFor")}
  ${grab("parseMeetLink")}
  ${grab("buildQuery")}
  ${grab("fetchField", "async function ")}
  ${grab("squadCtx")}
  ${grab("squadKey")}
  ${grab("squadParams")}
  const _divGender = new Map();
  return { state, parseMeetLink, buildQuery, fetchField, squadCtx, squadKey,
           squadParams };
})()`);

(async () => {
  /* 1. the link the search results hand over carries the meet */
  {
    const m = sb.parseMeetLink("/meet/xc/94686?alt=1");
    ok(m && m.id === "94686" && m.sport === "XC" && m.alt === 1,
       "a meet link's ?alt= is kept");
    const r = sb.parseMeetLink("/race/xc/94686/291?alt=0");
    ok(r && r.div === "291" && r.alt === 0, "a race link's too");
    ok(sb.parseMeetLink("/meet/tf/5").alt === null,
       "a bare link is the biggest meet of the id (null)");
    ok(sb.parseMeetLink("/race/xc/94686/22?school=X&alt=2").alt === 2,
       "wherever it sits in the query");
  }

  /* 2. every request names it: the field, the prediction (and with it the
        weather, the lineup and the share link), the squads */
  {
    sb.state.meet = { id: "94686", sport: "XC", div: "22", alt: 1 };
    const q = sb.buildQuery("22");
    ok(q.get("alt") === "1" && q.get("meet_id") === "94686",
       "the prediction request carries ?alt=");
    await sb.fetchField("22");
    const fq = new URLSearchParams(requests[requests.length - 1].split("?")[1]);
    ok(fq.get("alt") === "1", "the field request carries ?alt=");
    const ctx = sb.squadCtx("22");
    ok(sb.squadParams(ctx).get("alt") === "1", "the squad request carries it");

    /* the squad cache keeps the two meets apart: "as it ran" reads the
       chosen meet's season, so the other meet's squad is another answer */
    const k1 = sb.squadKey("Tufts", ctx, "");
    sb.state.meet.alt = null;
    const k0 = sb.squadKey("Tufts", ctx, "");
    ok(k1 !== k0, "the squad cache key includes the meet");
    ok(!sb.buildQuery("22").has("alt") && !sb.squadParams(ctx).has("alt"),
       "the biggest meet sends no ?alt= (the site's bare link)");
  }

  /* 3. the wiring the sandbox cannot run: the race list sends and keeps the
        server's index, the chosen meet links to the right meet page and
        shows that meet's date, a shared link reopens the same meet */
  {
    const loadRaces = body("loadRaces", "async function ");
    ok(/q\.set\("alt", state\.meet\.alt\)/.test(loadRaces),
       "the race list asks for the chosen meet");
    ok(/state\.meet\.alt = data\.alt/.test(loadRaces),
       "and keeps the index the server resolved");
    ok(/\$\("mc-date"\)/.test(loadRaces) && /id="mc-date"/.test(SRC),
       "the 'ran' date is redrawn from that meet's own date");
    const chosen = body("renderChosenMeet");
    ok(/\?alt=\$\{esc\(state\.meet\.alt\)\}/.test(chosen),
       "the meet name links to that meet's page");
    const restore = body("restoreFromQuery", "async function ");
    ok(/p\.get\("alt"\)/.test(restore) && /rq\.set\("alt", alt\)/.test(restore)
       && /\?alt=\$\{alt\}/.test(restore),
       "a shared prediction reopens the meet it named");
    const rs = body("restoreState");
    ok(/linkedAlt !== savedAlt/.test(rs),
       "a link to the id's other meet is not answered by a saved session");
  }

  if (fails) { console.error(`${fails} check(s) failed`); process.exit(1); }
  console.log("all meet-alt checks passed");
})();
