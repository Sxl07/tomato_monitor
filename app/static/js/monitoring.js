/**
 * monitoring.js — Polling module for the Monitoring Execution Screen.
 *
 * Public API:
 *   startMonitoringPolling(monitoringId, options)
 *   stopMonitoringPolling()
 *
 * Polls every 2 seconds (max 3 requests per cycle):
 *   1. GET /monitoring/{id}/status — live counters, status blocks, warning banners
 *   2. GET /api/monitoring/{id}/log?since=... — activity log entries (incremental)
 *   3. GET /api/monitoring/{id}/last-snapshot — last captured image thumbnail
 *
 * Also manages:
 *   - Elapsed time counter (MM:SS) updating every second
 *   - Auto-redirect to report on completed status
 *   - Temperature warning banner
 *   - Analysis progress (X/Y snapshots) during analyzing state
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

    // Terminal states that stop polling
    var TERMINAL_STATES = ["completed", "aborted", "error"];

    // All possible status values for toggling DOM blocks
    var ALL_STATUSES = [
        "initializing",
        "running",
        "paused",
        "finishing",
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
        if (pollingIntervalId !== null) {
            stopMonitoringPolling();
        }

        currentMonitoringId = monitoringId;
        currentStatus = null;
        lastLogTimestamp = null;
        elapsedSeconds = 0;

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
        stopElapsedTimer();
        document.removeEventListener("visibilitychange", handleVisibilityChange);
        window.removeEventListener("beforeunload", stopMonitoringPolling);
    }

    // --- Polling cycle (max 3 requests) ---

    /**
     * Execute one polling cycle: status + log + last-snapshot.
     * @param {number} monitoringId
     */
    async function pollCycle(monitoringId) {
        // Request 1: Status
        await pollStatus(monitoringId);

        // Only poll log and snapshot if not in a terminal state
        if (TERMINAL_STATES.indexOf(currentStatus) === -1) {
            // Request 2: Activity log
            pollLog(monitoringId);
            // Request 3: Last snapshot (only during capture, not analysis)
            if (currentStatus !== "analyzing") {
                pollLastSnapshot(monitoringId);
            }
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

        // Handle state transition
        if (data.status && data.status !== currentStatus) {
            handleStateTransition(data.status);
            currentStatus = data.status;
        }
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
            if (textEl) textEl.textContent = "Preparando análisis...";
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

            if (textEl) textEl.textContent = "Analizando snapshots... (" + processed + "/" + total + ")";
            if (processedEl) processedEl.textContent = String(processed);
            if (totalEl) totalEl.textContent = String(total);
            if (fillEl) {
                fillEl.style.width = pct + "%";
                fillEl.setAttribute("aria-valuenow", String(pct));
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
            }
        }
    }

    // --- Expose public API on window ---
    window.startMonitoringPolling = startMonitoringPolling;
    window.stopMonitoringPolling = stopMonitoringPolling;
    window.formatElapsedTime = formatElapsedTime;
    window.updateAnalysisProgress = updateAnalysisProgress;
    window.setMonitoringActionsDisabled = setMonitoringActionsDisabled;

})();
