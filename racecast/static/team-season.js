/*
 * team-season.js -- the three charts on /school/<name>/season
 * (team_season.html, team_tracker.py; owner 2026-10-10).
 *
 * ★ athlete-charts.js DRAWS THEM. drawChart is the athlete page's own
 *   function (loaded first, a global), so the axes, gridlines, dots,
 *   tooltips and resize behaviour are the athlete charts' exactly. This
 *   file only turns the weeks into its point shape:
 *     { d: week-ending date, v: value, y: season, sp: "XC", meet: words }
 *
 * ! THE PLACE CHART IS UPSIDE DOWN ON PURPOSE: 1st belongs at the top, so
 *   the value drawn is minus the place and the axis prints it back
 *   positive, whole numbers only (opts.integer).
 */
"use strict";

(function () {
  const node = document.getElementById("ts-data");
  if (!node || typeof drawChart !== "function") return;
  let weeks;
  try { weeks = JSON.parse(node.textContent) || []; } catch (e) { return; }

  function ordinal(n) {
    n = Math.round(n);
    const s = (n % 100 >= 11 && n % 100 <= 13) ? "th"
      : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
    return n + s;
  }

  function words(w) {
    const bits = [];
    if (w.meet_names && w.meet_names.length) bits.push(w.meet_names.join(", "));
    if (w.place) bits.push(`${ordinal(w.place)} of ${w.n_teams}`);
    return bits.join(" · ");
  }

  function series(key) {
    return weeks.filter((w) => w[key] !== null && w[key] !== undefined).map((w) => ({
      d: w.date, y: Number(w.date.slice(0, 4)), sp: "XC", meet: words(w),
      v: key === "place" ? -w.place : w[key]
    }));
  }

  const FMT = {
    top5: (v) => Number(v).toFixed(1),
    gap15: (v) => Number(v).toFixed(1),
    place: (v) => ordinal(-v)
  };

  const drawn = [];
  document.querySelectorAll("[data-ts]").forEach((host) => {
    const key = host.dataset.ts;
    const opts = { title: host.dataset.title, fmt: FMT[key], integer: key === "place" };
    const pts = series(key);
    drawChart(host, pts, opts);
    drawn.push({ host, pts, opts });
  });

  if (typeof ResizeObserver === "undefined") return;
  let pending = false;
  const ro = new ResizeObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      drawn.forEach(({ host, pts, opts }) => drawChart(host, pts, opts));
    });
  });
  drawn.forEach(({ host }) => ro.observe(host));
})();
