/*
 * The email editor (Setup > Email templates): plain text, or a designed message written in a small visual editor or as pasted HTML.
 * No library: a contenteditable surface with a toolbar. Everything that goes into the surface (the saved design, a paste) is cleaned first
 * in an inert document, so markup from anywhere cannot run in the page. The server cleans it again when the email is sent.
 */
(function () {
  "use strict";
  var form = document.getElementById("mail-form");
  if (!form) { return; }
  var surface = form.querySelector(".me-surface");
  var source = form.querySelector("#id_body_html");
  var text = form.querySelector("#id_body_text");
  var designBox = form.querySelector(".me-design");
  var radios = form.querySelectorAll("input[name=mode]");
  var brand = form.getAttribute("data-brand") || "#2459d6";
  var active = "visual";  // which design pane is showing
  var lastField = text;   // where a variable goes when its name is clicked

  var DROP = "script,style,iframe,frame,frameset,object,embed,applet,form,textarea,select,button,svg,math,link,meta,base,audio,video,canvas,input,noscript,template".split(",");
  var STYLE_OK = /^(color|background|background-color|font-size|font-weight|font-style|font-family|text-align|text-decoration|text-transform|line-height|letter-spacing|padding[a-z-]*|margin[a-z-]*|border[a-z-]*|width|max-width|min-width|height|display|vertical-align|list-style-type|white-space|word-break)$/;

  function cleanNode(root) {
    root.querySelectorAll(DROP.join(",")).forEach(function (el) { el.remove(); });
    root.querySelectorAll("*").forEach(function (el) {
      Array.prototype.slice.call(el.attributes).forEach(function (attr) {
        var name = attr.name.toLowerCase(), value = attr.value;
        if (name.indexOf("on") === 0 || name === "srcdoc" || name === "formaction") { el.removeAttribute(attr.name); return; }
        if ((name === "href" || name === "src") && /^\s*(javascript|data|vbscript):/i.test(value.replace(/[\u0000- ]+/g, ""))) { el.removeAttribute(attr.name); return; }
        if (name === "style") {
          var kept = value.split(";").filter(function (d) {
            var parts = d.split(":"), prop = (parts[0] || "").trim().toLowerCase(), val = parts.slice(1).join(":");
            return STYLE_OK.test(prop) && !/url\s*\(|expression|javascript|@import|behavio/i.test(val);
          });
          if (kept.length) { el.setAttribute("style", kept.join(";")); } else { el.removeAttribute("style"); }
        }
      });
    });
    return root;
  }
  function clean(html) {
    var doc = new DOMParser().parseFromString("<body>" + html + "</body>", "text/html");  // an inert document: nothing in it runs
    return cleanNode(doc.body).innerHTML;
  }
  // The browser writes > < & as entities inside the text; inside {{ }} and {% %} they must be the plain characters.
  function unescapeTags(html) {
    return html.replace(/(\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\})/g, function (block) {
      return block.replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&quot;/g, "\"").replace(/&#39;/g, "'").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&");
    });
  }
  function surfaceToSource() { source.value = unescapeTags(surface.innerHTML).replace(/<div><br><\/div>/g, "<p><br></p>"); }
  function sourceToSurface() { surface.innerHTML = clean(source.value || ""); }

  function mode() { var picked = form.querySelector("input[name=mode]:checked"); return picked ? picked.value : "text"; }
  function showMode() {
    var design = mode() === "design";
    designBox.hidden = !design;
    form.querySelector(".me-text-label-text").hidden = design;
    form.querySelector(".me-text-label-design").hidden = !design;
    form.querySelector(".me-text-help-text").hidden = design;
    form.querySelector(".me-text-help-design").hidden = !design;
    text.rows = design ? 6 : 16;
    if (design && active === "visual") { sourceToSurface(); }
    lastField = design ? (active === "visual" ? surface : source) : text;
  }
  function showTab(name) {
    if (name === active) { return; }
    if (active === "visual") { surfaceToSource(); } else { sourceToSurface(); }
    active = name;
    form.querySelectorAll("[data-me-tab]").forEach(function (b) { b.classList.toggle("is-active", b.getAttribute("data-me-tab") === name); });
    form.querySelectorAll("[data-me-pane]").forEach(function (pane) { pane.hidden = pane.getAttribute("data-me-pane") !== name; });
    lastField = name === "visual" ? surface : source;
  }

  radios.forEach(function (r) { r.addEventListener("change", showMode); });
  form.querySelectorAll("[data-me-tab]").forEach(function (b) { b.addEventListener("click", function () { showTab(b.getAttribute("data-me-tab")); }); });
  [text, source, surface].forEach(function (el) { el.addEventListener("focus", function () { lastField = el; }); });

  function esc(value) { return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function run(cmd, arg) { surface.focus(); document.execCommand(cmd, false, arg || null); }
  function selectedText() { var s = window.getSelection(); return s && s.rangeCount ? s.toString() : ""; }
  var COMMANDS = {
    bold: function () { run("bold"); }, italic: function () { run("italic"); }, underline: function () { run("underline"); },
    h2: function () { run("formatBlock", "<h2>"); }, p: function () { run("formatBlock", "<p>"); },
    ul: function () { run("insertUnorderedList"); }, ol: function () { run("insertOrderedList"); },
    left: function () { run("justifyLeft"); }, center: function () { run("justifyCenter"); },
    unlink: function () { run("unlink"); }, hr: function () { run("insertHorizontalRule"); },
    clear: function () { run("removeFormat"); }, undo: function () { run("undo"); }, redo: function () { run("redo"); },
    link: function () {
      var picked = selectedText();
      var url = window.prompt("Where should the link go? (a web address, or {{ link }})", "https://");
      if (!url) { return; }
      run("insertHTML", "<a href=\"" + esc(url) + "\">" + esc(picked || url) + "</a>");
    },
    button: function () {
      var label = window.prompt("What does the button say?", selectedText() || "Open it");
      if (!label) { return; }
      var url = window.prompt("Where does it go? (a web address, or {{ link }})", "{{ link }}");
      if (!url) { return; }
      run("insertHTML", "<a href=\"" + esc(url) + "\" style=\"display:inline-block;padding:12px 24px;background:" + esc(brand) + ";color:#ffffff;font-weight:bold;text-decoration:none;border-radius:8px\">" + esc(label) + "</a>&nbsp;");
    }
  };
  form.querySelectorAll("[data-cmd]").forEach(function (button) {
    button.addEventListener("mousedown", function (event) { event.preventDefault(); });  // keep the selection in the surface
    button.addEventListener("click", function () { var fn = COMMANDS[button.getAttribute("data-cmd")]; if (fn) { fn(); } });
  });

  // Pasting: a design keeps its formatting (cleaned), anything else goes in as plain text.
  surface.addEventListener("paste", function (event) {
    var data = event.clipboardData; if (!data) { return; }
    event.preventDefault();
    var html = data.getData("text/html");
    if (html) { document.execCommand("insertHTML", false, clean(html)); } else { document.execCommand("insertText", false, data.getData("text/plain")); }
  });

  // A variable goes into whatever the person was last typing in.
  form.parentNode.parentNode.querySelectorAll("[data-insert]").forEach(function (button) {
    button.addEventListener("mousedown", function (event) { event.preventDefault(); });
    button.addEventListener("click", function () {
      var token = "{{ " + button.getAttribute("data-insert") + " }}";
      if (lastField === surface) { surface.focus(); document.execCommand("insertText", false, token); return; }
      var field = lastField || text, start = field.selectionStart || 0, end = field.selectionEnd || 0;
      field.value = field.value.slice(0, start) + token + field.value.slice(end);
      field.focus(); field.selectionStart = field.selectionEnd = start + token.length;
    });
  });

  // Before sending: the pane that is showing is the truth. In plain-text mode no design is sent at all.
  form.addEventListener("submit", function () {
    if (mode() === "design") { if (active === "visual") { surfaceToSource(); } } else { source.value = ""; }
  });
  showMode();
})();
