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
    let seq = 0, timer = null;
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
        try {
          const res = await fetch("/search/api?kind=" + kind + "&q=" + encodeURIComponent(text));
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
          list.hidden = !list.innerHTML;
        } catch (e) { list.hidden = true; }
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

/* the picture (305): choosing a file submits it; nothing else to click */
(function () {
  const input = document.getElementById("acct-photo-file"), form = document.getElementById("acct-photo-form");
  if (!input || !form) return;
  input.addEventListener("change", () => { if (input.files && input.files.length) form.submit(); });
})();
