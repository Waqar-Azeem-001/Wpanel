/*
 * The mail app's compose window: opening, minimising and closing it; a small rich-text editor (bold, italic, underline,
 * lists, quote, links); a character counter; Ctrl+Enter to send; and an unsent draft kept in this browser so a reload does
 * not lose it. The server checks and cleans every address and all the HTML; nothing here decides what is allowed.
 */
(function () {
  var win = document.getElementById("ml-compose");
  if (!win) { return; }
  var editor = win.querySelector("[data-editor]");
  var to = win.querySelector("[name=to]");
  var subject = win.querySelector("[name=subject]");
  var htmlField = win.querySelector("[data-compose-html]");
  var textField = win.querySelector("[data-compose-text]");
  var counter = win.querySelector("[data-compose-count]");
  var note = win.querySelector("[data-draft-note]");
  var key = win.getAttribute("data-draft-key");
  var limit = 10000;
  var timer = null;

  function store() { try { return window.localStorage; } catch (e) { return null; } }
  function text() { return (editor.innerText || "").replace(/ /g, " ").trim(); }
  function empty() { return !to.value.trim() && !subject.value.trim() && !text(); }

  function count() {
    var used = text().length;
    counter.textContent = used.toLocaleString() + " / " + limit.toLocaleString();
    counter.classList.toggle("is-near", used > limit * 0.9);
  }

  function saveDraft() {
    var box = store();
    if (!box || !key) { return; }
    try {
      if (empty()) { box.removeItem(key); return; }
      box.setItem(key, JSON.stringify({ to: to.value, subject: subject.value, html: editor.innerHTML }));
    } catch (e) { /* storage full or blocked: the draft is only a convenience */ }
  }

  function clearDraft() {
    var box = store();
    try { if (box && key) { box.removeItem(key); } } catch (e) { /* ignore */ }
  }

  function open(focusEditor) {
    win.classList.add("is-open");
    win.classList.remove("is-min");
    (focusEditor ? editor : (to.value ? subject : to)).focus();
  }

  // A draft comes back only into an empty window: never over a reply, or a message that failed to send.
  function restore() {
    var box = store();
    if (!box || !key || !empty()) { return false; }
    try {
      var draft = JSON.parse(box.getItem(key) || "null");
      if (draft && (draft.to || draft.subject || (draft.html || "").replace(/<[^>]*>/g, "").trim())) {
        to.value = draft.to || "";
        subject.value = draft.subject || "";
        editor.innerHTML = draft.html || ""; // the draft is this person's own text, and the server cleans it again on send
        if (note) { note.hidden = false; }
        return true;
      }
    } catch (e) { /* a broken draft is ignored */ }
    return false;
  }

  document.addEventListener("click", function (event) {
    var opener = event.target.closest ? event.target.closest("[data-compose-open]") : null;
    if (opener) { restore(); count(); open(false); }
  });
  win.querySelector("[data-compose-close]").addEventListener("click", function (event) {
    event.stopPropagation();
    saveDraft();
    win.classList.remove("is-open");
  });
  win.querySelector("[data-compose-min]").addEventListener("click", function (event) {
    event.stopPropagation();
    win.classList.toggle("is-min");
  });
  win.querySelector(".ml-cw-head").addEventListener("click", function () { win.classList.toggle("is-min"); });
  win.querySelector("[data-compose-discard]").addEventListener("click", function () {
    clearDraft();
    to.value = ""; subject.value = ""; editor.innerHTML = "";
    if (note) { note.hidden = true; }
    count();
    win.classList.remove("is-open");
  });
  var discard = win.querySelector("[data-draft-discard]");
  if (discard) {
    discard.addEventListener("click", function () {
      clearDraft(); to.value = ""; subject.value = ""; editor.innerHTML = "";
      if (note) { note.hidden = true; }
      count();
    });
  }

  // formatting buttons: they act on the editor and never take the cursor away from it
  win.querySelectorAll("[data-cmd]").forEach(function (button) {
    button.addEventListener("mousedown", function (event) { event.preventDefault(); });
    button.addEventListener("click", function () {
      editor.focus();
      var command = button.getAttribute("data-cmd");
      var arg = button.getAttribute("data-arg") || null;
      if (command === "createLink") {
        var address = window.prompt("Link address (https://...)", "https://");
        if (!address || !/^(https?:|mailto:)/i.test(address.trim())) { return; }
        arg = address.trim();
      }
      document.execCommand(command, false, arg);
      count();
    });
  });

  // pasting always brings plain text, so no foreign styles or scripts ever enter the editor
  editor.addEventListener("paste", function (event) {
    var data = event.clipboardData || window.clipboardData;
    if (!data) { return; }
    event.preventDefault();
    document.execCommand("insertText", false, data.getData("text/plain"));
  });

  win.addEventListener("input", function () {
    count();
    window.clearTimeout(timer);
    timer = window.setTimeout(saveDraft, 500);
  });
  win.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      if (win.reportValidity()) { win.requestSubmit(); }
    }
  });
  win.addEventListener("submit", function (event) {
    if (!text()) {
      event.preventDefault();
      editor.focus();
      return;
    }
    htmlField.value = editor.innerHTML;
    textField.value = text();
    clearDraft();
  });

  count();
  if (win.classList.contains("is-open")) {
    // opened by the server: a reply starts below the cursor's greeting spot, a failed send keeps what was typed
    var hasText = !!text();
    if (hasText) {
      editor.focus();
      var range = document.createRange();
      range.setStart(editor, 0);
      range.collapse(true);
      var selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    } else {
      (to.value ? subject : to).focus();
    }
  }
})();
