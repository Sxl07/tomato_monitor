/** Reveal export download guidance without interrupting the browser's file request. */
(function () {
    "use strict";

    document.addEventListener("click", function (event) {
        var link = event.target.closest("[data-export-download]");
        if (!link) return;
        var container = link.closest("[data-export-download-container]");
        if (!container) return;
        var feedback = container.querySelector("[data-export-download-feedback]");
        if (!feedback) return;
        feedback.hidden = false;
        feedback.style.display = "";
    });
})();
