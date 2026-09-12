/**
 * monitoring.js — Polling module for the Monitoring Execution Screen.
 *
 * Public API:
 *   startMonitoringPolling(monitoringId, options)
 *   stopMonitoringPolling()
 *
 * Slow polling cycle every 2 seconds:
 *   1. GET /monitoring/{id}/status — live counters, status blocks, warning banners
 *   2. GET /api/monitoring/{id}/log?since=... — activity log entries (incremental)
 *   3. GET /api/monitoring/{id}/last-snapshot — last thumbnail (when not recording)
 *
 * Spec 023: the recording preview is a continuous MJPEG stream (an <img> whose
 * src points to GET /api/monitoring/{id}/preview-stream) shown while the session
 * is "running". There is NO fast frame-polling loop; the browser renders the
 * stream directly. The stream never opens a camera and does not change
 * recording_target_fps.
 *
 * Also manages:
 *   - Elapsed time counter (MM:SS) updating every second
 *   - Auto-redirect to report on completed status
 *   - Temperature warning banner
 *   - Analysis progress (X/Y frames) during analyzing state
 *
 * Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 5.3, 5.9, 5.10, 6.1,
 *              6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 9.3, 10.3, 12.3, 14.2, 14.3, 14.4
 */

(function () {
    "use strict";

    // --- Module-level state ---
    var pollingIntervalId = null;
    var elapsedTimerId = null;
    var elapsedSeconds = 0;
    var currentMonitoringId = null;
    var currentStatus = null;
    var lastLogTimestamp = null;
    var lastSnapshotBlobUrl = null;
    // --- Configuration ---
    var POLL_INTERVAL_MS = 2000;
    var TEMPERATURE_THRESHOLD = 70;

    // Spec 023: the recording preview is a continuous MJPEG stream rendered by an
    // <img>. There is NO fast frame-polling loop anymore. We only toggle the
    // <img> src based on the monitoring state (running vs not running).
    var recordingPreviewStreaming = false;
    // A single pending retry timer (NOT a polling interval). On stream error we
    // schedule at most one delayed reconnection while still "running".
    var previewRetryTimeoutId = null;

    // Terminal states that stop polling
    var TERMINAL_STATES = ["completed", "aborted", "error"];

    // All possible status values for toggling DOM blocks
    var ALL_STATUSES = [
        "initializing",
        "running",
        "paused",
        "finishing",
        // Spec 020: capture finished, analysis pending, manual start.
        "ready_for_analysis",
        "analyzing",
        "completed",
        "aborted",
        "error"
    ];

    // Log level CSS class mapping
    var LOG_LEVEL_CLASSES = {
        info: "log-entry--info",
        success: "log-entry--success",
        warning: "log-entry--warning",
        error: "log-entry--error"
    };

    // --- Public API ---

    /**
     * Start polling the monitoring status endpoint.
     * @param {number} monitoringId - The monitoring session ID.
     * @param {object} [options] - Optional configuration overrides.
     * @param {number} [options.interval] - Polling interval in ms (default 2000).
     * @param {number} [options.temperatureThreshold] - Temp threshold in °C (default 70).
     */
    function startMonitoringPolling(monitoringId, options) {
        // Always tear down any prior run first. Calling this unconditionally
        // (not only when pollingIntervalId is set) guarantees repeated calls
        // can never leave a duplicate preview loop, slow interval, or
        // visibility/unload listener registered.
        stopMonitoringPolling();

        currentMonitoringId = monitoringId;
        currentStatus = null;
        lastLogTimestamp = null;
        elapsedSeconds = 0;
        // Preview state (guard, controller, generation) was already reset by
        // the stopMonitoringPolling() call above via stopPreviewLoop(), which
        // aborts any in-flight request and advances the generation token.

        if (options) {
            if (typeof options.interval === "number" && options.interval > 0) {
                POLL_INTERVAL_MS = options.interval;
            }
            if (typeof options.temperatureThreshold === "number") {
                TEMPERATURE_THRESHOLD = options.temperatureThreshold;
            }
        }

        // Poll immediately, then set interval
        pollCycle(currentMonitoringId);
        pollingIntervalId = setInterval(function () {
            pollCycle(currentMonitoringId);
        }, POLL_INTERVAL_MS);

        // Stop polling when page is hidden or unloaded
        document.addEventListener("visibilitychange", handleVisibilityChange);
        window.addEventListener("beforeunload", stopMonitoringPolling);
    }

    /**
     * Stop the polling interval and clean up listeners.
     */
    function stopMonitoringPolling() {
        if (pollingIntervalId !== null) {
            clearInterval(pollingIntervalId);
            pollingIntervalId = null;
        }
        stopPreviewLoop();
        stopElapsedTimer();
        document.removeEventListener("visibilitychange", handleVisibilityChange);
        window.removeEventListener("beforeunload", stopMonitoringPolling);
    }

    /**
     * Attach the recording preview MJPEG stream to the <img>. Idempotent: if the
     * <img> is already pointing at this monitoring's stream, do NOT reassign src
     * (that would restart the connection). Never opens a camera.
     */
    function startPreviewLoop() {
        var imgEl = document.getElementById("recording-preview-img");
        var placeholderEl = document.getElementById("recording-preview-placeholder");
        if (!imgEl) return;
        var expected = "/api/monitoring/" + currentMonitoringId + "/preview-stream";
        // Idempotent: only (re)assign if not already streaming this monitoring.
        var current = imgEl.getAttribute("src") || "";
        if (recordingPreviewStreaming && current.indexOf(expected) === 0) {
            return;
        }
        // On stream error while running, schedule ONE delayed reconnection with
        // a cache-buster (NOT frame polling, NOT an interval). At most one retry
        // timer is pending at a time.
        imgEl.onerror = function () {
            if (currentStatus !== "running") return;
            if (previewRetryTimeoutId !== null) return; // a retry is already pending
            previewRetryTimeoutId = setTimeout(function () {
                previewRetryTimeoutId = null;
                if (currentStatus === "running") {
                    imgEl.src = "/api/monitoring/" + currentMonitoringId +
                        "/preview-stream?t=" + Date.now();
                }
            }, 800);
        };
        imgEl.onload = function () {
            imgEl.style.display = "block";
            if (placeholderEl) placeholderEl.style.display = "none";
        };
        imgEl.src = expected;
        recordingPreviewStreaming = true;
    }

    /**
     * Detach the recording preview stream. Idempotent. Clearing the src closes
     * the MJPEG connection and cancels any pending retry timer.
     */
    function stopPreviewLoop() {
        if (previewRetryTimeoutId !== null) {
            clearTimeout(previewRetryTimeoutId);
            previewRetryTimeoutId = null;
        }
        var imgEl = document.getElementById("recording-preview-img");
        if (imgEl) {
            imgEl.onerror = null;
            imgEl.onload = null;
            imgEl.removeAttribute("src");
        }
        recordingPreviewStreaming = false;
    }

    // --- Polling cycle (max 3 requests) ---

    /**
     * Execute one polling cycle: status + log + last-snapshot.
     * @param {number} monitoringId
     */
    async function pollCycle(monitoringId) {
        // Request 1: Status
        await pollStatus(monitoringId);

        // Only poll log/thumbnail if not in a terminal state. The live recording
        // preview is NOT fetched here — it runs on its own ~5 fps loop (managed
        // by state transitions) so the slow 2 s cycle stays at status + log.
        if (TERMINAL_STATES.indexOf(currentStatus) === -1) {
            // Request 2: Activity log
            pollLog(monitoringId);
            // Request 3: last-snapshot thumbnail only when NOT recording and NOT
            // analyzing (during recording the fast preview loop shows the frame).
            if (currentStatus !== "running" && currentStatus !== "analyzing") {
                pollLastSnapshot(monitoringId);
            }
        }
    }

    /**
     * Enable/disable the fast recording-preview loop based on the current
     * status. Called on every status update so the loop starts when a recording
     * begins and stops as soon as it leaves the "running" state.
     */
    function syncPreviewLoop() {
        if (currentStatus === "running" &&
            TERMINAL_STATES.indexOf(currentStatus) === -1) {
            startPreviewLoop();
        } else {
            stopPreviewLoop();
        }
    }

    // --- Status polling ---

    /**
     * Fetch the monitoring status and update the UI.
     * @param {number} monitoringId
     */
    async function pollStatus(monitoringId) {
        try {
            var response = await fetch("/monitoring/" + monitoringId + "/status");
            if (!response.ok) {
                showErrorBanner("Error al consultar el estado del monitoreo.");
                return;
            }
            var data = await response.json();
            hideErrorBanner();
            updateExecutionUI(data);
        } catch (error) {
            // Network error — server may be restarting
            showErrorBanner("Sin conexión con el servidor. Reintentando...");
        }
    }

    /**
     * Update DOM counters and delegate state transitions.
     * @param {object} data - JSON response from the status endpoint.
     */
    function updateExecutionUI(data) {
        // Update live counters
        var snapshotsEl = document.getElementById("counter-snapshots");
        var tomatoesEl = document.getElementById("counter-tomatoes");

        if (snapshotsEl && typeof data.total_snapshots !== "undefined") {
            snapshotsEl.textContent = data.total_snapshots;
        }
        if (tomatoesEl && typeof data.total_detections !== "undefined") {
            tomatoesEl.textContent = data.total_detections;
        }

        // Handle temperature warning
        handleTemperatureWarning(data);

        // Handle error message display
        if (data.error_message) {
            var errorMsgEl = document.getElementById("error-message");
            if (errorMsgEl) {
                errorMsgEl.textContent = data.error_message;
            }
        }

        // Handle pause reason display
        if (data.pause_reason) {
            var pauseReasonEl = document.getElementById("pause-reason");
            if (pauseReasonEl) {
                pauseReasonEl.textContent = data.pause_reason;
            }
        }

        // Always update analysis progress (even without state change)
        updateAnalysisProgress(data);

        // Update thermal state during analysis
        updateAnalysisThermalState(data);

        // Handle state transition
        if (data.status && data.status !== currentStatus) {
            handleStateTransition(data.status);
            currentStatus = data.status;
        }

        // Start/stop the fast recording-preview loop to match the current state.
        syncPreviewLoop();
    }

    // --- Analysis progress ---

    /**
     * Update the analysis progress UI elements.
     * @param {object} data - Status response data with analysis_processed and analysis_total.
     */
    function updateAnalysisProgress(data) {
        var processed = parseInt(data.analysis_processed, 10);
        var total = parseInt(data.analysis_total, 10);

        // Normalize invalid values
        if (isNaN(processed) || processed < 0) processed = 0;
        if (isNaN(total) || total < 0) total = 0;
        // Clamp processed to total when total > 0
        if (total > 0 && processed > total) processed = total;

        var textEl = document.getElementById("analysis-progress-text");
        var processedEl = document.getElementById("analysis-processed");
        var totalEl = document.getElementById("analysis-total");
        var fillEl = document.getElementById("analysis-progress-fill");

        if (total <= 0) {
            if (textEl) textEl.textContent = "Procesando video...";
            if (processedEl) processedEl.textContent = "0";
            if (totalEl) totalEl.textContent = "0";
            if (fillEl) {
                fillEl.style.width = "0%";
                fillEl.setAttribute("aria-valuenow", "0");
            }
        } else {
            var pct = Math.floor((processed / total) * 100);
            if (pct < 0) pct = 0;
            if (pct > 100) pct = 100;

            if (textEl) textEl.textContent = "Procesando video... (" + processed + "/" + total + ")";
            if (processedEl) processedEl.textContent = String(processed);
            if (totalEl) totalEl.textContent = String(total);
            if (fillEl) {
                fillEl.style.width = pct + "%";
                fillEl.setAttribute("aria-valuenow", String(pct));
            }
        }
    }

    // --- Analysis thermal state ---

    /**
     * Show or hide the analysis thermal alert based on response data.
     * @param {object} data - Status response data with analysis_thermal_paused and related fields.
     */
    function updateAnalysisThermalState(data) {
        var alertEl = document.getElementById("analysis-thermal-alert");
        var textEl = document.getElementById("analysis-thermal-text");
        var statsEl = document.getElementById("analysis-thermal-stats");
        var progressTextEl = document.getElementById("analysis-progress-text");

        if (!alertEl) return;

        if (data.analysis_thermal_paused === true) {
            alertEl.classList.remove("hidden");

            // Set pause reason text
            var pauseText = data.pause_reason
                ? data.pause_reason
                : "Pausado por temperatura. Esperando que la Raspberry Pi se enfríe para continuar el análisis.";
            if (textEl) {
                textEl.textContent = pauseText;
            }

            // Update progress text to show paused state
            if (progressTextEl) {
                var total = parseInt(data.analysis_total, 10);
                var processed = parseInt(data.analysis_processed, 10);
                if (!isNaN(total) && total > 0 && !isNaN(processed)) {
                    progressTextEl.textContent = "Pausado por temperatura... (" + processed + "/" + total + ")";
                }
            }

            // Show temperature stats
            if (statsEl) {
                if (typeof data.temperature === "number" && data.temperature >= 0) {
                    statsEl.textContent = "Temperatura actual: " + data.temperature.toFixed(1) + "°C";
                } else if (typeof data.analysis_peak_temperature_c === "number" && data.analysis_peak_temperature_c > 0) {
                    statsEl.textContent = "Temperatura máxima observada: " + data.analysis_peak_temperature_c.toFixed(1) + "°C";
                } else {
                    statsEl.textContent = "";
                }
            }
        } else {
            alertEl.classList.add("hidden");
            if (statsEl) {
                statsEl.textContent = "";
            }
        }
    }

    // --- Monitoring actions disable/enable ---

    /**
     * Disable or enable all monitoring action buttons.
     * @param {boolean} disabled
     */
    function setMonitoringActionsDisabled(disabled) {
        var buttons = document.querySelectorAll("[data-monitoring-action]");
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].disabled = !!disabled;
        }
    }

    // --- Activity log polling ---

    /**
     * Fetch new log entries and render them in the activity log panel.
     * @param {number} monitoringId
     */
    async function pollLog(monitoringId) {
        try {
            var url = "/api/monitoring/" + monitoringId + "/log";
            if (lastLogTimestamp) {
                url += "?since=" + encodeURIComponent(lastLogTimestamp);
            }
            var response = await fetch(url);
            if (!response.ok) return;

            var entries = await response.json();
            if (!Array.isArray(entries) || entries.length === 0) return;

            // Update lastLogTimestamp with the most recent entry
            lastLogTimestamp = entries[entries.length - 1].timestamp;

            // Render entries into the activity log panel
            renderLogEntries(entries);
        } catch (error) {
            // Silently ignore log fetch errors — non-critical
        }
    }

    /**
     * Render log entries into the activity log panel DOM.
     * @param {Array} entries - Array of {timestamp, level, source, message}
     */
    function renderLogEntries(entries) {
        var panel = document.getElementById("activity-log-panel");
        if (!panel) return;

        for (var i = 0; i < entries.length; i++) {
            var entry = entries[i];
            var div = document.createElement("div");
            var levelClass = LOG_LEVEL_CLASSES[entry.level] || "log-entry--info";
            div.className = "log-entry " + levelClass;

            // Timestamp (HH:MM:SS)
            var timeSpan = document.createElement("span");
            timeSpan.className = "log-entry-time";
            timeSpan.textContent = formatLogTimestamp(entry.timestamp);

            // Level indicator (color dot)
            var levelSpan = document.createElement("span");
            levelSpan.className = "log-entry-level";
            levelSpan.setAttribute("aria-hidden", "true");

            // Message
            var msgSpan = document.createElement("span");
            msgSpan.className = "log-entry-message";
            msgSpan.textContent = entry.message;

            div.appendChild(timeSpan);
            div.appendChild(levelSpan);
            div.appendChild(msgSpan);
            panel.appendChild(div);
        }

        // Auto-scroll to bottom
        panel.scrollTop = panel.scrollHeight;
    }

    /**
     * Format an ISO timestamp to HH:MM:SS for display.
     * @param {string} isoTimestamp
     * @returns {string}
     */
    function formatLogTimestamp(isoTimestamp) {
        try {
            var date = new Date(isoTimestamp);
            var h = String(date.getHours()).padStart(2, "0");
            var m = String(date.getMinutes()).padStart(2, "0");
            var s = String(date.getSeconds()).padStart(2, "0");
            return h + ":" + m + ":" + s;
        } catch (e) {
            return "--:--:--";
        }
    }

    // --- Last snapshot polling ---

    /**
     * Fetch the last snapshot image and update the thumbnail.
     * @param {number} monitoringId
     */
    async function pollLastSnapshot(monitoringId) {
        try {
            var response = await fetch("/api/monitoring/" + monitoringId + "/last-snapshot");
            if (!response.ok) {
                // 404 means no snapshot yet — show placeholder
                return;
            }

            var blob = await response.blob();
            // Revoke previous blob URL to free memory
            if (lastSnapshotBlobUrl) {
                URL.revokeObjectURL(lastSnapshotBlobUrl);
            }
            lastSnapshotBlobUrl = URL.createObjectURL(blob);

            var imgEl = document.getElementById("last-snapshot-img");
            var placeholderEl = document.getElementById("last-snapshot-placeholder");
            if (imgEl) {
                imgEl.src = lastSnapshotBlobUrl;
                imgEl.style.display = "block";
            }
            if (placeholderEl) {
                placeholderEl.style.display = "none";
            }
        } catch (error) {
            // Silently ignore — non-critical, placeholder remains
        }
    }

    // --- Recording preview (video-first, Spec 023) ---
    // The preview is a continuous MJPEG stream rendered by the browser via an
    // <img> whose src points to /api/monitoring/{id}/preview-stream. There is NO
    // frame-polling here anymore; startPreviewLoop()/stopPreviewLoop() only
    // attach/detach the <img> src. See those functions above.

    // --- Elapsed time counter ---

    /**
     * Start the elapsed time counter (increments every second).
     */
    function startElapsedTimer() {
        if (elapsedTimerId !== null) return; // Already running
        elapsedSeconds = 0;
        updateElapsedDisplay();
        elapsedTimerId = setInterval(function () {
            elapsedSeconds++;
            updateElapsedDisplay();
        }, 1000);
    }

    /**
     * Stop the elapsed time counter.
     */
    function stopElapsedTimer() {
        if (elapsedTimerId !== null) {
            clearInterval(elapsedTimerId);
            elapsedTimerId = null;
        }
    }

    /**
     * Update the elapsed timer display in MM:SS format.
     */
    function updateElapsedDisplay() {
        var el = document.getElementById("elapsed-timer");
        if (el) {
            el.textContent = formatElapsedTime(elapsedSeconds);
        }
    }

    /**
     * Format seconds as MM:SS string.
     * @param {number} totalSeconds
     * @returns {string}
     */
    function formatElapsedTime(totalSeconds) {
        var minutes = Math.floor(totalSeconds / 60);
        var seconds = totalSeconds % 60;
        return String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
    }

    // --- State transitions ---

    /**
     * React to monitoring state changes: toggle status blocks,
     * manage elapsed timer, auto-redirect on completion, stop polling on terminal states.
     * @param {string} newStatus
     */
    function handleStateTransition(newStatus) {
        // Toggle visibility of status blocks
        ALL_STATUSES.forEach(function (status) {
            var block = document.getElementById("status-" + status);
            if (block) {
                if (status === newStatus) {
                    block.classList.remove("hidden");
                } else {
                    block.classList.add("hidden");
                }
            }
        });

        // Also hide overlays when transitioning to a real state
        var finalizingOverlay = document.getElementById("finalizing-capture-overlay");
        if (finalizingOverlay) { finalizingOverlay.classList.add("hidden"); }
        var stoppingOverlay = document.getElementById("stopping-overlay");
        if (stoppingOverlay) { stoppingOverlay.classList.add("hidden"); }

        // Manage elapsed timer based on status
        if (newStatus === "running") {
            startElapsedTimer();
            setMonitoringActionsDisabled(false);
        } else if (newStatus === "paused") {
            stopElapsedTimer();
            setMonitoringActionsDisabled(false);
        } else if (newStatus === "analyzing") {
            stopElapsedTimer();
            setMonitoringActionsDisabled(true);
        } else {
            stopElapsedTimer();
        }

        // Auto-redirect on completed
        if (newStatus === "completed") {
            stopMonitoringPolling();
            window.location.href = "/monitoreos/" + currentMonitoringId + "/reporte";
            return;
        }

        // Stop polling on terminal states
        if (TERMINAL_STATES.indexOf(newStatus) !== -1) {
            stopMonitoringPolling();
        }
    }

    // --- Temperature warning ---

    /**
     * Show or hide the temperature warning banner.
     * @param {object} data - Status response data.
     */
    function handleTemperatureWarning(data) {
        var warningEl = document.getElementById("temp-warning");
        var warningTextEl = document.getElementById("temp-warning-text");

        if (!warningEl) return;

        if (typeof data.temperature === "number" && data.temperature > TEMPERATURE_THRESHOLD) {
            warningEl.classList.remove("hidden");
            if (warningTextEl) {
                warningTextEl.textContent =
                    "Temperatura alta: " + data.temperature.toFixed(1) + "°C — El sistema puede pausarse";
            }
        } else {
            warningEl.classList.add("hidden");
        }
    }

    // --- Error/reconnection banner ---

    /**
     * Show a reconnection/error banner at the top of the page.
     * @param {string} message
     */
    function showErrorBanner(message) {
        var banner = document.getElementById("polling-error-banner");
        if (!banner) {
            banner = document.createElement("div");
            banner.id = "polling-error-banner";
            banner.className = "alert alert--warning";
            banner.setAttribute("role", "alert");

            var icon = document.createElement("span");
            icon.className = "alert-icon";
            icon.textContent = "⚠️";

            var text = document.createElement("span");
            text.id = "polling-error-text";

            banner.appendChild(icon);
            banner.appendChild(text);

            var main = document.querySelector(".main-content");
            if (main && main.firstChild) {
                main.insertBefore(banner, main.firstChild);
            } else if (main) {
                main.appendChild(banner);
            } else {
                document.body.appendChild(banner);
            }
        }

        var textEl = document.getElementById("polling-error-text");
        if (textEl) {
            textEl.textContent = message;
        }
        banner.classList.remove("hidden");
    }

    /**
     * Hide the error/reconnection banner.
     */
    function hideErrorBanner() {
        var banner = document.getElementById("polling-error-banner");
        if (banner) {
            banner.classList.add("hidden");
        }
    }

    // --- Visibility handling ---

    /**
     * Handle page visibility changes — pause polling when hidden, resume when visible.
     */
    function handleVisibilityChange() {
        if (document.hidden) {
            if (pollingIntervalId !== null) {
                clearInterval(pollingIntervalId);
                pollingIntervalId = null;
            }
            // Also stop the fast preview loop while hidden — no point fetching
            // frames the operator cannot see, and it avoids background load.
            stopPreviewLoop();
            stopElapsedTimer();
        } else {
            if (currentMonitoringId !== null && pollingIntervalId === null) {
                pollCycle(currentMonitoringId);
                pollingIntervalId = setInterval(function () {
                    pollCycle(currentMonitoringId);
                }, POLL_INTERVAL_MS);
                if (currentStatus === "running") {
                    startElapsedTimer();
                }
                // Restore the fast preview loop ONLY if still recording.
                syncPreviewLoop();
            }
        }
    }

    // --- Expose public API on window ---
    window.startMonitoringPolling = startMonitoringPolling;
    window.stopMonitoringPolling = stopMonitoringPolling;
    window.formatElapsedTime = formatElapsedTime;
    window.updateAnalysisProgress = updateAnalysisProgress;
    window.updateAnalysisThermalState = updateAnalysisThermalState;
    window.setMonitoringActionsDisabled = setMonitoringActionsDisabled;

})();
