/**
 * remote_sync.js — Manual remote sync UI controller.
 *
 * Handles:
 * - Loading initial remote sync status on page load
 * - Triggering sync via POST /api/sync/trigger
 * - Temporary polling during active sync for progress
 * - Displaying results and errors
 * - Clearing the password after a successful sync; failed attempts remain retryable
 *
 * No secrets stored. No permanent intervals. No auto-sync.
 */

(function () {
    "use strict";

    // --- DOM references ---
    var statusEl = document.getElementById("remote-sync-status");
    var progressEl = document.getElementById("remote-sync-progress");
    var phaseEl = document.getElementById("remote-sync-phase");
    var counterEl = document.getElementById("remote-sync-counter");
    var resultEl = document.getElementById("remote-sync-result");
    var formEl = document.getElementById("remote-sync-form");
    var passwordInput = document.getElementById("sync-password");
    var syncBtn = document.getElementById("remote-sync-btn");
    var recoveryBtn = document.getElementById("cloud-recovery-btn");

    // --- State ---
    var pollingId = null;

    // --- Helpers ---

    // Ownership-aware blocking of the Recovery button: sync only disables it if
    // it was NOT already disabled (e.g. by an in-flight recovery), and only
    // re-enables the button it itself disabled. This mirrors the recovery
    // controller's ownership of the Sync button so neither clobbers the other.
    function setRecoveryBlockedBySync(blocked) {
        if (!recoveryBtn) return;
        if (blocked) {
            if (!recoveryBtn.disabled) {
                recoveryBtn.disabled = true;
                recoveryBtn.dataset.disabledBySync = "true";
            }
            return;
        }
        if (recoveryBtn.dataset.disabledBySync === "true") {
            recoveryBtn.disabled = false;
            delete recoveryBtn.dataset.disabledBySync;
        }
    }

    function fetchStatus() {
        return fetch("/api/sync/status", { credentials: "same-origin" })
            .then(function (r) {
                if (!r.ok) throw new Error("Status fetch failed");
                return r.json();
            });
    }

    function renderStatus(data) {
        if (!statusEl) return;
        var html = '<div class="dashboard-metrics" style="margin-bottom: 0;">';
        html += '<div class="metric-card"><span class="metric-value">' + data.pending_count + '</span><span class="metric-label">Pendientes</span></div>';
        html += '<div class="metric-card"><span class="metric-value">' + data.synced_count + '</span><span class="metric-label">Sincronizados</span></div>';
        html += '<div class="metric-card"><span class="metric-value">' + data.error_count + '</span><span class="metric-label">Errores</span></div>';
        html += '</div>';

        if (data.last_sync_at) {
            html += '<p class="text-secondary" style="margin-top: 8px;">Última sincronización: ' + formatDate(data.last_sync_at) + '</p>';
        }

        if (!data.user_has_remote_id) {
            html += '<p class="alert alert-warning" style="margin-top: 8px;">Tu cuenta no tiene identificador remoto. Registra tu cuenta primero.</p>';
            if (formEl) formEl.style.display = "none";
        } else {
            if (formEl) formEl.style.display = "";
        }

        statusEl.innerHTML = html;

        // Reconcile Recovery availability with the known sync state so that a
        // page opened while another sync is active reflects the real state.
        // Only a sync-owned disable is released here; a recovery-owned disable
        // is left untouched.
        setRecoveryBlockedBySync(data.is_syncing === true);
    }

    function formatDate(isoStr) {
        if (!isoStr) return "N/A";
        try {
            var d = new Date(isoStr);
            return d.toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" });
        } catch (e) {
            return isoStr;
        }
    }

    function showProgress(phase, processed, total) {
        if (!progressEl) return;
        progressEl.style.display = "";
        if (phaseEl) phaseEl.textContent = " " + (phase || "");
        if (counterEl && total > 0) {
            counterEl.textContent = " (" + processed + "/" + total + ")";
        } else {
            counterEl.textContent = "";
        }
    }

    function hideProgress() {
        if (progressEl) progressEl.style.display = "none";
    }

    function showResult(message, isError) {
        if (!resultEl) return;
        var cls = isError ? "alert alert-error" : "alert alert-info";
        resultEl.innerHTML = '<div class="' + cls + '" role="alert">' + message + '</div>';
        resultEl.style.display = "";
    }

    function hideResult() {
        if (resultEl) {
            resultEl.style.display = "none";
            resultEl.innerHTML = "";
        }
    }

    function setButtonEnabled(enabled) {
        if (!syncBtn) return;
        syncBtn.disabled = !enabled;
        syncBtn.textContent = enabled ? "Sincronizar ahora" : "Sincronizando...";
    }

    function clearPassword() {
        if (passwordInput) passwordInput.value = "";
    }

    function startPolling() {
        if (pollingId) return;
        pollingId = setInterval(function () {
            fetchStatus()
                .then(function (data) {
                    if (data.is_syncing && data.current_progress) {
                        showProgress(
                            data.current_progress.phase,
                            data.current_progress.processed,
                            data.current_progress.total
                        );
                    } else if (!data.is_syncing) {
                        stopPolling();
                    }
                })
                .catch(function () { /* ignore polling errors */ });
        }, 1000);
    }

    function stopPolling() {
        if (pollingId) {
            clearInterval(pollingId);
            pollingId = null;
        }
        hideProgress();
    }

    function mapErrorDetail(status, detail) {
        if (detail) return detail;
        if (status === 400) return "Configuración incompleta.";
        if (status === 401) return "Contraseña incorrecta.";
        if (status === 403) return "Identidad remota no coincide.";
        if (status === 409) return "Operación bloqueada.";
        return "Error inesperado durante la sincronización.";
    }

    // --- Main action ---

    function triggerSync() {
        var password = passwordInput ? passwordInput.value : "";
        if (!password) {
            showResult("Ingresa tu contraseña para continuar.", true);
            return;
        }

        hideResult();
        setButtonEnabled(false);
        setRecoveryBlockedBySync(true);
        startPolling();

        fetch("/api/sync/trigger", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: password }),
        })
            .then(function (r) {
                if (r.ok) return r.json().then(function (data) { return { ok: true, data: data }; });
                return r.json().then(function (data) { return { ok: false, status: r.status, data: data }; })
                    .catch(function () { return { ok: false, status: r.status, data: {} }; });
            })
            .then(function (result) {
                stopPolling();
                setButtonEnabled(true);
                setRecoveryBlockedBySync(false);

                if (result.ok) {
                    var d = result.data;
                    var msg = "Sincronización completada: " +
                        d.entities_synced + " entidades sincronizadas";
                    if (d.entities_failed > 0) {
                        msg += ", " + d.entities_failed + " con error";
                    }
                    if (d.deletions_synced > 0) {
                        msg += ", " + d.deletions_synced +
                            (d.deletions_synced === 1
                                ? " eliminación sincronizada"
                                : " eliminaciones sincronizadas");
                    }
                    if (d.deletions_failed > 0) {
                        msg += ", " + d.deletions_failed +
                            (d.deletions_failed === 1
                                ? " eliminación con error"
                                : " eliminaciones con error");
                    }
                    if (d.images_uploaded > 0) {
                        msg += ", " + d.images_uploaded + " imágenes subidas";
                    }
                    msg += " (" + d.duration_seconds.toFixed(1) + "s).";
                    showResult(msg, !d.success);
                    if (d.success) clearPassword();
                } else {
                    var detail = mapErrorDetail(result.status, result.data.detail);
                    showResult(detail, true);
                }

                // Refresh status
                fetchStatus().then(renderStatus).catch(function () {});
            })
            .catch(function () {
                stopPolling();
                setButtonEnabled(true);
                setRecoveryBlockedBySync(false);
                showResult("No se pudo conectar al servidor.", true);
            });
    }

    // --- Init ---

    if (syncBtn) {
        syncBtn.addEventListener("click", triggerSync);
    }

    // Load initial status
    fetchStatus()
        .then(renderStatus)
        .catch(function () {
            if (statusEl) statusEl.innerHTML = '<p class="text-secondary">No se pudo cargar el estado remoto.</p>';
        });

})();
