/* Keeps a chat page alive: asks for new messages every few seconds, shows them, and reloads the page when the chat changes hands (waiting, with staff, closed). */
(function () {
  var log = document.getElementById("asst-log");
  if (!log || !log.dataset.poll) { return; }
  var last = parseInt(log.dataset.last || "0", 10);
  var status = log.dataset.status;
  var staffView = log.dataset.staffView === "1";

  function addMessage(m) {
    var el;
    if (m.sender === "event") {
      el = document.createElement("p");
      el.className = "asst-event";
      el.textContent = m.kind ? m.body : "Checked: " + m.tool;
    } else {
      el = document.createElement("div");
      el.className = "asst-msg is-" + (staffView ? m.sender : (m.sender === "client" ? "client" : "ai"));
      var bubble = document.createElement("div");
      bubble.className = "asst-bubble";
      if (m.sender === "staff" && m.who) {
        var who = document.createElement("strong");
        who.textContent = m.who;
        bubble.appendChild(who);
        bubble.appendChild(document.createElement("br"));
      }
      bubble.appendChild(document.createTextNode(m.body));
      el.appendChild(bubble);
    }
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
  }

  function tick() {
    if (document.hidden) { return; }
    fetch(log.dataset.poll + "?after=" + last, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) { return; }
        var fresh = data.messages.filter(function (m) { return m.id > last; });
        fresh.forEach(function (m) { last = Math.max(last, m.id); addMessage(m); });
        if (data.status !== status) { window.location.reload(); }
      })
      .catch(function () { /* the next tick tries again */ });
  }

  log.scrollTop = log.scrollHeight;
  window.setInterval(tick, 4000);
  window.chatPollSeen = function (id) { last = Math.max(last, id); };
})();
