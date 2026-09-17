/* Spec 024 — Analytical Dashboard UI behaviour.
 *
 * PRESENTATION ONLY. No fetch, no network, no external libraries, no CDN.
 * Responsibilities:
 *   1. Tab switching (show one tabpanel at a time, aria state).
 *   2. Selector auto-submit (greenhouse/module -> GET querystring).
 *   3. Lightweight inline-SVG renderer for per-module temporal series.
 *
 * The renderer performs VISUAL geometry only (scaling, points, lines, axes,
 * legend). It never recomputes analytics values; series come pre-computed from
 * the backend and are read from each chart's data-series attribute (JSON).
 */
(function () {
    "use strict";

    // Distinct, color-blind-friendly palette for multiple module series.
    // Presentation only; legend + labels ensure we never rely on color alone.
    var SERIES_COLORS = [
        "#1976D2", "#388E3C", "#F57C00", "#7B1FA2",
        "#C2185B", "#00838F", "#5D4037", "#455A64"
    ];

    var SVG_NS = "http://www.w3.org/2000/svg";

    // ---- Tabs ----------------------------------------------------------
    function initTabs() {
        var tablist = document.querySelector(".analytics-tabs");
        if (!tablist) return;
        var tabs = Array.prototype.slice.call(
            tablist.querySelectorAll('[role="tab"]')
        );

        function activate(tab) {
            tabs.forEach(function (t) {
                var selected = t === tab;
                t.setAttribute("aria-selected", selected ? "true" : "false");
                t.tabIndex = selected ? 0 : -1;
                var panel = document.getElementById(t.getAttribute("aria-controls"));
                if (panel) {
                    if (selected) {
                        panel.removeAttribute("hidden");
                    } else {
                        panel.setAttribute("hidden", "");
                    }
                }
            });
            // (Re)render charts of the now-visible panel so SVG sizes correctly.
            renderAllCharts();
        }

        tabs.forEach(function (tab, index) {
            tab.addEventListener("click", function () { activate(tab); });
            tab.addEventListener("keydown", function (e) {
                var dir = 0;
                if (e.key === "ArrowRight") dir = 1;
                else if (e.key === "ArrowLeft") dir = -1;
                else return;
                e.preventDefault();
                var next = tabs[(index + dir + tabs.length) % tabs.length];
                next.focus();
                activate(next);
            });
        });
    }

    // ---- Selector auto-submit ------------------------------------------
    function initSelectors() {
        var form = document.getElementById("analytics-filters");
        if (!form) return;
        var ghSelect = form.querySelector('[data-role="greenhouse-select"]');
        var modSelect = form.querySelector('[data-role="module-select"]');

        if (ghSelect) {
            ghSelect.addEventListener("change", function () {
                // Changing greenhouse resets module scope to "all".
                if (modSelect) modSelect.value = "all";
                form.submit();
            });
        }
        if (modSelect) {
            modSelect.addEventListener("change", function () { form.submit(); });
        }
    }

    // ---- SVG series renderer -------------------------------------------
    function parseSeries(el) {
        var raw = el.getAttribute("data-series");
        if (!raw) return [];
        try {
            return JSON.parse(raw) || [];
        } catch (err) {
            return [];
        }
    }

    function niceMax(v) {
        if (v <= 0) return 1;
        var pow = Math.pow(10, Math.floor(Math.log10(v)));
        var n = Math.ceil(v / pow) * pow;
        return n;
    }

    function computeYDomain(series, mode) {
        if (mode === "pct") return [0, 100];
        if (mode === "index") return [0, 1];
        var max = 0;
        series.forEach(function (s) {
            s.points.forEach(function (p) {
                if (p.value > max) max = p.value;
            });
        });
        return [0, niceMax(max)];
    }

    function svgEl(name, attrs) {
        var node = document.createElementNS(SVG_NS, name);
        Object.keys(attrs || {}).forEach(function (k) {
            node.setAttribute(k, attrs[k]);
        });
        return node;
    }

    function tsOf(point) {
        // Real timestamp (ms) from the ISO started_at; NaN if unparseable.
        return Date.parse(point.ts);
    }

    function renderChart(container) {
        // Do not render hidden panels (0-width); they render on activation.
        if (container.offsetParent === null) return;
        if (container.getAttribute("data-rendered") === "1") return;

        var allSeries = parseSeries(container);
        // Only series that actually have points participate in domains, paths,
        // points and legend.
        var series = allSeries.filter(function (s) {
            return s.points && s.points.length > 0;
        });
        var empty = container.querySelector(".analytics-chart__empty");
        if (series.length === 0) {
            if (empty) empty.style.display = "";
            return;
        }
        if (empty) empty.style.display = "none";

        var mode = container.getAttribute("data-y-mode") || "auto";
        var width = Math.max(container.clientWidth || 320, 280);
        var height = 220;
        var pad = { top: 16, right: 12, bottom: 34, left: 40 };
        var innerW = width - pad.left - pad.right;
        var innerH = height - pad.top - pad.bottom;

        // X domain: REAL time. Collect every point's timestamp across all
        // (non-empty) series so gaps and different date ranges are respected.
        var allTs = [];
        series.forEach(function (s) {
            s.points.forEach(function (p) {
                var t = tsOf(p);
                if (!isNaN(t)) allTs.push(t);
            });
        });
        var xMin = Math.min.apply(null, allTs);
        var xMax = Math.max.apply(null, allTs);

        var yDomain = computeYDomain(series, mode);
        var yMin = yDomain[0], yMax = yDomain[1];

        function xPos(point) {
            var t = tsOf(point);
            if (isNaN(t) || xMax === xMin) return pad.left + innerW / 2;
            return pad.left + ((t - xMin) / (xMax - xMin)) * innerW;
        }
        function yPos(v) {
            var f = (v - yMin) / (yMax - yMin || 1);
            return pad.top + innerH - f * innerH;
        }

        var svg = svgEl("svg", {
            viewBox: "0 0 " + width + " " + height,
            preserveAspectRatio: "xMidYMid meet",
            role: "img"
        });
        svg.setAttribute(
            "aria-label",
            container.getAttribute("aria-label") || "Gráfica analítica"
        );

        // Axes
        svg.appendChild(svgEl("line", {
            x1: pad.left, y1: pad.top, x2: pad.left, y2: pad.top + innerH,
            stroke: "#BDBDBD", "stroke-width": "1"
        }));
        svg.appendChild(svgEl("line", {
            x1: pad.left, y1: pad.top + innerH, x2: pad.left + innerW, y2: pad.top + innerH,
            stroke: "#BDBDBD", "stroke-width": "1"
        }));

        // Y ticks (min, mid, max)
        [yMin, (yMin + yMax) / 2, yMax].forEach(function (val) {
            var y = yPos(val);
            var label = mode === "index" ? val.toFixed(1) : Math.round(val).toString();
            var t = svgEl("text", {
                x: pad.left - 6, y: y + 4, "text-anchor": "end",
                "font-size": "11", fill: "#616161"
            });
            t.textContent = label;
            svg.appendChild(t);
        });

        // Series: one polyline + points per module, positioned on the REAL
        // time axis. Points are drawn in chronological order within each
        // module; modules are never joined to each other.
        series.forEach(function (s, si) {
            var color = SERIES_COLORS[si % SERIES_COLORS.length];
            var pts = s.points.slice().sort(function (a, b) {
                return (tsOf(a) || 0) - (tsOf(b) || 0);
            });
            if (pts.length > 1) {
                var d = pts.map(function (p, i) {
                    return (i === 0 ? "M" : "L") + xPos(p) + " " + yPos(p.value);
                }).join(" ");
                svg.appendChild(svgEl("path", {
                    d: d, fill: "none", stroke: color, "stroke-width": "2"
                }));
            }
            pts.forEach(function (p) {
                var c = svgEl("circle", {
                    cx: xPos(p), cy: yPos(p.value), r: "3.5", fill: color
                });
                var title = svgEl("title", {});
                title.textContent = s.module_name + " — " + p.label + ": " +
                    (mode === "index" ? p.value.toFixed(2) : (mode === "pct" ? p.value.toFixed(1) + " %" : Math.round(p.value)));
                c.appendChild(title);
                svg.appendChild(c);
            });
        });

        // Temporal X labels derived from real dates: min, mid (if it adds
        // value), max. Kept minimal so 800x480 stays uncluttered.
        function labelForTs(t) {
            // Reuse an existing point label when a point sits exactly on t;
            // otherwise format the timestamp as dd/mm.
            for (var i = 0; i < series.length; i++) {
                var found = series[i].points.filter(function (p) { return tsOf(p) === t; })[0];
                if (found) return found.label;
            }
            var d = new Date(t);
            return ("0" + d.getDate()).slice(-2) + "/" + ("0" + (d.getMonth() + 1)).slice(-2);
        }
        var xTicks;
        if (xMax === xMin) {
            xTicks = [{ t: xMin, anchor: "middle", x: pad.left + innerW / 2 }];
        } else {
            var mid = (xMin + xMax) / 2;
            xTicks = [
                { t: xMin, anchor: "start", x: pad.left },
                { t: mid, anchor: "middle", x: pad.left + innerW / 2 },
                { t: xMax, anchor: "end", x: pad.left + innerW }
            ];
        }
        xTicks.forEach(function (tick) {
            var txt = svgEl("text", {
                x: tick.x, y: pad.top + innerH + 16, "text-anchor": tick.anchor,
                "font-size": "11", fill: "#616161"
            });
            txt.textContent = labelForTs(tick.t);
            svg.appendChild(txt);
        });

        // Replace any previous SVG, keep the empty placeholder node.
        var old = container.querySelector("svg");
        if (old) old.parentNode.removeChild(old);
        var oldLegend = container.querySelector(".analytics-chart__legend");
        if (oldLegend) oldLegend.parentNode.removeChild(oldLegend);
        container.insertBefore(svg, container.firstChild);

        // Legend when more than one series.
        if (series.length > 1) {
            var legend = document.createElement("div");
            legend.className = "analytics-chart__legend";
            series.forEach(function (s, si) {
                var item = document.createElement("span");
                item.className = "analytics-chart__legend-item";
                var sw = document.createElement("span");
                sw.className = "analytics-chart__legend-swatch";
                sw.style.backgroundColor = SERIES_COLORS[si % SERIES_COLORS.length];
                item.appendChild(sw);
                item.appendChild(document.createTextNode(s.module_name));
                legend.appendChild(item);
            });
            container.appendChild(legend);
        }

        container.setAttribute("data-rendered", "1");
    }

    function renderAllCharts() {
        var charts = document.querySelectorAll(".analytics-chart[data-chart]");
        Array.prototype.forEach.call(charts, renderChart);
    }

    function init() {
        initTabs();
        initSelectors();
        renderAllCharts();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
