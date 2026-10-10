/*
 * meet-preview.js -- the race tabs on /meet/preview/xc/<id>
 * (meet_preview.html, upcoming_preview.py; owner 2026-10-10).
 *
 * ★ ONE RACE AT A TIME. Each tab fetches its race's projection from
 *   /api/meet-preview/xc/<meet>/<div> -- cached per race on the server and
 *   at the edge -- so opening the page costs one prediction, not twelve.
 *   Answers are kept here too, so flipping back to a tab is instant.
 *
 * ! THE HEADER'S "PROJECTED WINNER" IS THE OPEN RACE'S. It follows the tab,
 *   so it never names a girls' winner over the boys' race.
 */
"use strict";

(function () {
  const main = document.querySelector("main.pv-page");
  const tabs = main && main.querySelector(".pv-races");
  if (!tabs) return;
  const meet = main.dataset.meet;
  const src = main.dataset.src ? "?src=" + encodeURIComponent(main.dataset.src) : "";
  const panel = main.querySelector(".pv-panel");
  const status = panel.querySelector(".pv-status");
  const body = panel.querySelector(".pv-body");
  const open = main.querySelector(".pv-open");
  const champs = main.querySelector(".pv-champs");
  const seen = {};

  function esc(v) {
    if (v === null || v === undefined) return "";
    return String(v).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }

  function team(t) {
    const name = t.href ? `<a href="${esc(t.href)}">${esc(t.label)}</a>` : esc(t.label);
    const sc = (t.scorers || []).map((p) => `<i>${p == null ? "-" : esc(p)}</i>`).join("");
    return `<tr><td class="pl">${t.place}</td><td class="nm">${name}` +
      `<span class="rc-subline">${(t.scorers || []).map(esc).join(" · ")}</span></td>` +
      `<td class="rc-wide pv-sc">${sc}</td><td class="n"><b>${esc(t.score)}</b></td></tr>`;
  }

  function runner(r) {
    const school = r.href ? `<a href="${esc(r.href)}">${esc(r.school)}</a>` : esc(r.school || "");
    return `<tr><td class="pl">${esc(r.place)}</td>` +
      `<td class="nm"><a href="/athlete/${esc(r.person_id)}">${esc(r.name)}</a>` +
      `<span class="rc-subline">${esc(r.school || "")}${r.grade ? " · " + esc(r.grade) : ""}</span></td>` +
      `<td class="sch rc-wide">${school}${r.grade ? `<span class="gr">${esc(r.grade)}</span>` : ""}</td>` +
      `<td class="n tm">${esc(r.time || "")}</td></tr>`;
  }

  function draw(d) {
    if (!d || !d.available) {
      status.textContent = (d && d.reason) || "This race could not be projected just now.";
      status.hidden = false;
      body.hidden = true;
      champs.hidden = true;
      return;
    }
    body.querySelector(".pv-teams tbody").innerHTML = d.teams.map(team).join("") ||
      `<tr><td colspan="4" class="meta">No team brings five runners.</td></tr>`;
    body.querySelector(".pv-runners tbody").innerHTML = d.runners.map(runner).join("");
    body.querySelector(".pv-nt").textContent =
      d.n_teams > d.teams.length ? `top ${d.teams.length} of ${d.n_teams}` : `${d.n_teams} teams`;
    body.querySelector(".pv-nf").textContent = `of ${d.n_field} projected starters`;
    status.hidden = true;
    body.hidden = false;
    const t0 = d.teams[0], r0 = d.runners[0];
    if (t0 || r0) {
      champs.querySelector(".pv-tw").textContent = t0 ? t0.label : "-";
      champs.querySelector(".pv-tws").textContent = t0 ? `${t0.score} pts` : "";
      champs.querySelector(".pv-iw").textContent = r0 ? r0.name : "-";
      champs.querySelector(".pv-iwt").textContent = r0 ? r0.time : "";
      champs.hidden = false;
    }
  }

  function load(btn) {
    tabs.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("is-on", b === btn));
    if (open && btn.dataset.predict) open.href = btn.dataset.predict;
    const div = btn.dataset.div;
    if (seen[div]) { draw(seen[div]); return; }
    status.textContent = "Projecting this race…";
    status.hidden = false;
    body.hidden = true;
    fetch(`/api/meet-preview/xc/${encodeURIComponent(meet)}/${encodeURIComponent(div)}${src}`)
      .then((r) => r.json())
      .then((d) => { seen[div] = d; if (btn.classList.contains("is-on")) draw(d); })
      .catch(() => draw(null));
  }

  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    e.preventDefault();
    history.replaceState(null, "", "#race-" + btn.dataset.div);
    load(btn);
  });

  const want = (location.hash.match(/^#race-(\d+)$/) || [])[1];
  const first = (want && tabs.querySelector(`[data-div="${want}"]`)) ||
    tabs.querySelector(".seg-btn.is-on") || tabs.querySelector(".seg-btn");
  if (first) load(first);
})();
