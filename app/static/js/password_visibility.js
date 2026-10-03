/** Toggle the visibility of each password input without retaining its value. */
(function () {
    "use strict";

    document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
        var input = document.getElementById(button.getAttribute("aria-controls"));
        if (!input || input.type !== "password") return;

        button.addEventListener("click", function () {
            var visible = input.type === "password";
            input.type = visible ? "text" : "password";
            button.setAttribute("aria-label", visible ? "Ocultar contraseña" : "Mostrar contraseña");
            button.setAttribute("aria-pressed", visible ? "true" : "false");
        });
    });
})();
