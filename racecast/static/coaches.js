/* coaches.js -- the only per-reader part of /coaches (283).
 *
 * ★ WHY THIS IS NOT SERVER-RENDERED. /coaches is edge-cacheable like every
 *   other page, so the HTML must be identical for every reader. The teams
 *   someone coaches come from /api/me AFTER load, exactly as the topbar's
 *   account chip does -- and this file does not fetch anything itself, it
 *   listens for the `xcp:me` event topbar-search.js already dispatches.
 *   One request, not two.
 *
 * ! IT MAY HAVE ALREADY FIRED. topbar-search.js runs on DOMContentLoaded
 *   and this script is at the end of <body>, so the order is not
 *   guaranteed. window.xcpMe is checked first and the listener is the
 *   fallback; doing only one of the two loses the block on a fast cache
 *   hit or on a slow /api/me, depending which you pick.
 */
(function () {
  'use strict';

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  /* The site's own school URL shape, so a claimed team links to its page.
     ⚠ SLASHES SURVIVE. school_identity.schoolHref quotes with safe="/" and
     the route is a <path:> converter -- "Chisago Lakes/Rush City" is one
     real school name, and %2F would 404 it. */
  function schoolHref(school, state) {
    var u = '/school/' + encodeURIComponent(school).replace(/%2F/g, '/');
    return state ? u + '?state=' + encodeURIComponent(state) : u;
  }

  function render(me) {
    var note = document.getElementById('coach-mine-note');
    var list = document.getElementById('coach-team-list');
    var block = document.getElementById('coach-mine');
    if (!note || !list || !block) return;

    if (!me || !me.signed_in) return;                 /* the signed-out copy stands */

    if (me.admin) block.classList.add('is-admin');

    var teams = me.teams || [];
    var selves = me.athletes || [];

    if (!teams.length && !selves.length) {
      note.innerHTML = 'Add the team you coach in <a href="/account">settings</a> and it will sit here.' +
        (me.admin ? ' <span class="coach-admin-tag">admin</span>' : '');
      return;
    }

    var rows = teams.map(function (t) {
      var label = t.label || t.school || 'Team';
      return '<li><a href="' + esc(schoolHref(t.school, t.state)) + '">' + esc(label) + '</a>' +
        '<span class="dist-note">' + esc(t.level_label || t.level || '') +
        (t.status === 'verified' ? ' · verified' : ' · not yet verified') + '</span></li>';
    });

    rows = rows.concat(selves.map(function (a) {
      return '<li><a href="/athlete/' + encodeURIComponent(a.person_id) + '">' +
        esc(a.name || ('Athlete ' + a.person_id)) + '</a>' +
        '<span class="dist-note">your athlete page</span></li>';
    }));

    list.innerHTML = rows.join('');
    list.hidden = false;
    note.innerHTML = 'Manage these in <a href="/account">settings</a>.' +
      (me.admin ? ' <span class="coach-admin-tag">admin</span>' : '');
  }

  if (window.xcpMe) render(window.xcpMe);
  else document.addEventListener('xcp:me', function (e) { render(e.detail); });
})();
