/* follow.js -- the Follow button on athlete and school pages and the Save
   button on a college's recruiting page (owner, 2026-10-10).

   ★ THE PAGE STAYS EDGE-CACHEABLE. The button is drawn here, after load, from
     /api/follow/state or /api/shortlist/state (no-store) -- the way the
     topbar asks /api/me. With no rc_si hint cookie the reader is signed out
     and no request is made at all: the button is a link that signs them in
     and brings them back (/account/return).
   ! ONE SCRIPT TAG CONFIGURES IT, so a template's only hook is that tag:
       <script src="follow.js" data-follow="athlete" data-person-id="7"
               data-mount=".rc-hd [data-person-id]"></script>
       data-follow="team"  data-school data-state data-level
       data-shortlist="1"  data-school data-state
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
  var CHECK = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M9.5 16.2 5.3 12l-1.4 1.4 5.6 5.6L20.1 8.4 18.7 7z"/></svg>';

  var shortlist = cfg.shortlist === '1';
  var subject = shortlist ? { school: cfg.school, state: cfg.state }
    : (cfg.follow === 'athlete' ? { kind: 'athlete', person_id: cfg.personId }
       : { kind: 'team', school: cfg.school, state: cfg.state, level: cfg.level });
  var words = shortlist ? { off: 'Save to shortlist', on: 'On your shortlist' } : { off: 'Follow', on: 'Following' };

  /* ! THE BUTTON'S RULES TRAVEL WITH IT: the pages it sits on load only
       style.css and race.css, and a hook of one script tag is the point.
       The site's tokens, the topbar chips' pill shape. Once per page. */
  function style() {
    if (document.getElementById('rc-follow-css')) return;
    var css = document.createElement('style');
    css.id = 'rc-follow-css';
    css.textContent =
      'main.rc .rc-follow-wrap{white-space:nowrap}' +
      'main.rc .rc-follow{display:inline-flex;align-items:center;gap:.3rem;padding:.12rem .6rem;margin:0;font:inherit;' +
      'font-size:.88rem;font-weight:600;line-height:1.5;color:var(--ink);background:#fff;border:1px solid var(--line-2);' +
      'border-radius:999px;cursor:pointer;white-space:nowrap;text-decoration:none;vertical-align:baseline}' +
      'main.rc .rc-follow:hover{background:var(--hover);text-decoration:none}' +
      'main.rc .rc-follow.is-on{background:var(--sel);color:#fff;border-color:var(--sel)}' +
      'main.rc .rc-follow.is-on:hover{background:#000}' +
      'main.rc .rc-follow[disabled]{opacity:.6;cursor:progress}' +
      'main.rc .rc-follow svg{flex:none}' +
      'main.rc .rc-follow-note{font-size:.8rem;font-weight:400;color:var(--muted);margin-left:.35rem;white-space:nowrap}' +
      'main.rc .rc-follow-note a{font-weight:600}';
    document.head.appendChild(css);
  }

  function mount() {
    style();
    var host = document.querySelector(cfg.mount || '.rc-hd .rc-links') || document.querySelector('.rc-hd .rc-left');
    if (!host) return null;
    var wrap = document.createElement('span');
    wrap.className = 'rc-follow-wrap';
    if (cfg.sep) wrap.appendChild(document.createTextNode(cfg.sep));
    host.appendChild(wrap);
    return wrap;
  }

  function drawSignedOut(wrap) {
    var back = location.pathname + location.search;
    wrap.innerHTML = (cfg.sep ? esc(cfg.sep) : '') + '<a class="rc-follow" href="/account/return?' + enc({ next: back }) +
      '" title="Sign in to ' + (shortlist ? 'save programs' : 'follow and get email alerts') + '">' + PLUS + '<span>' + esc(words.off) + '</span></a>';
  }

  function draw(wrap, st) {
    var on = shortlist ? st.saved : st.following;
    var note = '';
    if (on && shortlist) note = '<span class="rc-follow-note">' + esc(st.count) + ' of ' + esc(st.max) + ' · <a href="/account/shortlist">Compare</a></span>';
    else if (on) note = '<span class="rc-follow-note">' + (st.cadence === 'off' ? 'emails off' : 'emails on') + ' · <a href="/account/me">My page</a></span>';
    if (st.message) note = '<span class="rc-follow-note">' + esc(st.message) + '</span>';
    wrap.innerHTML = (cfg.sep ? esc(cfg.sep) : '') + '<button type="button" class="rc-follow' + (on ? ' is-on' : '') + '" aria-pressed="' + (on ? 'true' : 'false') + '">' +
      (on ? CHECK : PLUS) + '<span>' + esc(on ? words.on : words.off) + '</span></button>' + note;
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
