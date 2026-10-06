/*
 * DNS screen: shows only the fields a record type needs (priority for MX and SRV), an example for the value, and fills the edit
 * dialog from the row that opened it. The server validates every record by its type; nothing here decides what is allowed.
 */
(function () {
  var HINTS = {
    A: "An IPv4 address, for example 203.0.113.10. Use @ as the name for the domain itself.",
    AAAA: "An IPv6 address, for example 2001:db8::10.",
    CNAME: "Another host name, for example the-other-site.example.net. A CNAME cannot be used on @ and cannot share a name with other records.",
    MX: "The host name of the mail server, for example mail.example.com, and a priority (the lower number is tried first).",
    TXT: "Text, for example an SPF policy (v=spf1 +a +mx ~all) or a verification code a service gave you.",
    SRV: "The name looks like _sip._tcp. The value is: weight port host, for example 5 5060 sip.example.com, plus a priority.",
    CAA: 'Which authority may issue certificates: 0 issue "letsencrypt.org"'
  };

  function needsPriority(type) { return type === "MX" || type === "SRV"; }

  function show(form, type) {
    var box = form.querySelector("[data-dns-priority-box]");
    var hint = form.querySelector("[data-dns-hint]");
    if (box) { box.hidden = !needsPriority(type); }
    if (hint) { hint.textContent = HINTS[type] || ""; }
  }

  var addForm = document.querySelector("form.dns-add");
  if (addForm) {
    var select = addForm.querySelector("[data-dns-type]");
    select.addEventListener("change", function () { show(addForm, select.value); });
    show(addForm, select.value);
  }

  var dialog = document.getElementById("dns-edit");
  if (dialog) {
    var form = dialog.querySelector("form");
    dialog.addEventListener("show.bs.modal", function (event) {
      var button = event.relatedTarget;
      if (!button) { return; }
      var type = button.getAttribute("data-type");
      form.querySelector("[name=record_id]").value = button.getAttribute("data-id");
      form.querySelector("[name=type]").value = type;
      form.querySelector("[name=name]").value = button.getAttribute("data-name");
      form.querySelector("[name=value]").value = button.getAttribute("data-value");
      form.querySelector("[name=priority]").value = button.getAttribute("data-priority") || "";
      var ttl = form.querySelector("[name=ttl]");
      var seconds = button.getAttribute("data-ttl");
      if (!Array.prototype.some.call(ttl.options, function (o) { return o.value === seconds; })) {
        ttl.add(new Option(seconds + " seconds", seconds)); // a TTL that is not one of the presets must survive an edit
      }
      ttl.value = seconds;
      dialog.querySelector("[data-dns-edit-type]").textContent = type;
      show(form, type);
    });
  }
})();
