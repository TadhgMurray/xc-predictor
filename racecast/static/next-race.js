/*
 * next-race.js -- fills the "Next race" line on the athlete and school pages
 * (_next_race.html, upcoming_preview.py; owner 2026-10-10).
 *
 *   Clovis Invite, Sat Oct 18: predicted ~15:02, about 12th of 180
 *     Varsity · Boys · course +2.1% · forecast 14°C, sunny
 *
 * ★ ONE FETCH OF A CACHED ENDPOINT. The page is edge cached; the answer
 *   comes from /api/next-race/..., cached per athlete / school per day on
 *   the server and at the edge. No answer, no posted meet, an error: the
 *   line simply stays hidden -- it was never on the page.
 *
 * ! "~" AND "ABOUT" ARE THE POINT. A prediction for a meet whose entries are
 *   not posted is a guess about the field as well as the time; the "?"
 *   says whose field it is.
 */
"use strict";

(function () {
  function esc(v) {
    if (v === null || v === undefined) return "";
    return String(v).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }

  /* "Oct 19, 2025" from an ISO date, built from the string (no UTC shift) */
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function mdy(iso) {
    const [y, m, d] = String(iso || "").slice(0, 10).split("-");
    return y && m && d ? `${MONTHS[Number(m) - 1]} ${Number(d)}, ${y}` : "";
  }

  function conditions(r) {
    const bits = [];
    /* the label usually says the gender already ("Boys Varsity Gold") */
    const said = /\b(boy|girl|men|women)/i.test(r.race || "");
    if (r.race) bits.push(esc(r.race) + (r.gender && !said ? ` · ${r.gender === "M" ? "Boys" : "Girls"}` : ""));
    if (r.difficulty_pct) bits.push(`course ${esc(r.difficulty_pct)}`);
    if (r.weather) bits.push(`${r.weather_kind === "forecast" ? "forecast" : "usually"} ${esc(r.weather)}`);
    return bits.join(" · ");
  }

  function basis(r) {
    const blurb = "No entries are posted for this meet, so the field is the teams that ran its " +
      `last edition (${mdy(r.edition_date)}), each with this season's squad. The time is the ` +
      "prediction model's on this course and distance; the place is among that projected field.";
    return `<span class="flag rank-note" tabindex="0" data-title="Predicted from the last edition" ` +
      `data-blurb="${esc(blurb)}" aria-label="${esc(blurb)}">?</span>`;
  }

  function athleteRow(r) {
    const place = r.place ? `, about <strong>${esc(r.place_word)}</strong> of ${esc(r.n_field)}` : "";
    return `<span class="rk"><a href="${esc(r.preview_href)}">${esc(r.name)}</a>, ${esc(r.date_label)}</span>: ` +
      `predicted <strong>~${esc(r.time)}</strong>${place} ` +
      `<span class="rl-floor">${conditions(r)}</span> ${basis(r)}`;
  }

  function schoolRow(r) {
    const g = r.gender === "F" ? "Girls" : r.gender === "M" ? "Boys" : esc(r.race);
    const place = r.team_place
      ? `projected <strong>${esc(r.team_place_word)}</strong> of ${esc(r.n_teams)} teams (${esc(r.score)} pts)`
      : "no projected team score (fewer than five runners)";
    const top = (r.top && r.top[0])
      ? `, led by <a href="/athlete/${esc(r.top[0].person_id)}">${esc(r.top[0].name)}</a> ~${esc(r.top[0].time)}` : "";
    return `<span class="rk"><a href="${esc(r.preview_href)}">${esc(r.name)}</a>, ${esc(r.date_label)}</span>: ` +
      `<span class="nr-g">${g}</span> ${place}${top} ` +
      `<span class="rl-floor">${conditions(Object.assign({}, r, { race: null, gender: null }))}</span> ${basis(r)}`;
  }

  document.querySelectorAll("[data-next-race]").forEach((line) => {
    const url = line.dataset.nextRace;
    if (!url) return;
    fetch(url, { credentials: "omit" })
      .then((res) => (res.ok ? res.json() : null))
      .then((d) => {
        if (!d || !d.available || !d.races || !d.races.length) return;
        const row = line.dataset.kind === "school" ? schoolRow : athleteRow;
        line.innerHTML = `<span class="rl-lab">Next race</span>` +
          d.races.map(row).join(`<span class="nr-br"></span>`);
        line.hidden = false;
      })
      .catch(() => {});
  });
})();
