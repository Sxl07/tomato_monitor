/**
 * cloud_recovery.js — Manual cloud recovery UI controller (Spec 022, block F).
 *
 * Flow:
 *   button -> confirmation modal (+ password) -> POST /api/recovery/trigger
 *   -> spinner + disabled controls -> await the SAME POST response
 *   -> render summary -> reload only when the user dismisses the result AND
 *      entities were recovered.
 *
 * Recovery direction: nube -> SQLite local (import-missing-only, user-scoped,
 * no overwrite). This is DISTINCT from sync (local -> nube).
 *
 * No polling. No recovery-status endpoint. No background tasks. No secrets
 * stored: the password lives only in the input + POST body and is cleared in
 * every terminal path (success/error/cancel/close).
 */

(function () {
    "use strict";

    var recoveryBtn = document.getElementById("cloud-recovery-btn");
    var modal = document.getElementById("cloud-recovery-modal");
    var form = document.getElementById("cloud-recovery-form");
    var passwordInput = document.getElementById("recovery-password");
    var cancelBtn = document.getElementById("cloud-recovery-cancel-btn");
    var confirmBtn = document.getElementById("cloud-recovery-confirm-btn");
    var resultEl = document.getElementById("cloud-recovery-result");
    // Sync button (if present on the same screen) — disabled during recovery.
    var syncBtn = document.getElementById("remote-sync-btn");

    // Whether a reload should happen once the user dismisses the result.
    var pendingReload = false;

    function clearPassword() {
        if (passwordInput) passwordInput.value = "";
    }

    function openModal() {
        if (!modal) return;
        modal.style.display = "flex";
        if (passwordInput) passwordInput.focus();
    }

    function closeModal() {
        if (!modal) return;
        modal.style.display = "none";
        clearPassword();
    }

    // Ownership-aware blocking of the Sync button: recovery only disables Sync
    // if it was NOT already disabled (e.g. by an in-flight sync), and only
    // re-enables the button it itself disabled. This prevents recovery from
    // clobbering a disable owned by the sync controller.
    function setSyncBlockedByRecovery(blocked) {
        if (!syncBtn) return;
        if (blocked) {
            if (!syncBtn.disabled) {
                syncBtn.disabled = true;
                syncBtn.dataset.disabledByRecovery = "true";
            }
            return;
        }
        if (syncBtn.dataset.disabledByRecovery === "true") {
            syncBtn.disabled = false;
            delete syncBtn.dataset.disabledByRecovery;
        }
    }

    function setBusy(busy) {
        if (recoveryBtn) {
            recoveryBtn.disabled = busy;
            recoveryBtn.textContent = busy
                ? "Recuperando datos..."
                : "Recuperar datos desde la nube";
        }
        if (confirmBtn) {
            confirmBtn.disabled = busy;
            confirmBtn.textContent = busy ? "Recuperando datos..." : "Recuperar";
        }
        if (cancelBtn) cancelBtn.disabled = busy;
        if (passwordInput) passwordInput.disabled = busy;
        // Prevent the user from starting a sync while recovery is in flight,
        // without clobbering a disable owned by the sync controller.
        setSyncBlockedByRecovery(busy);
    }

    function showResult(html, cls) {
        if (!resultEl) return;
        resultEl.innerHTML =
            '<div class="' + cls + '" role="alert">' + html +
            '<div style="margin-top: 12px;">' +
            '<button type="button" id="cloud-recovery-result-accept" ' +
            'class="btn btn-primary" style="min-height: 44px;">Aceptar</button>' +
            "</div></div>";
        resultEl.style.display = "";
        var acceptBtn = document.getElementById("cloud-recovery-result-accept");
        if (acceptBtn) {
            acceptBtn.addEventListener("click", acceptResult);
            acceptBtn.focus();
        }
    }

    function acceptResult() {
        // Explicit accept: reload only when a recovery imported data; otherwise
        // just dismiss the result. Applies to error messages too (dismiss only).
        if (pendingReload) {
            pendingReload = false;
            window.location.reload();
            return;
        }
        hideResult();
    }

    function hideResult() {
        if (resultEl) {
            resultEl.style.display = "none";
            resultEl.innerHTML = "";
        }
    }

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    function pluralRecords(n) {
        return n === 1 ? "registro" : "registros";
    }

    function pluralImages(n) {
        return n === 1 ? "imagen" : "imágenes";
    }

    function buildSummary(data) {
        // Primary counters.
        var lines = [];
        lines.push(
            data.entities_recovered + " " + pluralRecords(data.entities_recovered) + " recuperados."
        );
        if (data.entities_reused > 0) {
            lines.push(
                data.entities_reused + " " + pluralRecords(data.entities_reused) + " ya existían."
            );
        }
        if (data.images_downloaded > 0) {
            lines.push(
                data.images_downloaded + " " + pluralImages(data.images_downloaded) + " descargadas."
            );
        }

        // Secondary counters (only when relevant).
        var extra = [];
        if (data.entities_skipped > 0) {
            extra.push(
                data.entities_skipped + " " + pluralRecords(data.entities_skipped) + " omitidos."
            );
        }
        if (data.conflicts > 0) {
            extra.push(
                data.conflicts + (data.conflicts === 1 ? " conflicto detectado." : " conflictos detectados.")
            );
        }
        if (data.images_skipped > 0) {
            extra.push(
                data.images_skipped + " " + pluralImages(data.images_skipped) + " omitidas."
            );
        }
        if (data.images_failed > 0) {
            extra.push(
                data.images_failed + " " +
                (data.images_failed === 1
                    ? "imagen no pudo recuperarse."
                    : "imágenes no pudieron recuperarse.")
            );
        }

        var isPartial = data.success === false;
        var title = isPartial
            ? "Recuperación completada parcialmente"
            : "Recuperación completada";
        var cls = isPartial ? "alert alert-warning" : "alert alert-info";

        var html = "<strong>" + title + "</strong>";
        if (isPartial) {
            html +=
                "<p style=\"margin: 8px 0 0;\">Parte de la información fue " +
                "recuperada, pero algunos elementos no pudieron procesarse. " +
                "Puedes volver a intentarlo más tarde.</p>";
        }
        html += "<ul style=\"margin: 8px 0 0; padding-left: 20px;\">";
        lines.forEach(function (l) {
            html += "<li>" + escapeHtml(l) + "</li>";
        });
        extra.forEach(function (l) {
            html += "<li>" + escapeHtml(l) + "</li>";
        });
        html += "</ul>";

        return { html: html, cls: cls };
    }

    function mapErrorDetail(status, detail) {
        if (detail) return escapeHtml(detail);
        if (status === 400) return "Configuración incompleta.";
        if (status === 401) return "Contraseña incorrecta.";
        if (status === 403) return "Identidad remota no coincide.";
        if (status === 409) return "Operación bloqueada.";
        return "No fue posible completar la recuperación.";
    }

    function handleSuccess(data) {
        var summary = buildSummary(data);
        showResult(summary.html, summary.cls);
        // A reload is warranted only when local data actually changed.
        pendingReload = data.entities_recovered > 0;
    }

    function triggerRecovery() {
        var password = passwordInput ? passwordInput.value : "";
        if (!password) {
            // Keep the modal open; prompt inside the modal is implicit.
            if (passwordInput) passwordInput.focus();
            return;
        }

        hideResult();
        setBusy(true);

        fetch("/api/recovery/trigger", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: password }),
        })
            .then(function (r) {
                if (r.ok) {
                    return r.json().then(function (data) {
                        return { ok: true, data: data };
                    });
                }
                return r
                    .json()
                    .then(function (data) {
                        return { ok: false, status: r.status, data: data };
                    })
                    .catch(function () {
                        return { ok: false, status: r.status, data: {} };
                    });
            })
            .then(function (result) {
                closeModal(); // also clears the password
                if (result.ok) {
                    handleSuccess(result.data);
                } else {
                    showResult(
                        mapErrorDetail(result.status, result.data.detail),
                        "alert alert-error"
                    );
                }
            })
            .catch(function () {
                closeModal();
                showResult(
                    "No se pudo conectar con el servicio de recuperación.",
                    "alert alert-error"
                );
            })
            .finally(function () {
                clearPassword();
                setBusy(false);
            });
    }

    // --- Wiring ---

    if (recoveryBtn) {
        recoveryBtn.addEventListener("click", function () {
            hideResult();
            openModal();
        });
    }

    if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
            closeModal();
        });
    }

    if (form) {
        form.addEventListener("submit", function (ev) {
            ev.preventDefault();
            triggerRecovery();
        });
    }
    // NOTE: the result is dismissed/reloaded ONLY via the explicit "Aceptar"
    // button rendered inside showResult(); a generic click on the result box
    // never triggers a reload.
})();
