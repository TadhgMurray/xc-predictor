// training.js -- the training page's uploader (training_log.py, owner
// 2026-11-08). Files go to /account/training/upload in batches that stay
// under the app's 9 MB request cap, so a whole export folder (thousands of
// runs) uploads from one pick. Only .fit, .gpx, .tcx and their .gz are sent;
// the server reads each one and keeps only its summary.
(function () {
  "use strict";
  var form = document.getElementById("tr-form");
  if (!form) return;
  var files = document.getElementById("tr-files");
  var folder = document.getElementById("tr-folder");
  var status = document.getElementById("tr-status");
  var go = document.getElementById("tr-go");
  var maxBytes = (parseInt(form.dataset.maxMb, 10) || 8) * 1024 * 1024;
  var BATCH_BYTES = 7 * 1024 * 1024;   // under the 9 MB cap, with room for the form
  var BATCH_FILES = 40;
  var WANT = /\.(fit|gpx|tcx)(\.gz)?$/i;

  function picked() {
    var out = [];
    [files, folder].forEach(function (inp) {
      if (!inp || !inp.files) return;
      for (var i = 0; i < inp.files.length; i++) {
        if (WANT.test(inp.files[i].name)) out.push(inp.files[i]);
      }
    });
    return out;
  }

  function batches(list) {
    var out = [], cur = [], size = 0;
    list.forEach(function (f) {
      if (cur.length && (size + f.size > BATCH_BYTES || cur.length >= BATCH_FILES)) {
        out.push(cur); cur = []; size = 0;
      }
      cur.push(f); size += f.size;
    });
    if (cur.length) out.push(cur);
    return out;
  }

  function say(text) { status.textContent = text; }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var list = picked();
    var big = list.filter(function (f) { return f.size > maxBytes; });
    list = list.filter(function (f) { return f.size <= maxBytes; });
    if (!list.length) { say("Pick FIT, GPX or TCX files, or the activities folder of an export."); return; }
    var todo = batches(list), done = 0, added = 0, dup = 0, skipped = big.length, failed = 0;
    go.disabled = true;
    function next() {
      if (!todo.length) {
        say("Done: " + added + " runs added" + (dup ? ", " + dup + " already here" : "") +
            (skipped ? ", " + skipped + " files skipped (not a run, or unreadable)" : "") +
            (failed ? ", " + failed + " batches failed: try those again" : "") + ".");
        go.disabled = false;
        if (added) setTimeout(function () { location.reload(); }, 1500);
        return;
      }
      var b = todo.shift(), fd = new FormData();
      fd.append("csrf", form.dataset.csrf);
      fd.append("person_id", form.dataset.person);
      b.forEach(function (f) { fd.append("files", f, f.name); });
      fetch(form.action, { method: "POST", body: fd, credentials: "same-origin",
                           headers: { "X-CSRF": form.dataset.csrf } })
        .then(function (r) { return r.json().catch(function () { return { ok: false }; }); })
        .then(function (j) {
          if (j && j.ok) { added += j.added; dup += j.duplicates; skipped += (j.skipped || []).length; }
          else { failed += 1; if (j && j.error) say(j.error); }
        })
        .catch(function () { failed += 1; })
        .then(function () {
          done += b.length;
          say("Uploading… " + done + " of " + list.length + " files, " + added + " runs added");
          next();
        });
    }
    say("Uploading… 0 of " + list.length + " files");
    next();
  });
})();
