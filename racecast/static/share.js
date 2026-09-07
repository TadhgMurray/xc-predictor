/* Share (281): the phone's share sheet where there is one, the link on the
   clipboard where there is not; the page's card is the link's preview. */
document.querySelectorAll(".share-btn").forEach(function (b) {
  b.addEventListener("click", function () {
    var url = b.dataset.shareUrl, title = b.dataset.shareTitle;
    if (navigator.share) { navigator.share({title: title, url: url}).catch(function () {}); return; }
    var done = function () {
      var sp = b.querySelector("span"), was = sp ? sp.textContent : "";
      if (sp) sp.textContent = "Link copied";
      b.classList.add("is-done");
      setTimeout(function () { if (sp) sp.textContent = was; b.classList.remove("is-done"); }, 1600);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, function () { window.prompt("Copy this link", url); });
    else window.prompt("Copy this link", url);
  });
});
