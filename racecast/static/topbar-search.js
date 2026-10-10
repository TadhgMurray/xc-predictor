// topbar-search.js -- the live search dropdown. Loaded on every page, from
// <head> with `defer`: it runs after the document is parsed, and every init
// below still checks readyState so it would work loaded any other way.

/* ★ ONE ESCAPE FOR TEXT AND ATTRIBUTES (sweep 2026-10-10, D11). The old
     textContent -> innerHTML trick escapes & < > but NOT quotes, so a value
     put inside href="..." or title="..." could close the attribute. */
function escAttr(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

(function () {
    /* ★ ONE DROPDOWN, ANY NUMBER OF BOXES (2026-10-10). The home hero's big
         search is this same box: same endpoint, same rows, same keys. Any
         input with data-live-search="<id of its results div>" is wired by
         this one function rather than by a copy of it. */
    function init() {
        wire(document.getElementById('search-input'),
             document.getElementById('search-results'));
        var more = document.querySelectorAll('input[data-live-search]');
        for (var i = 0; i < more.length; i++) {
            wire(more[i], document.getElementById(more[i].getAttribute('data-live-search')));
        }
    }

    function wire(input, box) {
        if (!input || !box) return;

        var timer = null, seq = 0, closed = false;
        /* Index of the keyboard-highlighted row, -1 for none. Reset whenever
           the list re-renders: the old index would point at a row that may
           no longer exist, or worse, a different one. */
        var active = -1;

        /* Every keyboard-reachable row, in visual order. The "See all
           results" link is included: it is the last stop of an arrow-down
           walk, same as it is the last thing the eye reaches. */
        function options() {
            return box.querySelectorAll('.sr-item, .sr-more');
        }

        function setActive(i) {
            var opts = options();
            if (!opts.length) { active = -1; return; }
            /* Wrap at both ends -- arrow-down from the last row returns to
               the first, which beats a dead stop nobody can see the reason
               for. */
            active = ((i % opts.length) + opts.length) % opts.length;
            for (var k = 0; k < opts.length; k++) {
                opts[k].classList.toggle('is-active', k === active);
            }
            /* The list scrolls (.sr-scroll); the highlight must not walk out
               of view. 'nearest' only scrolls when needed, so mouse users
               see no jump. */
            opts[active].scrollIntoView({ block: 'nearest' });
        }

        input.addEventListener('input', function () {
            var q = input.value.trim();
            clearTimeout(timer);
            if (q.length < 2) { box.innerHTML = ''; active = -1; return; }
            closed = false;             // typing re-opens what a click shut
            var mySeq = ++seq;
            // ★ SAY IT IS WORKING (sweep, 2026-09-26): the box stayed blank
            //   until the answer came, and a slow answer read as none
            if (!box.querySelector('.sr-item')) {
                box.innerHTML = '<div class="sr-state">Searching\u2026</div>';
            }
            timer = setTimeout(function () {
                fetch('/search/api?q=' + encodeURIComponent(q))
                    .then(function (r) {
                        if (!r.ok) throw new Error(r.status);
                        return r.json();
                    })
                    .then(function (rows) {
                        if (mySeq !== seq || closed) return;
                        render(rows, q);
                    })
                    .catch(function () {
                        if (mySeq !== seq || closed) return;
                        active = -1;
                        box.innerHTML = '<div class="sr-state">Search is not ' +
                            'answering right now. Try again in a moment.</div>';
                    });
            }, 150);
        });

        function render(rows, q) {
            active = -1;
            var more = '<a class="sr-more" href="/search?q=' +
                    encodeURIComponent(q) + '">See all results →</a>';
            if (!rows.length) {
                // ★ AND SAY WHEN NOTHING MATCHED, rather than showing only
                //   a link to a results page that will say the same
                box.innerHTML = '<div class="sr-state">No athletes, teams, ' +
                    'meets or courses match \u201c' + esc(q) + '\u201d.</div>' + more;
                return;
            }
            var items = rows.map(function (r) {
                var sub = r.sublabel ? '<span class="sr-sub">' + esc(r.sublabel) + '</span>' : '';
                return '<a class="sr-item" href="' + esc(r.link || '') + '">' +
                    '<span class="sr-kind">' + esc(r.kind || '') + '</span>' +
                    '<span class="sr-label">' + esc(r.label) + '</span>' + sub + '</a>';
            }).join('');
            box.innerHTML = '<div class="sr-scroll">' + items + '</div>' + more;
        }

        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                if (!options().length) return;
                /* Without this the caret jumps to the end/start of the input
                   on every press, which reads as the page fighting you. */
                e.preventDefault();
                setActive(active + (e.key === 'ArrowDown' ? 1 : -1));
            } else if (e.key === 'Enter') {
                var opts = options();
                if (active >= 0 && opts[active]) {
                    /* A highlighted row wins: rows are <a>, so following the
                       highlight is just following its href. */
                    e.preventDefault();
                    window.location = opts[active].href;
                } else {
                    /* No highlight -> the full results page, as before. */
                    var q = input.value.trim();
                    if (q) window.location = '/search?q=' + encodeURIComponent(q);
                }
            } else if (e.key === 'Escape') {
                closed = true;              // and stay shut
                box.innerHTML = '';
                active = -1;
            }
        });

        /* ⚠ CLOSING IS NOT ENOUGH ON ITS OWN. Clicking away emptied the box,
             and then a response still in flight painted it again seconds
             later -- over the page, with nothing left to click away from.
             seq answered "is this superseded"; nothing answered "does anyone
             still want this". */
        document.addEventListener('click', function (e) {
            if (!input.contains(e.target) && !box.contains(e.target)) {
                closed = true;
                box.innerHTML = '';
            }
        });

        function esc(s) { return escAttr(s); }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

/* ---- the account (283) ------------------------------------------------
   Every page is served signed-out and cached; this asks /api/me (never
   cached) and swaps the topbar link, marks an athlete page that is yours,
   and hands the answer to any page script listening (xcp:me). */
(function () {
  /* ! AFTER THE DOM, LIKE init() ABOVE: deferred, so the DOM is parsed by
       the time this runs, but the readyState check keeps it safe either way. */
  function whoami() {
    var slot = document.getElementById('topbar-account');
    if (!slot) return;
    var esc = escAttr;
    /* ★ NOT A REQUEST ON EVERY PAGE FOR EVERYONE (sweep 2026-10-10, D15).
         rc_si is the server's hint (accounts.setHint): 1 signed in, 0 known
         signed out. On 0 the answer is already known; absent (a first
         visit, a session from before the hint) still asks, and that
         answer sets it. Listeners read window.xcpMe first, so a
         synchronous answer reaches them too. */
    if (/(?:^|;\s*)rc_si=0(?:;|$)/.test(document.cookie)) {
        window.xcpMe = { signed_in: false };
        document.dispatchEvent(new CustomEvent('xcp:me', { detail: window.xcpMe }));
        return;
    }
    fetch('/api/me', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (me) {
            window.xcpMe = me || { signed_in: false };
            if (me && me.signed_in) {
                /* the picture, or the first letter of the name, as a round
                   button: it reads as "you", not as one more menu item */
                var initial = (me.label || '?').trim().charAt(0).toUpperCase();
                slot.innerHTML = '<a href="/account" class="tb-me' + (me.photo ? ' has-photo' : '') + '" title="' +
                    esc(me.label) + ' · settings">' +
                    (me.photo ? '<img src="' + esc(me.photo) + '" alt="" width="34" height="34">' : esc(initial)) + '</a>';
                var mine = document.querySelector('[data-person-id]');
                if (mine && (me.athletes || []).some(function (a) { return String(a.person_id) === mine.dataset.personId; })) {
                    mine.insertAdjacentHTML('beforeend', ' · <a href="/account" class="is-account">Your page</a>');
                    var av = document.getElementById('ath-avatar');
                    if (av && av.classList.contains('ath-avatar-empty') && !me.photo) {
                        av.innerHTML = '<a href="/account#photo" class="ath-avatar-add" title="Add your picture">+<span>photo</span></a>';
                        av.hidden = false;
                    }
                }
            }
            document.dispatchEvent(new CustomEvent('xcp:me', { detail: window.xcpMe }));
        })
        /* ! STILL DISPATCH ON FAILURE. Pages that wait for xcp:me (coaches.js)
             would hang forever on a network error otherwise, leaving their
             signed-in block empty rather than falling back to signed-out. */
        .catch(function () {
            window.xcpMe = { signed_in: false };
            document.dispatchEvent(new CustomEvent('xcp:me', { detail: window.xcpMe }));
        });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', whoami);
  else whoami();
})();

/* ★ SIGN IN COMES BACK HERE (sweep 2026-10-10, B17). Runs on the cached,
     signed-out HTML, in the browser only: the link carries ?next=<this
     page>, so signing in from the bar lands where you were, not on Settings.
   ! THE EDITION MEMORY THAT USED TO SHARE THIS BLOCK IS GONE (2026-10-10):
     the bar is one nav for everyone now (_topbar.html), so there is no
     second link set to re-apply and nothing kept in sessionStorage. */
(function () {
  function init() {
    var path = location.pathname;
    var signin = document.querySelector('#topbar-account .tb-signin');
    if (signin && path.indexOf('/login') !== 0) {
      /* read when used, not only at load: rankings and recruiting rewrite
         their query string as filters change */
      var aim = function () {
        signin.setAttribute('href', '/login?next=' +
            encodeURIComponent(location.pathname + location.search));
      };
      aim();
      signin.addEventListener('mousedown', aim);
      signin.addEventListener('focus', aim);
      signin.addEventListener('click', aim);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

/* ★ THE TOPBAR'S TOOLS MENU CLOSES LIKE A MENU (ui pass, 2026-10-04; was
   "More" until 2026-10-10). It is a <details>, so it opens and works with no
   script at all; this only shuts it on a click anywhere else, or Escape,
   instead of leaving it hanging open over the page.
   ★ AND THE PHONE MENU BUTTON (2026-10-10). Under 700px the sections fold
   behind .nav-toggle; it flips aria-expanded and .nav-open on the bar (the
   CSS shows the panel), and Escape or a click outside the bar closes it,
   handing focus back to the button when the focus was inside the panel. */
(function () {
  function shut(except) {
    var open = document.querySelectorAll('.topnav-more[open]');
    for (var i = 0; i < open.length; i++) if (open[i] !== except) open[i].removeAttribute('open');
  }
  function setMenu(btn, on) {
    var bar = btn.closest('.topbar');
    btn.setAttribute('aria-expanded', on ? 'true' : 'false');
    if (bar) bar.classList.toggle('nav-open', on);
    /* the panel lists Tools' items, not a dropdown inside a dropdown */
    var tools = bar && bar.querySelector('.topnav-tools');
    if (tools) { if (on) tools.setAttribute('open', ''); else tools.removeAttribute('open'); }
  }
  function toggles() { return document.querySelectorAll('.topbar .nav-toggle'); }
  document.addEventListener('click', function (e) {
    var t = e.target;
    shut(t.closest ? t.closest('.topnav-more') : null);
    var btn = t.closest ? t.closest('.nav-toggle') : null;
    if (btn) { setMenu(btn, btn.getAttribute('aria-expanded') !== 'true'); return; }
    var all = toggles();
    for (var i = 0; i < all.length; i++) {
      var bar = all[i].closest('.topbar');
      if (bar && !bar.contains(t)) setMenu(all[i], false);
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    /* the phone panel first: it closes whole, Tools with it, and the
       focus goes back to its button if it was inside */
    var all = toggles(), closed = false;
    for (var i = 0; i < all.length; i++) {
      if (all[i].getAttribute('aria-expanded') !== 'true') continue;
      var nav = document.getElementById(all[i].getAttribute('aria-controls'));
      var inside = nav && nav.contains(document.activeElement);
      setMenu(all[i], false);
      if (inside) all[i].focus();
      closed = true;
    }
    if (closed) return;
    // then the desktop Tools menu, focus back on its summary
    var tools = document.querySelector('.topnav-more[open]');
    if (tools) {
      var hadFocus = tools.contains(document.activeElement);
      shut(null);
      var sum = tools.querySelector('summary');
      if (hadFocus && sum) sum.focus();
    }
  });
})();
