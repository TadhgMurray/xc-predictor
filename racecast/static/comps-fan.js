/*
 * comps-fan.js -- "runners like you" on the athlete page's rating chart
 * (comps.py nextSeasonFan, owner 2026-10-10).
 *
 *   Runners like you averaged 129.4 the next season (8 in 10 between 124.1
 *   and 134.0; 86 comps). Middle half shaded darker.
 *
 * ★ THE RANGE IS THE COMPS' OWN 10th-90th PERCENTILE (comps.FAN_Q): where
 *   eight in ten of the statistically similar runners actually landed the
 *   season after. Below ten comps there is no 10th percentile to speak of,
 *   and the caption gives the middle half instead.
 *
 * ! LOADED AFTER THE PAGE, FROM A CACHED ENDPOINT (/api/runners-like-you),
 *   and only for the sport whose chart exists. No answer, no fan: the chart
 *   is exactly what it was.
 */
"use strict";

(function () {
  const meta = document.querySelector("[data-person-id]");
  const pid = meta && meta.dataset.personId;
  if (!pid) return;

  const SPORTS = [["XC", "xc_rating"], ["TF", "tf_rating"]];

  function caption(f) {
    const r = (v) => Number(v).toFixed(1);
    const range = (f.p10 != null && f.p90 != null)
      ? `8 in 10 between ${r(f.p10)} and ${r(f.p90)}`
      : `the middle half between ${r(f.p25)} and ${r(f.p75)}`;
    return `Runners like you averaged ${r(f.mean)} the next season (${range}; ` +
      `${f.n} comparable runner${f.n === 1 ? "" : "s"}).`;
  }

  function attach(sport, key) {
    const host = document.querySelector(`[data-chart="${key}"]`);
    if (!host) return;
    fetch(`/api/runners-like-you/${encodeURIComponent(pid)}?sport=${sport}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((d) => {
        if (!d || !d.available || !d.fan || !window.rcCharts) return;
        const fan = Object.assign({}, d.fan, { label: caption(d.fan) });
        if (!window.rcCharts.setFan(key, fan)) return;
        const cap = document.createElement("p");
        cap.className = "chart-cap";
        cap.innerHTML = caption(d.fan).replace(/^Runners like you/,
          `<a href="/recruit/${encodeURIComponent(pid)}#like">Runners like you</a>`) +
          ' <span class="chart-cap-key"><i class="k-in"></i>middle half' +
          (d.fan.p10 != null ? '<i class="k-out"></i>8 in 10' : "") + "</span>";
        host.insertAdjacentElement("afterend", cap);
      })
      .catch(() => {});
  }

  function go() { SPORTS.forEach(([s, k]) => attach(s, k)); }
  if (window.rcCharts) go();
  else document.addEventListener("rc-charts-ready", go, { once: true });
})();
