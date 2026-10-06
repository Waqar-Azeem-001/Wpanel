/*
 * Database manager: builds the insert/edit form from the table's column list, and puts a ready-made statement into the
 * SQL box. The server checks every name and value; nothing here decides what is allowed.
 */
(function () {
  var dialog = document.getElementById("dbm-row");

  function textField(column, value, mode) {
    var wrap = document.createElement("div");
    wrap.className = "mb-3";
    var id = "dbm-f-" + column.name;
    var label = document.createElement("label");
    label.className = "form-label small";
    label.setAttribute("for", id);
    label.textContent = column.name + " (" + column.type.toLowerCase() + (column.pk ? ", key" : "") + ")";
    var long = (value || "").length > 60 || /blob|json/i.test(column.type);
    var input = document.createElement(long ? "textarea" : "input");
    if (!long) { input.type = "text"; } else { input.rows = 3; }
    input.className = "form-control";
    input.id = id;
    if (mode === "update" && column.pk) {
      input.readOnly = true; // the key identifies the row; it is not edited here
    } else {
      input.name = "val__" + column.name;
    }
    input.value = value === null || value === undefined ? "" : value;
    if (value === null) { input.placeholder = "NULL"; }
    wrap.appendChild(label);
    wrap.appendChild(input);
    return wrap;
  }

  function fill(mode, values, keys) {
    var columns = JSON.parse(dialog.getAttribute("data-columns") || "[]");
    var holder = dialog.querySelector("[data-db-fields]");
    holder.textContent = "";
    dialog.querySelector("[data-db-action]").value = mode;
    dialog.querySelector("[data-db-title]").textContent = mode === "insert" ? "Insert a row" : "Edit this row";
    columns.forEach(function (column) {
      var value = values ? values[column.name] : "";
      holder.appendChild(textField(column, mode === "insert" ? "" : value, mode));
    });
    Object.keys(keys || {}).forEach(function (name) {
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "key__" + name;
      hidden.value = keys[name];
      holder.appendChild(hidden);
    });
  }

  if (dialog) {
    dialog.addEventListener("show.bs.modal", function (event) {
      var opener = event.relatedTarget;
      if (opener && opener.hasAttribute("data-db-edit")) {
        fill("update", JSON.parse(opener.getAttribute("data-values") || "{}"), JSON.parse(opener.getAttribute("data-keys") || "{}"));
      } else {
        fill("insert", null, null);
      }
    });
    dialog.addEventListener("shown.bs.modal", function () {
      var first = dialog.querySelector("input[type=text], textarea");
      if (first) { first.focus(); }
    });
  }

  var sql = document.getElementById("dbm-sql");
  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-sql-snippet]") : null;
    if (button && sql) { sql.value = button.getAttribute("data-sql-snippet"); sql.focus(); }
  });

  // Ask before a statement that destroys data (the shared confirmation dialog reads data-confirm at submit time).
  var form = document.getElementById("dbm-sql-form");
  if (form && sql) {
    function check() {
      if (/\b(drop|truncate|delete|alter)\b/i.test(sql.value)) {
        form.setAttribute("data-confirm", "This statement can destroy data and cannot be undone. Run it?");
      } else {
        form.removeAttribute("data-confirm");
      }
    }
    sql.addEventListener("input", check);
    check();
  }
})();
