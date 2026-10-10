/* follow.js -- the Follow button on athlete and school pages and the Save
   button on a college's recruiting page (owner, 2026-10-10).

   ★ THE PAGE STAYS EDGE-CACHEABLE. The button is drawn here, after load, from
     /api/follow/state or /api/shortlist/state (no-store) -- the way the
     topbar asks /api/me. With no rc_si hint cookie the reader is signed out
     and no request is made at all: the button is a link that signs them in
     and brings them back (/account/return).
   ★ FOLLOW SITS BESIDE THE NAME, NOT IN THE LINK ROW (owner, 2026-10-10:
     wrapping onto its own line in the meta row, and "hard to understand").
     The h1 and the button share one row, the button right-aligned; on a
     phone it drops under the name as a pill. It says what it does: a bell,
     "Follow", and on a first view one line under it -- "Get an email when
     Owen races, sets a PR or breaks out." Following, it reads "Following ✓"
     with the email setting one tap away.
   ! ONE SCRIPT TAG CONFIGURES IT, so a template's only hook is that tag:
       <script src="follow.js" data-follow="athlete" data-person-id="7"
               data-first="Owen"></script>
       data-follow="team"  data-school data-state data-level data-name
       data-shortlist="1"  data-school data-state data-mount data-sep
   ! EVERY CHANGE IS A POST WITH THE SESSION'S CSRF TOKEN (X-CSRF), same
     origin; the server checks both (accounts.csrfOk). */
(function () {
  var me = document.currentScript;
  if (!me) return;
  var cfg = me.dataset;

  function signedIn() { return /(?:^|;\s*)rc_si=1(?:;|$)/.test(document.cookie); }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function enc(o) {
    return Object.keys(o).filter(function (k) { return o[k] != null && o[k] !== ''; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&');
  }
  var PLUS = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z"/></svg>';
  var CHECK = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M9.5 16.2 5.3 12l-1.4 1.4 5.6 5.6L20.1 8.4 18.7 7z"/></svg>';
  var BELL = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22zm7-6V11a7 7 0 0 0-5.5-6.84V3.5a1.5 1.5 0 0 0-3 0v.66A7 7 0 0 0 5 11v5l-2 2v1h18v-1z"/></svg>';

  var shortlist = cfg.shortlist === '1';
  var subject = shortlist ? { school: cfg.school, state: cfg.state }
    : (cfg.follow === 'athlete' ? { kind: 'athlete', person_id: cfg.personId }
       : { kind: 'team', school: cfg.school, state: cfg.state, level: cfg.level });
  // the setting's words as the button shows them (follows.CADENCES)
  var CADENCE = { daily: 'after each meet', weekly: 'weekly', off: 'off' };
  var helper = shortlist ? '' : (cfg.follow === 'athlete'
    ? 'Get an email when ' + (cfg.first || 'they') + ' races, sets a PR or breaks out.'
    : 'Get an email after each ' + (cfg.name || cfg.school || 'team') + ' meet.');

  /* ! THE BUTTON'S RULES TRAVEL WITH IT: the pages it sits on load only
       style.css and race.css, and a hook of one script tag is the point.
       The site's tokens, the topbar chips' pill shape. Once per page. */
  function style() {
    if (document.getElementById('rc-follow-css')) return;
    var css = document.createElement('style');
    css.id = 'rc-follow-css';
    css.textContent =
      'main.rc .rc-namerow{display:flex;align-items:flex-start;justify-content:space-between;gap:.4rem 1.5rem}' +
      'main.rc .rc-namerow>h1{min-width:0}' +
      'main.rc .rc-follow-box{flex:none;display:flex;flex-direction:column;align-items:flex-end;text-align:right;' +
      'margin-top:.35rem;max-width:17rem}' +
      'main.rc .rc-follow-wrap{white-space:nowrap}' +
      'main.rc .rc-follow{display:inline-flex;align-items:center;gap:.35rem;padding:.3rem .85rem;margin:0;font:inherit;' +
      'font-size:.92rem;font-weight:600;line-height:1.4;color:var(--ink);background:#fff;border:1px solid var(--line-2);' +
      'border-radius:999px;cursor:pointer;white-space:nowrap;text-decoration:none;vertical-align:baseline}' +
      'main.rc .rc-follow:hover{background:var(--hover);text-decoration:none}' +
      'main.rc .rc-follow.is-on{background:var(--sel);color:#fff;border-color:var(--sel)}' +
      'main.rc .rc-follow.is-on:hover{background:#000}' +
      'main.rc .rc-follow[disabled]{opacity:.6;cursor:progress}' +
      'main.rc .rc-follow svg{flex:none}' +
      'main.rc .rc-follow-help{font-size:.8rem;line-height:1.35;color:var(--muted);margin:.35rem 0 0}' +
      'main.rc .rc-follow-help a{font-weight:600}' +
      'main.rc .rc-follow-note{font-size:.8rem;font-weight:400;color:var(--muted);margin-left:.35rem;white-space:nowrap}' +
      'main.rc .rc-follow-note a{font-weight:600}' +
      '@media (max-width:760px){main.rc .rc-namerow{flex-direction:column}' +
      'main.rc .rc-follow-box{align-items:flex-start;text-align:left;margin:0 0 .4rem;max-width:none}}';
    document.head.appendChild(css);
  }

  // ! "first view": the helper line shows until the reader has seen it once
  //   (or follows); a per-browser convenience, so storage may fail quietly
  var KEY = 'rc_follow_help_seen';
  function helpSeen() { try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; } }
  function markSeen() { try { localStorage.setItem(KEY, '1'); } catch (e) { /* private mode */ } }

  function mount() {
    style();
    if (!shortlist) {
      var h1 = document.querySelector('.rc-hd h1');
      if (!h1) return null;
      var row = document.createElement('div');
      row.className = 'rc-namerow';
      h1.parentNode.insertBefore(row, h1);
      row.appendChild(h1);
      var box = document.createElement('div');
      box.className = 'rc-follow-box';
      row.appendChild(box);
      return box;
    }
    var host = document.querySelector(cfg.mount || '.rc-hd .rc-links') || document.querySelector('.rc-hd .rc-left');
    if (!host) return null;
    var wrap = document.createElement('span');
    wrap.className = 'rc-follow-wrap';
    host.appendChild(wrap);
    return wrap;
  }

  function helpLine(first) {
    return first ? '<p class="rc-follow-help">' + esc(helper) + '</p>' : '';
  }

  function drawSignedOut(wrap) {
    var back = location.pathname + location.search;
    var label = shortlist ? 'Save to shortlist' : 'Follow';
    var showHelp = !shortlist && !helpSeen();
    wrap.innerHTML = (shortlist && cfg.sep ? esc(cfg.sep) : '') + '<a class="rc-follow" href="/account/return?' + enc({ next: back }) +
      '" title="' + esc(shortlist ? 'Sign in to save programs' : helper + ' Sign in to follow.') + '">' +
      (shortlist ? PLUS : BELL) + '<span>' + label + '</span></a>' + helpLine(showHelp);
    if (showHelp) markSeen();
  }

  function draw(wrap, st) {
    var on = shortlist ? st.saved : st.following;
    var html, note = '';
    if (shortlist) {
      if (on) note = '<span class="rc-follow-note">' + esc(st.count) + ' of ' + esc(st.max) + ' · <a href="/account/shortlist">Compare</a></span>';
      if (st.message) note = '<span class="rc-follow-note">' + esc(st.message) + '</span>';
      html = (cfg.sep ? esc(cfg.sep) : '') + '<button type="button" class="rc-follow' + (on ? ' is-on' : '') +
        '" aria-pressed="' + (on ? 'true' : 'false') + '">' + (on ? CHECK : PLUS) + '<span>' +
        (on ? 'On your shortlist' : 'Save to shortlist') + '</span></button>' + note;
    } else {
      var showHelp = !on && !helpSeen();
      html = '<button type="button" class="rc-follow' + (on ? ' is-on' : '') + '" aria-pressed="' + (on ? 'true' : 'false') +
        '" title="' + esc(on ? 'Following. Click to stop.' : helper) + '">' +
        (on ? '<span>Following</span>' + CHECK : BELL + '<span>Follow</span>') + '</button>';
      if (st.message) html += '<p class="rc-follow-help">' + esc(st.message) + '</p>';
      else if (on) html += '<p class="rc-follow-help">Email me: <a href="/account/me#alerts">' +
        esc(CADENCE[st.cadence] || CADENCE.daily) + '</a></p>';
      else html += helpLine(showHelp);
      if (showHelp) markSeen();
    }
    wrap.innerHTML = html;
    var btn = wrap.querySelector('button');
    btn.addEventListener('click', function () {
      btn.disabled = true;
      var body = Object.assign({}, subject);
      body.action = shortlist ? (on ? 'remove' : 'save') : (on ? 'unfollow' : 'follow');
      fetch(shortlist ? '/api/shortlist' : '/api/follow', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF': st.csrf },
        body: enc(body)
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          var next = Object.assign({}, st, { message: null });
          if (!res.ok) { next.message = res.j.error || 'That did not work. Try again.'; }
          else if (shortlist) { next.saved = !!res.j.saved; next.count = res.j.count; }
          else { next.following = !!res.j.following; }
          draw(wrap, next);
        })
        .catch(function () { draw(wrap, Object.assign({}, st, { message: 'That did not work. Try again.' })); });
    });
  }

  function init() {
    var wrap = mount();
    if (!wrap) return;
    if (!signedIn()) { drawSignedOut(wrap); return; }
    fetch((shortlist ? '/api/shortlist/state?' : '/api/follow/state?') + enc(subject), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (st) {
        if (!st.signed_in) { drawSignedOut(wrap); return; }
        if (!st.ready || st.error) { wrap.remove(); return; }
        draw(wrap, st);
      })
      .catch(function () { wrap.remove(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
