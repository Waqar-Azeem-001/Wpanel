/* The AI Agent chat: the full page and the pop-up share this file. It sends messages, shows the answers, keeps asking for what the team writes, and reloads the chat when it changes hands. */
(function () {
  if (window.WHAgent) { return; }
  var timer = null;

  function csrf(root) {
    var el = root.querySelector("input[name=csrfmiddlewaretoken]");
    if (el) { return el.value; }
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function init(root, reload) {
    var box = root.querySelector("[data-agent-root]") || (root.matches && root.matches("[data-agent-root]") ? root : null);
    if (timer) { window.clearInterval(timer); timer = null; }
    if (!box) { return; }
    var log = box.querySelector(".asst-log");
    var form = box.querySelector(".asst-form");
    var last = parseInt(log.dataset.last || "0", 10);
    var status = log.dataset.status;
    log.scrollTop = log.scrollHeight;

    function add(sender, text, tool, kind, who) {
      var el;
      if (sender === "event") {
        el = document.createElement("p");
        el.className = "asst-event";
        el.textContent = kind ? text : "Checked: " + tool;
      } else {
        el = document.createElement("div");
        el.className = "asst-msg is-" + (sender === "client" ? "client" : "ai");
        var bubble = document.createElement("div");
        bubble.className = "asst-bubble";
        if (sender === "staff" && who) {
          var strong = document.createElement("strong");
          strong.textContent = who;
          bubble.appendChild(strong);
          bubble.appendChild(document.createElement("br"));
        }
        bubble.appendChild(document.createTextNode(text));
        el.appendChild(bubble);
      }
      var hello = log.querySelector(".asst-hello");
      if (hello) { hello.remove(); }
      log.appendChild(el);
      log.scrollTop = log.scrollHeight;
      return el;
    }

    if (form) {
      var input = form.querySelector("textarea");
      var button = form.querySelector("button[type=submit]");
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
      });
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var text = input.value.trim();
        if (!text) { return; }
        add("client", text);
        var data = new FormData(form);
        input.value = "";
        button.disabled = true;
        var wait = document.createElement("p");
        wait.className = "asst-event asst-typing";
        wait.textContent = "Looking into it...";
        log.appendChild(wait);
        log.scrollTop = log.scrollHeight;
        fetch(box.dataset.send, { method: "POST", body: data, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
          .then(function (res) {
            wait.remove();
            if (!res.ok) { add("ai", res.data.error || "Something went wrong. Please try again."); return; }
            res.data.messages.forEach(function (m) {
              last = Math.max(last, m.id);
              if (m.sender === "client") { return; }
              add(m.sender, m.body, m.tool, m.kind, m.who);
            });
          })
          .catch(function () { wait.remove(); add("ai", "The connection dropped. Please try again."); })
          .then(function () { button.disabled = false; input.focus(); });
      });
    }

    var ticket = box.querySelector("[data-agent-ticket]");
    if (ticket) {
      ticket.addEventListener("click", function () {
        post(ticket.dataset.agentTicket, root, function (d) { if (d.url) { window.location.href = d.url; } else { reload(); } });
      });
    }

    timer = window.setInterval(function () {
      if (document.hidden || root.hidden) { return; }
      fetch(log.dataset.poll + "?after=" + last, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) { return; }
          data.messages.forEach(function (m) { if (m.id > last) { last = m.id; add(m.sender, m.body, m.tool, m.kind, m.who); } });
          if (data.status !== status) { reload(); }
        })
        .catch(function () { /* the next tick tries again */ });
    }, 4000);
  }

  function post(url, root, done) {
    var body = new FormData();
    body.append("csrfmiddlewaretoken", csrf(root) || csrf(document));
    fetch(url, { method: "POST", body: body, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(done)
      .catch(function () { /* nothing changed */ });
  }

  // ---- the pop-up -------------------------------------------------------------------------------------------------------------------------
  function popup() {
    var pop = document.getElementById("agent-pop");
    if (!pop) { return; }
    var body = pop.querySelector(".agent-body");
    var loaded = false;

    function load() {
      fetch(pop.dataset.panel, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.text(); })
        .then(function (html) {
          body.innerHTML = html;
          loaded = true;
          init(body, load);
          body.querySelectorAll("[data-agent-post]").forEach(function (b) {
            b.addEventListener("click", function () { post(b.dataset.agentPost, body, load); });
          });
          var close = body.querySelector("[data-agent-close]");
          if (close) { close.addEventListener("click", function () { pop.hidden = true; }); }
          var field = body.querySelector("textarea");
          if (field) { field.focus(); }
        });
    }

    document.addEventListener("click", function (e) {
      var opener = e.target.closest("[data-agent-open]");
      if (!opener) { return; }
      e.preventDefault();
      if (!pop.hidden) { pop.hidden = true; return; }
      pop.hidden = false;
      if (!loaded) { load(); }
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !pop.hidden) { pop.hidden = true; } });
  }

  window.WHAgent = { init: init };
  document.addEventListener("DOMContentLoaded", function () {
    var page = document.querySelector("section.asst");
    if (page) { init(page, function () { window.location.reload(); }); }
    popup();
  });
})();
