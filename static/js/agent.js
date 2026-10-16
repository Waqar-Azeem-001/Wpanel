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

    function card(text, id, state, needs) {
      var el = document.createElement("div");
      el.className = "asst-action is-" + state + (needs === "code" ? " needs-code" : "");
      el.dataset.needs = needs || "approve";
      el.dataset.action = id;
      el.dataset.status = state;
      var line = document.createElement("div");
      line.className = "asst-action-text";
      var icon = document.createElement("i");
      icon.className = "bi bi-shield-check";
      line.appendChild(icon);
      line.appendChild(document.createTextNode(" " + text));
      el.appendChild(line);
      if (state === "pending") {
        var row = document.createElement("div");
        row.className = "asst-action-buttons";
        if (needs === "code") {
          var link = document.createElement("a");
          link.className = "btn btn-sm btn-primary";
          link.href = box.dataset.verify + id + "/";
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "Verify securely";
          row.appendChild(link);
        }
        [["approve", "Approve", "btn btn-sm btn-primary"], ["cancel", "Cancel", "btn btn-sm btn-outline-secondary"]].forEach(function (b) {
          if (b[0] === "approve" && needs === "code") { return; }
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = b[2];
          btn.dataset.act = b[0];
          btn.textContent = b[1];
          row.appendChild(btn);
        });
        el.appendChild(row);
      } else {
        var s = document.createElement("span");
        s.className = "asst-action-state";
        s.textContent = state.charAt(0).toUpperCase() + state.slice(1);
        el.appendChild(s);
      }
      return el;
    }

    function thumbs(id) {
      var wrap = document.createElement("span");
      wrap.className = "asst-feedback";
      wrap.dataset.msg = id;
      [["up", "bi-hand-thumbs-up", "This helped"], ["down", "bi-hand-thumbs-down", "This did not help"]].forEach(function (v) {
        var b = document.createElement("button");
        b.type = "button";
        b.dataset.vote = v[0];
        b.title = v[2];
        b.setAttribute("aria-label", v[2]);
        var i = document.createElement("i");
        i.className = "bi " + v[1];
        b.appendChild(i);
        wrap.appendChild(b);
      });
      return wrap;
    }

    function add(sender, text, tool, kind, who, action, state, needs, id) {
      var el;
      if (sender === "event" && kind === "action") {
        el = card(text, action, state || "pending", needs);
      } else if (sender === "event") {
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
        text.split(/(\/help\/[a-z0-9-]+\/[a-z0-9-]+\/)/).forEach(function (part) {
          if (/^\/help\/[a-z0-9-]+\/[a-z0-9-]+\/$/.test(part)) {
            var a = document.createElement("a");
            a.href = part;
            a.textContent = part;
            bubble.appendChild(a);
          } else {
            bubble.appendChild(document.createTextNode(part));
          }
        });
        el.appendChild(bubble);
        if (sender === "ai" && id) { el.appendChild(thumbs(id)); }
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
              add(m.sender, m.body, m.tool, m.kind, m.who, m.action, m.state, m.needs, m.id);
            });
          })
          .catch(function () { wait.remove(); add("ai", "The connection dropped. Please try again."); })
          .then(function () { button.disabled = false; input.focus(); });
      });
    }

    box.addEventListener("click", function (e) {
      var vote = e.target.closest(".asst-feedback button");
      if (vote) {
        var holderVote = vote.closest(".asst-feedback");
        var form = new FormData();
        form.append("csrfmiddlewaretoken", csrf(root) || csrf(document));
        fetch(box.dataset.feedback + holderVote.dataset.msg + "/" + vote.dataset.vote + "/", { method: "POST", body: form, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
          .then(function (r) { if (r.ok) { holderVote.querySelectorAll("button").forEach(function (b) { b.classList.toggle("is-picked", b === vote); }); } });
        return;
      }
      var btn = e.target.closest("button[data-act]");
      var holder = btn && btn.closest(".asst-action");
      if (!btn || !holder) { return; }
      var body = new FormData();
      body.append("csrfmiddlewaretoken", csrf(root) || csrf(document));
      holder.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      fetch(box.dataset.act + holder.dataset.action + "/" + btn.dataset.act + "/", { method: "POST", body: body, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) {
            add("ai", res.data.error || "That did not work.");
            holder.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
            return;
          }
          var fresh = card(holder.querySelector(".asst-action-text").textContent.trim(), holder.dataset.action, res.data.status, holder.dataset.needs);
          holder.replaceWith(fresh);
          res.data.messages.forEach(function (m) { last = Math.max(last, m.id); add(m.sender, m.body, m.tool, m.kind, m.who); });
        })
        .catch(function () { holder.querySelectorAll("button").forEach(function (b) { b.disabled = false; }); });
    });

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
          data.messages.forEach(function (m) { if (m.id > last) { last = m.id; add(m.sender, m.body, m.tool, m.kind, m.who, m.action, m.state, m.needs, m.id); } });
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
