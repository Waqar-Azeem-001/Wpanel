/*
 * The mail composer: the message box grows as you type (so the whole message is always visible and nothing is hidden behind
 * a tiny scroll area), a counter shows how much of the limit is used, Ctrl+Enter sends, and an unsent draft is kept in this
 * browser so a closed tab or a reload does not lose it. The server checks every address and length; nothing here decides
 * what is allowed.
 */
(function () {
  var form = document.querySelector("[data-compose]");
  if (!form) { return; }
  var body = form.querySelector("[data-compose-body]");
  var to = form.querySelector("[name=to]");
  var subject = form.querySelector("[name=subject]");
  var counter = form.querySelector("[data-compose-count]");
  var note = form.querySelector("[data-draft-note]");
  var key = form.getAttribute("data-draft-key");
  var limit = parseInt(body.getAttribute("maxlength"), 10) || 10000;
  var timer = null;

  function store() { try { return window.localStorage; } catch (e) { return null; } }

  function grow() {
    body.style.height = "auto"; // measuring needs the natural height first
    var max = Math.max(320, Math.round(window.innerHeight * 0.7));
    body.style.height = Math.min(Math.max(body.scrollHeight + 2, 280), max) + "px";
    body.style.overflowY = body.scrollHeight > max ? "auto" : "hidden";
  }

  function count() {
    var used = body.value.length;
    counter.textContent = used.toLocaleString() + " / " + limit.toLocaleString();
    counter.classList.toggle("is-near", used > limit * 0.9);
  }

  function saveDraft() {
    var box = store();
    if (!box || !key) { return; }
    try {
      if (!to.value && !subject.value && !body.value.trim()) { box.removeItem(key); return; }
      box.setItem(key, JSON.stringify({ to: to.value, subject: subject.value, body: body.value }));
    } catch (e) { /* storage full or blocked: the draft is a convenience only */ }
  }

  function clearDraft() {
    var box = store();
    try { if (box && key) { box.removeItem(key); } } catch (e) { /* ignore */ }
  }

  // Restore a saved draft, but never over text the server just put here (a reply, or a message that failed to send).
  (function restore() {
    var box = store();
    if (!box || !key || to.value || subject.value || body.value.trim()) { return; }
    try {
      var draft = JSON.parse(box.getItem(key) || "null");
      if (draft && (draft.to || draft.subject || (draft.body || "").trim())) {
        to.value = draft.to || "";
        subject.value = draft.subject || "";
        body.value = draft.body || "";
        if (note) { note.hidden = false; }
      }
    } catch (e) { /* a broken draft is simply ignored */ }
  })();

  body.addEventListener("input", function () { grow(); count(); });
  form.addEventListener("input", function () { window.clearTimeout(timer); timer = window.setTimeout(saveDraft, 500); });
  form.addEventListener("submit", clearDraft);
  form.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      if (form.reportValidity()) { form.requestSubmit(); }
    }
  });

  var discard = form.querySelector("[data-draft-discard]");
  if (discard) {
    discard.addEventListener("click", function () {
      clearDraft();
      to.value = ""; subject.value = ""; body.value = "";
      if (note) { note.hidden = true; }
      grow(); count();
    });
  }

  window.addEventListener("resize", grow);
  grow();
  count();
  // a reply or a new message: start typing in the right place
  (body.value.trim() ? body : (to.value ? subject : to)).focus();
  if (body.value.trim() && document.activeElement === body) { body.setSelectionRange(0, 0); }
})();
