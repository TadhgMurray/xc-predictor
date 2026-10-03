/* account.js -- the pickers on the account page (283): the site's own
   typeahead, athletes or schools, writing the chosen id or name into the
   form's hidden fields. The role radios submit themselves. */
(function () {
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  document.querySelectorAll(".acct-pick").forEach((form) => {
    const q = form.querySelector(".acct-q"), list = form.querySelector(".r-combo-list");
    const go = form.querySelector("button[type=submit]");
    const kind = q.dataset.searchKind;
    let seq = 0, timer = null, ctl = null;
    // ★ A SEARCH SAYS WHAT IT IS DOING (2026-10-03): "Searching...", then
    //   the names, "No match", or "Search did not answer" -- never a
    //   silent box, which on a slow phone connection read as a hang. The
    //   request is cut off at 8s and an older one is aborted by a newer.
    function note(text) {
      list.innerHTML = `<div class="r-combo-note">${esc(text)}</div>`;
      list.hidden = false;
    }
    function clear() {
      form.querySelectorAll("input[type=hidden]").forEach((h) => { if (h.name !== "csrf" && h.name !== "kind") h.value = ""; });
      go.disabled = true;
    }
    q.addEventListener("input", () => {
      clear();
      const text = q.value.trim();
      clearTimeout(timer);
      if (text.length < 2) { list.hidden = true; return; }
      const mine = ++seq;
      timer = setTimeout(async () => {
        if (ctl) ctl.abort();
        ctl = window.AbortController ? new AbortController() : null;
        const cut = ctl && setTimeout(() => ctl.abort(), 8000);
        note("Searching…");
        try {
          const res = await fetch("/search/api?kind=" + kind + "&q=" + encodeURIComponent(text),
                                  ctl ? { signal: ctl.signal } : {});
          if (!res.ok) throw new Error("HTTP " + res.status);
          const rows = await res.json();
          if (mine !== seq) return;
          list.innerHTML = rows.slice(0, 8).map((r) => {
            if (kind === "athlete") {
              const m = /\/athlete\/(\d+)/.exec(r.link || "");
              if (!m) return "";
              return `<button type="button" class="r-combo-item" data-id="${m[1]}" data-label="${esc(r.label)}">${esc(r.label)}${r.sublabel ? ` <span class="sr-sub">${esc(r.sublabel)}</span>` : ""}</button>`;
            }
            const st = /\(([A-Z]{2})\)\s*$/.exec(r.label || "");
            return `<button type="button" class="r-combo-item" data-school="${esc(r.value || r.label)}" data-state="${st ? st[1] : ""}" data-label="${esc(r.label)}">${esc(r.label)}${r.sublabel ? ` <span class="sr-sub">${esc(r.sublabel)}</span>` : ""}</button>`;
          }).join("");
          if (!list.innerHTML) note(kind === "athlete" ? "No match. Try the name as it appears in results."
                                                      : "No match. Try the school's name without \"High School\".");
          else list.hidden = false;
        } catch (e) {
          if (mine === seq) note("Search did not answer. Type again to retry.");
        } finally {
          if (cut) clearTimeout(cut);
        }
      }, 150);
    });
    list.addEventListener("click", (e) => {
      const b = e.target.closest(".r-combo-item");
      if (!b) return;
      if (kind === "athlete") form.querySelector("input[name=person_id]").value = b.dataset.id;
      else {
        form.querySelector("input[name=school]").value = b.dataset.school;
        form.querySelector("input[name=state]").value = b.dataset.state;
      }
      q.value = b.dataset.label;
      list.hidden = true;
      go.disabled = false;
    });
    document.addEventListener("click", (e) => { if (!form.contains(e.target)) list.hidden = true; });
  });
})();

/* the picture (305): choosing a file submits it; nothing else to click.
 *
 * ★ SHRUNK ON THE PHONE BEFORE IT IS SENT (2026-10-03). A phone photo is
 *   3-12 MB; sent whole over a mobile connection the page sat with no sign
 *   of life for many seconds (it looked hung), and nginx refuses a request
 *   body over its default 1 MB before Flask ever sees it. The server only
 *   keeps a 512px square (accounts.processPhoto), so a 1024px JPEG here
 *   loses nothing; the server still re-encodes it and strips everything.
 *   Any step that fails (an old browser, a format the browser cannot draw)
 *   sends the original file, as before.
 * ! The avatar says "Uploading..." from the moment a file is chosen. */
(function () {
  const input = document.getElementById("acct-photo-file"), form = document.getElementById("acct-photo-form");
  if (!input || !form) return;
  const MAX = 1024;

  function busy() {
    const label = form.querySelector(".acct-avatar"), cta = form.querySelector(".acct-avatar-cta");
    if (label) label.classList.add("is-busy");
    if (cta) cta.textContent = "Uploading…";
  }

  function shrink(file) {
    return new Promise((resolve) => {
      if (!/^image\/(jpeg|png|webp)$/i.test(file.type || "") || !window.URL || !window.DataTransfer) return resolve(null);
      const url = URL.createObjectURL(file), img = new Image();
      img.onload = () => {
        try {
          const scale = Math.min(1, MAX / Math.max(img.naturalWidth, img.naturalHeight));
          if (scale === 1 && file.size < 900 * 1024) { URL.revokeObjectURL(url); return resolve(null); }
          const c = document.createElement("canvas");
          c.width = Math.round(img.naturalWidth * scale);
          c.height = Math.round(img.naturalHeight * scale);
          c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);   // browsers draw it upright (EXIF)
          URL.revokeObjectURL(url);
          c.toBlob((blob) => resolve(blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : null), "image/jpeg", 0.9);
        } catch (e) { URL.revokeObjectURL(url); resolve(null); }
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
      img.src = url;
    });
  }

  input.addEventListener("change", async () => {
    if (!input.files || !input.files.length) return;
    busy();
    try {
      const small = await shrink(input.files[0]);
      if (small) {
        const dt = new DataTransfer();
        dt.items.add(small);
        input.files = dt.files;
      }
    } catch (e) { /* the original file goes, as before */ }
    form.submit();
  });
})();

/* one submit per form: a second tap while the first is on its way would
   send it twice. The button reads as busy; it is not `disabled`, which
   would drop it from the form data. */
(function () {
  document.querySelectorAll(".account-page form").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (e.defaultPrevented) return;
      if (form.dataset.sent) { e.preventDefault(); return; }
      form.dataset.sent = "1";
      const b = e.submitter || form.querySelector("button[type=submit]");
      if (b) { b.classList.add("is-busy"); b.setAttribute("aria-busy", "true"); }
    });
  });
  window.addEventListener("pageshow", (e) => {
    if (!e.persisted) return;
    document.querySelectorAll(".account-page form[data-sent]").forEach((f) => {
      delete f.dataset.sent;
      f.querySelectorAll(".is-busy").forEach((b) => { b.classList.remove("is-busy"); b.removeAttribute("aria-busy"); });
    });
  });
})();
