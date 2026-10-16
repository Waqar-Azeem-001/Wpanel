/* The help article page: marks the section being read in "In this article", and sends the Was-this-helpful vote. */
(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll(".help-toc a"));
  var heads = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); }).filter(Boolean);
  if (links.length && "IntersectionObserver" in window) {
    var current = null;
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { current = entry.target.id; }
      });
      if (!current) { return; }
      links.forEach(function (a) { a.classList.toggle("is-current", a.getAttribute("href") === "#" + current); });
    }, { rootMargin: "-80px 0px -65% 0px" });
    heads.forEach(function (h) { observer.observe(h); });
  }

  var box = document.querySelector(".help-vote");
  if (!box) { return; }
  var thanks = box.querySelector(".help-vote-thanks");
  function mark(choice) {
    box.querySelectorAll("button[data-choice]").forEach(function (b) { b.classList.toggle("is-picked", b.dataset.choice === choice); b.disabled = true; });
    if (thanks) { thanks.hidden = false; }
  }
  if (box.dataset.voted) { mark(box.dataset.voted); }
  box.querySelectorAll("button[data-choice]").forEach(function (button) {
    button.addEventListener("click", function () {
      var body = new FormData();
      var token = box.querySelector("input[name=csrfmiddlewaretoken]");
      body.append("choice", button.dataset.choice);
      if (token) { body.append("csrfmiddlewaretoken", token.value); }
      fetch(box.dataset.voteUrl, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && d.ok) { mark(d.choice || button.dataset.choice); } });
    });
  });
})();
