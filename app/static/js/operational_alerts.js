/** Load the shared header's preview of existing operational alerts. */
(function () {
    "use strict";

    var root = document.getElementById("header-alerts");
    if (!root || root.dataset.loaded === "true") return;

    var badge = document.getElementById("header-alert-count");
    var list = document.getElementById("header-alert-list");

    function message(text) {
        var paragraph = document.createElement("p");
        paragraph.className = "header-alerts__empty";
        paragraph.textContent = text;
        list.replaceChildren(paragraph);
    }

    fetch("/api/operational-alerts", { credentials: "same-origin" })
        .then(function (response) {
            if (!response.ok) throw new Error("Alert request failed");
            return response.json();
        })
        .then(function (data) {
            badge.hidden = data.count === 0;
            badge.textContent = data.count > 99 ? "99+" : String(data.count);
            if (!data.alerts.length) {
                message("No hay alertas operativas pendientes.");
                return;
            }

            list.replaceChildren();
            data.alerts.forEach(function (alert) {
                var item = document.createElement(alert.url ? "a" : "div");
                if (alert.url) item.href = alert.url;
                var severity = ["critical", "warning", "info"].includes(alert.severity)
                    ? alert.severity : "info";
                item.className = "header-alerts__item header-alerts__item--" + severity;
                var title = document.createElement("strong");
                title.textContent = alert.title;
                var detail = document.createElement("p");
                detail.textContent = alert.message;
                item.append(title, detail);
                list.appendChild(item);
            });
        })
        .catch(function () {
            message("No se pudieron cargar las alertas.");
        });
})();
