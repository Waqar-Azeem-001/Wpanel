/*
 * The mail app's behaviour: the rich-text editor (bold, italic, underline, lists, quote, links) wherever there is one, the
 * floating compose window (open, minimise, close, drafts saved on the server as you write), the inline reply under a message,
 * the character counter, Ctrl+Enter to send, and "Close mail" for a mail window opened from the panel.
 * The server checks and cleans every address and all the HTML; nothing here decides what is allowed.
 */
(function () {
  var LIMIT = 10000;

  function plain(editor) { return (editor.innerText || "").replace(/ /g, " ").trim(); }

  // ---- the editor: toolbar buttons, plain-text paste, and copying the result into the form on submit ----------------------
  function wireEditor(form) {
    var editor = form.querySelector("[data-editor]");
    if (!editor) { return null; }
    var counter = form.querySelector("[data-compose-count]");
    var htmlField = form.querySelector("[data-html-field]");
    var textField = form.querySelector("[data-text-field]");

    function count() {
      if (!counter) { return; }
      var used = plain(editor).length;
      counter.textContent = used.toLocaleString() + " / " + LIMIT.toLocaleString();
      counter.classList.toggle("is-near", used > LIMIT * 0.9);
    }

    form.querySelectorAll("[data-cmd]").forEach(function (button) {
      button.addEventListener("mousedown", function (event) { event.preventDefault(); }); // keep the cursor in the editor
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

    editor.addEventListener("paste", function (event) {
      var data = event.clipboardData || window.clipboardData;
      if (!data) { return; }
      event.preventDefault(); // pasted text is always plain, so no foreign styles or scripts enter the editor
      document.execCommand("insertText", false, data.getData("text/plain"));
    });
    editor.addEventListener("input", count);

    function fill() {
      if (htmlField) { htmlField.value = editor.innerHTML; }
      if (textField) { textField.value = plain(editor); }
    }
    form.addEventListener("submit", fill);
    count();
    return { editor: editor, fill: fill, count: count };
  }

  function caretAtStart(editor) {
    editor.focus();
    var range = document.createRange();
    range.setStart(editor, 0);
    range.collapse(true);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function ctrlEnter(form) {
    form.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        if (form.reportValidity()) { form.requestSubmit(); }
      }
    });
  }

  document.querySelectorAll("form[data-editor-form]").forEach(wireEditor);

  // ---- the floating compose window --------------------------------------------------------------------------------------
  var win = document.getElementById("ml-compose");
  if (win) {
    var parts = wireEditor(win);
    var to = win.querySelector("[name=to]");
    var subject = win.querySelector("[name=subject]");
    var draftId = win.querySelector("[data-draft-id]");
    var saved = win.querySelector("[data-saved-note]");
    var timer = null;
    var lastSaved = "";

    function empty() { return !to.value.trim() && !subject.value.trim() && !plain(parts.editor); }

    // Drafts live on the server (Drafts folder). A quiet background request saves them; it never changes the page.
    function saveDraft(announce) {
      if (empty()) { return Promise.resolve(); }
      var snapshot = to.value + "|" + subject.value + "|" + parts.editor.innerHTML;
      if (snapshot === lastSaved && !announce) { return Promise.resolve(); }
      parts.fill();
      var data = new FormData(win);
      data.set("action", "save_draft");
      return fetch(win.getAttribute("action"), { method: "POST", body: data, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (response) { return response.ok && response.status !== 204 ? response.json() : null; })
        .then(function (json) {
          if (json && json.draft_id) {
            draftId.value = json.draft_id;
            lastSaved = snapshot;
            if (saved) { saved.hidden = false; window.setTimeout(function () { saved.hidden = true; }, 2500); }
          }
        })
        .catch(function () { /* the next change tries again */ });
    }

    function open() {
      win.classList.add("is-open");
      win.classList.remove("is-min");
      if (plain(parts.editor)) { caretAtStart(parts.editor); } else { (to.value ? subject : to).focus(); }
    }

    document.addEventListener("click", function (event) {
      var opener = event.target.closest ? event.target.closest("[data-compose-open]") : null;
      if (opener) { open(); }
    });
    win.querySelector("[data-compose-close]").addEventListener("click", function (event) {
      event.stopPropagation();
      window.clearTimeout(timer);
      saveDraft(false).then(function () { win.classList.remove("is-open"); });
    });
    win.querySelector("[data-compose-min]").addEventListener("click", function (event) {
      event.stopPropagation();
      win.classList.toggle("is-min");
    });
    win.querySelector(".ml-cw-head").addEventListener("click", function () { win.classList.toggle("is-min"); });
    win.querySelector("[data-compose-discard]").addEventListener("click", function () {
      window.clearTimeout(timer);
      to.value = ""; subject.value = ""; parts.editor.innerHTML = ""; parts.count();
      if (draftId.value) {
        var data = new FormData(win);
        data.set("action", "delete"); data.set("message", draftId.value); data.set("folder", "drafts");
        fetch(win.getAttribute("action"), { method: "POST", body: data, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
        draftId.value = "";
      }
      win.classList.remove("is-open");
    });
    win.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(function () { saveDraft(false); }, 2000);
    });
    win.addEventListener("submit", function (event) {
      if (!plain(parts.editor)) { event.preventDefault(); parts.editor.focus(); return; }
      window.clearTimeout(timer);
    });
    ctrlEnter(win);
    if (win.classList.contains("is-open")) {
      if (plain(parts.editor)) { caretAtStart(parts.editor); } else { (to.value ? subject : to).focus(); }
    }
  }

  // ---- the inline reply, under the message ---------------------------------------------------------------------------------
  var inline = document.getElementById("ml-inline");
  if (inline) {
    var reply = wireEditor(inline);
    var bar = document.querySelector(".ml-reply-bar");
    function showReply() {
      inline.classList.add("is-open");
      if (bar) { bar.hidden = true; }
      inline.scrollIntoView({ block: "nearest", behavior: "smooth" });
      caretAtStart(reply.editor);
    }
    function hideReply() { inline.classList.remove("is-open"); if (bar) { bar.hidden = false; } }
    document.addEventListener("click", function (event) {
      var opener = event.target.closest ? event.target.closest("[data-reply-open]") : null;
      if (opener) { event.preventDefault(); showReply(); }
    });
    inline.querySelector("[data-reply-close]").addEventListener("click", hideReply);
    inline.addEventListener("submit", function (event) {
      if (!plain(reply.editor)) { event.preventDefault(); reply.editor.focus(); }
    });
    ctrlEnter(inline);
    if (inline.classList.contains("is-open")) { caretAtStart(reply.editor); }
  }

  // ---- "Close mail": a mail window opened from the panel closes; otherwise it goes back to Email accounts ------------------
  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("[data-close-window]") : null;
    if (!link || event.defaultPrevented || event.ctrlKey || event.metaKey) { return; }
    if (window.opener || window.history.length <= 1) {
      event.preventDefault();
      window.close();
      window.setTimeout(function () { window.location.href = link.href; }, 250); // a window the script cannot close: just navigate
    }
  });
})();
