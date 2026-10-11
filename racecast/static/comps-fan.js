/*
 * comps-fan.js -- "runners like you" on the athlete page's rating chart
 * (comps.py nextSeasonFan, owner 2026-10-10).
 *
 *   Next season: about 129.4, most likely between 124.1 and 134.0. From 86
 *   past runners who had a similar rating and trend at this point (see
 *   them): 8 in 10 of them landed in that range, shown light; the darker
 *   band is the middle half.
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

  // ★ PLAIN WORDS, THE NUMBER FIRST (owner, 2026-10-11: the old caption
  //   "looks wrong / hard to understand"): what to expect next season, the
  //   likely range, and only then where it comes from. The chart's own
  //   label (the tooltip) keeps the short form.
  function caption(f) {
    const r = (v) => Number(v).toFixed(1);
    return `Next season: about ${r(f.mean)}.`;
  }
  function explain(f) {
    const r = (v) => Number(v).toFixed(1);
    const wide = f.p10 != null && f.p90 != null;
    const lo = wide ? f.p10 : f.p25, hi = wide ? f.p90 : f.p75;
    const share = wide ? "8 in 10" : "half";
    return `<b>Next season: about ${r(f.mean)}</b>, most likely between ` +
      `${r(lo)} and ${r(hi)}. ` +
      `<span class="chart-cap-why">From ${f.n} past runner${f.n === 1 ? "" : "s"} who had a ` +
      `similar rating and trend at this point (<a href="/recruit/${encodeURIComponent(pid)}#like">` +
      `see them</a>): ${share} of them landed in that range` +
      (wide ? ", shown light; the darker band is the middle half." : ", the shaded band.") +
      `</span>`;
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
        cap.innerHTML = explain(d.fan);
        host.insertAdjacentElement("afterend", cap);
      })
      .catch(() => {});
  }

  function go() { SPORTS.forEach(([s, k]) => attach(s, k)); }
  if (window.rcCharts) go();
  else document.addEventListener("rc-charts-ready", go, { once: true });
})();
