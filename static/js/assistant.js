(function () {
  var form = document.getElementById("asst-form");
  var log = document.getElementById("asst-log");
  if (!form || !log) { return; }
  var input = document.getElementById("asst-input");
  var button = form.querySelector("button[type=submit]");
  log.scrollTop = log.scrollHeight;

  function add(sender, text, tool) {
    var el;
    if (sender === "event") {
      el = document.createElement("p");
      el.className = "asst-event";
      el.textContent = (tool === "request_human_handoff" ? "Handed to our team" : "Checked: " + tool);
    } else {
      el = document.createElement("div");
      el.className = "asst-msg is-" + sender;
      var bubble = document.createElement("div");
      bubble.className = "asst-bubble";
      bubble.textContent = text;
      el.appendChild(bubble);
    }
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) { return; }
    var hello = log.querySelector(".asst-hello");
    if (hello) { hello.remove(); }
    add("client", text);
    var data = new FormData(form);
    input.value = "";
    button.disabled = true;
    var wait = document.createElement("p");
    wait.className = "asst-event asst-typing";
    wait.textContent = "Looking into it...";
    log.appendChild(wait);
    log.scrollTop = log.scrollHeight;
    fetch(form.action, { method: "POST", body: data, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        wait.remove();
        if (!res.ok) { add("ai", res.data.error || "Something went wrong. Please try again."); return; }
        res.data.messages.forEach(function (m) {
          if (window.chatPollSeen) { window.chatPollSeen(m.id); }
          if (m.sender === "client") { return; }
          add(m.sender, m.body, m.tool);
        });
      })
      .catch(function () { wait.remove(); add("ai", "The connection dropped. Please try again."); })
      .then(function () { button.disabled = false; input.focus(); });
  });
})();
