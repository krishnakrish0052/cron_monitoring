(function () {
    var root = document.getElementById("monitoring-dashboard");
    if (!root) return;

    var selectedCode = null;
    var selectedRun = null;
    var lastLive = null;
    var lastOverview = null;
    var cronFilter = {text: "", status: "all"};
    var inflight = {};
    var istFormatter = new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZoneName: "short"
    });

    function $(id) {
        return document.getElementById(id);
    }

    function esc(value) {
        var div = document.createElement("div");
        div.textContent = value == null ? "" : value;
        return div.innerHTML;
    }

    function attr(value) {
        return esc(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function errorBanner(message) {
        return '<div class="monitoring-error-banner" role="alert">' +
            '<span class="error-icon" aria-hidden="true">!</span>' +
            '<span class="error-text">' + esc(message || "Unknown monitoring error") + '</span>' +
        '</div>';
    }

    function showError(id, message) {
        var element = $(id);
        if (!element) return;
        if (element.tagName === "TBODY") {
            element.innerHTML = '<tr><td colspan="99">' + errorBanner(message) + '</td></tr>';
            return;
        }
        if (element.tagName === "PRE") {
            element.textContent = message || "Unknown monitoring error";
            element.classList.add("monitoring-error-banner");
            return;
        }
        if (element.namespaceURI === "http://www.w3.org/2000/svg") {
            element.textContent = message || "Unknown monitoring error";
            return;
        }
        element.innerHTML = errorBanner(message);
    }

    function clearError(id) {
        var element = $(id);
        if (!element) return;
        if (element.tagName === "PRE" && element.classList.contains("monitoring-error-banner")) {
            element.classList.remove("monitoring-error-banner");
            element.textContent = "";
            return;
        }
        Array.prototype.forEach.call(element.querySelectorAll(".monitoring-error-banner"), function (banner) {
            banner.remove();
        });
    }

    function responseJson(response) {
        if (!response.ok) {
            throw new Error("HTTP " + response.status + " while loading monitoring data");
        }
        return response.json();
    }

    function getCookie(name) {
        var value = "; " + document.cookie;
        var parts = value.split("; " + name + "=");
        if (parts.length === 2) return decodeURIComponent(parts.pop().split(";").shift());
        return "";
    }

    function toMs(value) {
        if (!value) return NaN;
        if (typeof value === "number") return value > 100000000000 ? value : value * 1000;
        return Date.parse(value);
    }

    function formatIST(value) {
        var ms = toMs(value);
        if (!Number.isFinite(ms)) return "-";
        return istFormatter.format(new Date(ms));
    }

    function timeAgo(value) {
        var ms = toMs(value);
        if (!Number.isFinite(ms)) return "Never";
        var seconds = Math.max(0, Math.floor((Date.now() - ms) / 1000));
        if (seconds < 60) return seconds + "s ago";
        if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
        if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
        return Math.floor(seconds / 86400) + "d ago";
    }

    function timeUntil(value) {
        var ms = toMs(value);
        if (!Number.isFinite(ms)) return "";
        var seconds = Math.floor((ms - Date.now()) / 1000);
        if (seconds < -60) return "due " + timeAgo(value);
        if (seconds <= 60) return "due now";
        if (seconds < 3600) return "in " + Math.ceil(seconds / 60) + "m";
        if (seconds < 86400) return "in " + Math.ceil(seconds / 3600) + "h";
        return "in " + Math.ceil(seconds / 86400) + "d";
    }

    function formatNumber(value, digits) {
        if (value == null || !Number.isFinite(Number(value))) return "-";
        return Number(value).toFixed(digits == null ? 1 : digits);
    }

    function formatBytes(value) {
        if (value == null || !Number.isFinite(Number(value))) return "-";
        var units = ["B", "KB", "MB", "GB", "TB"];
        var size = Number(value);
        var index = 0;
        while (size >= 1024 && index < units.length - 1) {
            size = size / 1024;
            index += 1;
        }
        return size.toFixed(index === 0 ? 0 : 1) + " " + units[index];
    }

    function formatSeconds(value) {
        if (value == null || !Number.isFinite(Number(value))) return "-";
        var seconds = Math.floor(Number(value));
        if (seconds < 60) return seconds + "s";
        if (seconds < 3600) return Math.floor(seconds / 60) + "m " + (seconds % 60) + "s";
        return Math.floor(seconds / 3600) + "h " + Math.floor((seconds % 3600) / 60) + "m";
    }

    function cronopsWorkerCoverage(hodl) {
        var coverage = (hodl.worker_coverage || []);
        var running = coverage.filter(function (lane) {
            return (lane.status || "").toLowerCase() === "running";
        });
        var unavailable = coverage.filter(function (lane) {
            return (lane.status || "").toLowerCase() !== "running";
        });
        return {
            total: coverage.length,
            running: running.length,
            unavailable: unavailable,
            unavailableNames: unavailable.map(function (lane) { return lane.queue_name || "unknown"; })
        };
    }

    function statsFromValues(values) {
        if (!values.length) return {latest: null, min: null, max: null, avg: null};
        var total = values.reduce(function (sum, value) { return sum + value; }, 0);
        return {
            latest: values[values.length - 1],
            min: Math.min.apply(null, values),
            max: Math.max.apply(null, values),
            avg: total / values.length
        };
    }

    function drawChart(svg, points, options) {
        if (!svg) return;
        options = options || {};
        points = points || [];
        var width = 760;
        var height = options.small ? 150 : 220;
        var pad = {top: 18, right: 22, bottom: 34, left: 54};
        var valid = points.map(function (point, index) {
            var value = typeof point === "number" ? Number(point) : Number(point.value);
            var ts = typeof point === "number" ? null : point.ts;
            return {index: index, value: value, ts: ts, ts_ist: point.ts_ist};
        }).filter(function (point) {
            return Number.isFinite(point.value);
        });

        if (!valid.length) {
            svg.setAttribute("viewBox", "0 0 " + width + " " + height);
            svg.innerHTML = '<text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#6b7280">No data yet</text>';
            return;
        }

        var values = valid.map(function (point) { return point.value; });
        var min = options.min != null ? options.min : Math.min.apply(null, values);
        var max = options.max != null ? options.max : Math.max.apply(null, values);
        if (min === max) {
            max = min + 1;
            min = Math.max(0, min - 1);
        }
        if (options.percent) {
            min = 0;
            max = Math.max(100, max);
        }

        var graphWidth = width - pad.left - pad.right;
        var graphHeight = height - pad.top - pad.bottom;
        var path = valid.map(function (point, index) {
            var x = pad.left + (valid.length === 1 ? graphWidth : (index / (valid.length - 1)) * graphWidth);
            var y = pad.top + graphHeight - ((point.value - min) / (max - min)) * graphHeight;
            return (index ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
        }).join(" ");
        var fillPath = path + " L" + (pad.left + graphWidth) + "," + (pad.top + graphHeight) + " L" + pad.left + "," + (pad.top + graphHeight) + " Z";
        var unit = options.unit || "";
        var color = options.color || "#00e5ff";
        var first = valid[0];
        var last = valid[valid.length - 1];
        var xStart = first.ts ? formatIST(first.ts * 1000) : "";
        var xEnd = last.ts ? formatIST(last.ts * 1000) : "";

        svg.setAttribute("viewBox", "0 0 " + width + " " + height);
        svg.innerHTML =
            '<g class="chart-grid">' +
            '<line x1="' + pad.left + '" y1="' + pad.top + '" x2="' + pad.left + '" y2="' + (pad.top + graphHeight) + '"></line>' +
            '<line x1="' + pad.left + '" y1="' + (pad.top + graphHeight) + '" x2="' + (pad.left + graphWidth) + '" y2="' + (pad.top + graphHeight) + '"></line>' +
            '<line x1="' + pad.left + '" y1="' + (pad.top + graphHeight / 2) + '" x2="' + (pad.left + graphWidth) + '" y2="' + (pad.top + graphHeight / 2) + '"></line>' +
            '<text x="' + (pad.left - 9) + '" y="' + (pad.top + 5) + '" text-anchor="end">' + esc(formatNumber(max, 0) + unit) + '</text>' +
            '<text x="' + (pad.left - 9) + '" y="' + (pad.top + graphHeight) + '" text-anchor="end">' + esc(formatNumber(min, 0) + unit) + '</text>' +
            '<text x="' + pad.left + '" y="' + (height - 7) + '">' + esc(xStart) + '</text>' +
            '<text x="' + (pad.left + graphWidth) + '" y="' + (height - 7) + '" text-anchor="end">' + esc(xEnd) + '</text>' +
            '</g>' +
            '<path d="' + fillPath + '" fill="' + color + '" opacity="0.16"></path>' +
            '<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="3" vector-effect="non-scaling-stroke"></path>' +
            '<circle cx="' + (pad.left + graphWidth) + '" cy="' + (pad.top + graphHeight - ((last.value - min) / (max - min)) * graphHeight).toFixed(1) + '" r="5" fill="' + color + '"></circle>';
    }

    function eventHtml(event) {
        var severity = event.severity || "info";
        var data = event.data || {};
        var extra = "";
        if (event.type === "http_response") {
            var cls = data.classification || {};
            extra = (data.status_code ? "HTTP " + data.status_code + " " : "") + (cls.type || "");
        } else if (event.type === "db_query") {
            extra = (data.operation || "SQL") + " " + (data.table || "") + " " + formatNumber(data.duration_seconds, 3) + "s";
        } else if (event.type === "python_trace") {
            extra = (data.function || "") + " line " + (data.line || "-");
        }
        return '<div class="trace-event ' + esc(severity) + '">' +
            '<div><span class="trace-type">' + esc(event.type || "event") + '</span> ' +
            '<span class="trace-time">' + esc(formatIST(event.at_ist || event.at_utc)) + '</span></div>' +
            '<strong>' + esc(event.message || "") + '</strong>' +
            (extra ? '<small>' + esc(extra) + '</small>' : '') +
        '</div>';
    }

    function renderEventList(containerId, events, emptyText) {
        var container = $(containerId);
        if (!container) return;
        if (!events || !events.length) {
            container.innerHTML = '<div class="monitoring-muted">' + esc(emptyText) + '</div>';
            return;
        }
        container.innerHTML = events.slice(-30).reverse().map(eventHtml).join("");
    }

    function renderSummary(totals, hodlCronOps) {
        var items;
        if (hodlCronOps && hodlCronOps.total != null) {
            items = [
                ["HODL CronOps", hodlCronOps.total || 0, "info"],
                ["HODL Up", hodlCronOps.up || 0, "ok"],
                ["HODL Down", hodlCronOps.down || 0, (hodlCronOps.down || 0) ? "bad" : "ok"],
                ["HODL Late", hodlCronOps.grace || 0, (hodlCronOps.grace || 0) ? "warn" : "ok"],
                ["HODL New", hodlCronOps.new || 0, ""],
                ["All Healthchecks", totals.total || 0, "info"],
                ["Checks Down", totals.down || 0, (totals.down || 0) ? "bad" : "ok"],
                ["Checks Paused", totals.paused || 0, ""],
            ];
        } else {
            items = [
                ["Total", totals.total || 0, "info"],
                ["Up", totals.up || 0, "ok"],
                ["Down", totals.down || 0, "bad"],
                ["Late", totals.grace || 0, "warn"],
                ["New", totals.new || 0, ""],
                ["Paused", totals.paused || 0, ""],
            ];
        }
        $("monitoring-summary").innerHTML = items.map(function (item) {
            return '<div class="monitoring-card ' + esc(item[2]) + '"><div class="label-text">' +
                esc(item[0]) + '</div><div class="value">' + esc(item[1]) + '</div></div>';
        }).join("");
    }

    function expandPanel(panel) {
        if (!panel) return;
        panel.classList.remove("collapsed");
        var button = panel.querySelector(".monitoring-collapse-toggle");
        if (button) button.textContent = "Collapse";
    }

    function collapsePanel(panel) {
        if (!panel) return;
        panel.classList.add("collapsed");
        var button = panel.querySelector(".monitoring-collapse-toggle");
        if (button) button.textContent = "Expand";
    }

    function setPanelExpanded(panel, expanded) {
        if (expanded) expandPanel(panel);
        else collapsePanel(panel);
    }

    function initCollapsiblePanels() {
        Array.prototype.forEach.call(document.querySelectorAll(".monitoring-collapsible"), function (panel) {
            var button = panel.querySelector(".monitoring-collapse-toggle");
            if (!button || button.dataset.bound === "1") return;
            button.dataset.bound = "1";
            button.textContent = panel.classList.contains("collapsed") ? "Expand" : "Collapse";
        });
    }

    function renderActionCenter() {
        var overview = lastOverview || {};
        var live = lastLive || {};
        var totals = overview.totals || {};
        var hodlCronOps = overview.hodl_cronops_summary || {};
        var liveTotals = live.totals || {};
        var server = live.server || {};
        var hodl = live.hodl_cronops || {};
        var queueSummary = hodl.queue_summary || {};
        var workers = hodl.workers || [];
        var workerCoverage = cronopsWorkerCoverage(hodl);
        var spool = hodl.spool_summary || {};
        var down = hodlCronOps.total != null ? (hodlCronOps.down || 0) : (totals.down || 0);
        var waiting = hodlCronOps.total != null ? (hodlCronOps.new || 0) : (totals.new || 0);
        var running = liveTotals.running || 0;
        var stale = liveTotals.stale || 0;
        var queueBacklog = Number(queueSummary.queued || 0) +
            Number(queueSummary.waiting_for_capacity || 0) +
            Number(queueSummary.retrying || 0) +
            Number(queueSummary.deferred_by_pressure || 0);
        var automaticRecovery = Number(queueSummary.auto_recovery_pending || 0);
        var missedSla = Number(queueSummary.missed_sla_24h || 0);
        var completedRecovery = (hodl.recent_auto_recoveries || []).filter(function (recovery) {
            return cronOpsStatus(recovery.effective_status || recovery.status) === "success";
        }).length;
        var spoolPending = Number(spool.pending || 0);
        var spoolFailed = Number(spool.failed || 0);
        var externalErrors = (live.external_errors || []).length;
        var orphanCount = (live.orphans || []).length;
        var serverCpu = Number(server.cpu_percent || 0);
        var serverRam = Number(server.memory_percent || 0);
        var pressure = Math.max(serverCpu, serverRam);
        var pressureText = "CPU " + formatNumber(serverCpu, 1) + "% · RAM " + formatNumber(serverRam, 1) + "%";
        var updatedAt = live.generated_at_ist || overview.generated_at_ist || new Date().toISOString();
        var cards = [
            {
                label: "HODL Failed / Down",
                value: down,
                note: down ? "Open Cron Tables filtered by Down." : "No down checks right now.",
                state: down ? "bad" : "ok"
            },
            {
                label: "Running Now",
                value: running,
                note: running ? "Live observer is tracking active processes." : "No cron currently active.",
                state: stale ? "warn" : "ok"
            },
            {
                label: "CronOps Queue",
                value: queueBacklog,
                note: "Queued " + Number(queueSummary.queued || 0) +
                    " · waiting " + Number(queueSummary.waiting_for_capacity || 0) +
                    " · retrying " + Number(queueSummary.retrying || 0),
                state: queueBacklog >= 20 ? "bad" : queueBacklog ? "warn" : "ok"
            },
            {
                label: "Financial Recovery",
                value: automaticRecovery ? automaticRecovery + " active" : completedRecovery,
                note: automaticRecovery ?
                    "Snapshot-backed financial recovery is in progress." :
                    (completedRecovery ? completedRecovery + " completed recovery run(s) in the last 24 hours." : "No recent financial recovery runs."),
                state: automaticRecovery ? "warn" : "ok"
            },
            {
                label: "Lane Workers",
                value: workerCoverage.running + "/" + workerCoverage.total,
                note: workerCoverage.total ?
                    (workerCoverage.unavailable.length ? "Missing: " + workerCoverage.unavailableNames.join(", ") : "All expected CronOps lanes are live.") :
                    "CronOps worker coverage is unavailable.",
                state: workerCoverage.unavailable.length || !workerCoverage.total ? "warn" : "ok"
            },
            {
                label: "DB Spool",
                value: spoolPending,
                note: spoolFailed ? spoolFailed + " replay failures need review." : "Local DB-outage trigger buffer.",
                state: spoolPending || spoolFailed ? "bad" : "ok"
            },
            {
                label: "External API",
                value: externalErrors,
                note: externalErrors ? "Explorer/API failures found in traces." : "No recent explorer/API errors.",
                state: externalErrors ? "bad" : "ok"
            },
            {
                label: "Untracked Processes",
                value: orphanCount,
                note: orphanCount ? "Review orphan table before killing anything." : "No orphan cron processes.",
                state: orphanCount ? "warn" : "ok"
            },
            {
                label: "Server Pressure",
                value: pressureText,
                note: "Live 1s sample from observer.",
                state: pressure >= 85 ? "bad" : pressure >= 70 ? "warn" : "ok"
            },
            {
                label: "HODL Waiting First Run",
                value: waiting,
                note: waiting ? "Scheduled jobs that have not reached first due time." : "No waiting-first-run checks.",
                state: waiting ? "warn" : "ok"
            }
        ];
        var grid = $("monitoring-action-grid");
        if (grid) {
            grid.innerHTML = cards.map(function (card) {
                return '<div class="action-card ' + esc(card.state) + '">' +
                    '<span>' + esc(card.label) + '</span>' +
                    '<strong>' + esc(card.value) + '</strong>' +
                    '<small>' + esc(card.note) + '</small>' +
                '</div>';
            }).join("");
        }
        var updated = $("monitoring-action-updated");
        if (updated) updated.textContent = "Updated " + formatIST(updatedAt);
    }

    function applyCronFilter() {
        var rows = document.querySelectorAll(".monitoring-cron-row");
        var text = (cronFilter.text || "").trim().toLowerCase();
        Array.prototype.forEach.call(rows, function (row) {
            var statusOk = cronFilter.status === "all" || row.dataset.status === cronFilter.status;
            var textOk = !text || (row.dataset.search || "").indexOf(text) !== -1;
            row.style.display = statusOk && textOk ? "" : "none";
        });
        Array.prototype.forEach.call(document.querySelectorAll(".project-panel"), function (panel) {
            var visible = Array.prototype.some.call(panel.querySelectorAll(".monitoring-cron-row"), function (row) {
                return row.style.display !== "none";
            });
            panel.style.display = visible ? "" : "none";
        });
    }

    function renderProjects(projects) {
        $("monitoring-projects").innerHTML = projects.map(function (project) {
            var rows = project.checks.map(function (check) {
                var search = [
                    project.name,
                    check.name,
                    check.tags,
                    check.schedule,
                    check.status_label || check.status
                ].join(" ").toLowerCase();
                var scheduleCell = '<code>' + esc(check.schedule || "-") + '</code>';
                if (check.next_due_ist || check.next_due) {
                    scheduleCell += '<br><small class="monitoring-subtext">Next ' + esc(timeUntil(check.next_due || check.next_due_ist)) + '</small>';
                    scheduleCell += '<br><small class="monitoring-subtext">' + esc(formatIST(check.next_due_ist || check.next_due)) + '</small>';
                }
                var lastPingCell = check.last_ping ? esc(timeAgo(check.last_ping)) : 'Waiting first run';
                if (check.last_ping) {
                    lastPingCell += '<br><small class="monitoring-subtext">' + esc(formatIST(check.last_ping_ist || check.last_ping)) + '</small>';
                } else if (check.next_due) {
                    lastPingCell += '<br><small class="monitoring-subtext">Expected ' + esc(timeUntil(check.next_due)) + '</small>';
                }
                return '<tr class="monitoring-cron-row" data-status="' + attr(check.status) + '" data-search="' + attr(search) + '">' +
                    '<td><span class="monitoring-status ' + esc(check.status) + '">' + esc(check.status_label || check.status) + '</span></td>' +
                    '<td><strong>' + esc(check.name) + '</strong><br><small class="monitoring-muted">' + esc(check.tags) + '</small></td>' +
                    '<td>' + scheduleCell + '</td>' +
                    '<td>' + lastPingCell + '</td>' +
                    '<td>' + esc(check.last_duration == null ? "-" : formatSeconds(check.last_duration)) + '</td>' +
                    '<td><button class="btn monitoring-mini-btn monitoring-graph" data-code="' + esc(check.code) + '">Inspect</button></td>' +
                    '<td><a class="btn monitoring-mini-btn" href="' + esc(check.details_url) + '">Details</a></td>' +
                    '<td><a class="btn monitoring-mini-btn" href="' + esc(check.log_url) + '">Ping/Event Log</a></td>' +
                '</tr>';
            }).join("");

            var health = project.health || {};
            var healthLabel = health.status || "unknown";
            var cronopsSummary = project.cronops_summary;
            var cronopsText = "";
            if (cronopsSummary && cronopsSummary.total != null) {
                cronopsText = '<span class="monitoring-status ' + ((cronopsSummary.down || 0) ? "down" : "up") + '">' +
                    'CronOps ' + esc(cronopsSummary.total) + ': ' + esc(cronopsSummary.up || 0) + ' up · ' + esc(cronopsSummary.down || 0) + ' down · ' + esc(cronopsSummary.new || 0) + ' new</span>';
            }
            return '<div class="monitoring-panel project-panel">' +
                '<div class="monitoring-project-head">' +
                    '<h2>' + esc(project.name) + '</h2>' +
                    '<div class="monitoring-head-actions">' + cronopsText +
                    '<span class="monitoring-status ' + (healthLabel === "ok" ? "up" : "down") + '">External ' + esc(healthLabel) + '</span></div>' +
                '</div>' +
                '<div class="table-responsive five-row-table-wrap"><table class="table table-condensed monitoring-table">' +
                    '<thead><tr><th>Status</th><th>Name</th><th>Schedule</th><th>Last ping</th><th>Duration</th><th></th><th></th><th></th></tr></thead>' +
                    '<tbody>' + rows + '</tbody>' +
                '</table></div>' +
            '</div>';
        }).join("");

        Array.prototype.forEach.call(document.querySelectorAll(".monitoring-graph"), function (button) {
            button.addEventListener("click", function () {
                selectedCode = button.dataset.code;
                selectedRun = null;
                loadCheckSeries(selectedCode);
                loadRuns(selectedCode);
                loadCheckLive(selectedCode);
            });
        });
        applyCronFilter();
    }

    function renderMetricCard(key, metric) {
        var details = metric.details || {};
        var unit = metric.unit || "";
        var value = metric.current == null ? "-" : formatNumber(metric.current, unit === "%" ? 1 : 2) + unit;
        var detailHtml = "";

        if (key === "memory" || key === "disk") {
            detailHtml =
                '<div>Used <strong>' + esc(formatBytes(details.used_bytes)) + '</strong></div>' +
                '<div>Total <strong>' + esc(formatBytes(details.total_bytes)) + '</strong></div>' +
                '<div>Free <strong>' + esc(formatBytes(details.free_bytes)) + '</strong></div>';
        } else if (key === "cpu") {
            detailHtml =
                '<div>Live <strong>1s</strong></div>' +
                '<div>Avg <strong>' + esc(formatNumber(metric.avg, 1)) + esc(unit) + '</strong></div>' +
                '<div>Max <strong>' + esc(formatNumber(metric.max, 1)) + esc(unit) + '</strong></div>' +
                '<div>Load 1m <strong>' + esc(formatNumber(details.load1, 2)) + '</strong></div>' +
                '<div>Cores <strong>' + esc(formatNumber(details.cores, 0)) + '</strong></div>';
        } else {
            detailHtml =
                '<div>Total/hour <strong>' + esc(formatNumber(details.total_requests_window, 0)) + '</strong></div>' +
                '<div>Active <strong>' + esc(formatNumber(details.active_connections, 0)) + '</strong></div>';
        }

        return '<div class="monitoring-metric-card">' +
            '<div class="metric-card-top"><span>' + esc(metric.label) + '</span><strong>' + esc(value) + '</strong></div>' +
            '<svg id="infra-' + esc(key) + '" class="monitoring-sparkline" viewBox="0 0 760 150"></svg>' +
            '<div class="monitoring-statline">' +
                '<span>Live 1s <strong>' + esc(value) + '</strong></span>' +
                '<span>Max 1h <strong>' + esc(formatNumber(metric.max, 1)) + esc(unit) + '</strong></span>' +
            '</div>' +
            '<div class="metric-details">' + detailHtml + '</div>' +
        '</div>';
    }

    function loadOverview() {
        if (inflight.overview) return inflight.overview;
        inflight.overview = true;
        return fetch(root.dataset.overviewUrl, {credentials: "same-origin"})
            .then(responseJson)
            .then(function (data) {
                clearError("monitoring-projects");
                lastOverview = data;
                renderSummary(data.totals || {}, data.hodl_cronops_summary || null);
                renderProjects(data.projects || []);
                renderActionCenter();
            })
            .catch(function (err) {
                showError("monitoring-projects", "Overview data failed to load: " + err.message);
            })
            .finally(function () { inflight.overview = false; });
    }

    function loadInfrastructure() {
        if (inflight.infrastructure) return inflight.infrastructure;
        inflight.infrastructure = true;
        return fetch(root.dataset.infraUrl, {credentials: "same-origin"})
            .then(responseJson)
            .then(function (data) {
                clearError("monitoring-infra-grid");
                var metrics = data.metrics || {};
                var order = ["cpu", "memory", "disk", "nginx_requests"];
                $("monitoring-infra-grid").innerHTML = order.map(function (key) {
                    return renderMetricCard(key, metrics[key] || {label: key, series: []});
                }).join("");
                drawChart($("infra-cpu"), metrics.cpu && metrics.cpu.series, {small: true, percent: true, unit: "%", color: "#00f5d4"});
                drawChart($("infra-memory"), metrics.memory && metrics.memory.series, {small: true, percent: true, unit: "%", color: "#00b4ff"});
                drawChart($("infra-disk"), metrics.disk && metrics.disk.series, {small: true, percent: true, unit: "%", color: "#ffb000"});
                drawChart($("infra-nginx_requests"), metrics.nginx_requests && metrics.nginx_requests.series, {small: true, unit: "", color: "#f72585"});
            })
            .catch(function (err) {
                showError("monitoring-infra-grid", "Infrastructure metrics failed to load: " + err.message);
            })
            .finally(function () { inflight.infrastructure = false; });
    }

    function liveCronName(item) {
        var name = item.function || "unknown";
        return name.split(".").slice(-2).join(".");
    }

    function renderLiveSummary(data) {
        var totals = data.totals || {};
        var server = data.server || {};
        var hodl = data.hodl_cronops || {};
        var queueSummary = hodl.queue_summary || {};
        var workers = hodl.workers || [];
        var workerCoverage = cronopsWorkerCoverage(hodl);
        var spool = hodl.spool_summary || {};
        var queueBacklog = Number(queueSummary.queued || 0) +
            Number(queueSummary.waiting_for_capacity || 0) +
            Number(queueSummary.retrying || 0) +
            Number(queueSummary.deferred_by_pressure || 0);
        var automaticRecovery = Number(queueSummary.auto_recovery_pending || 0);
        var completedRecovery = (hodl.recent_auto_recoveries || []).filter(function (recovery) {
            return cronOpsStatus(recovery.effective_status || recovery.status) === "success";
        }).length;
        $("monitoring-live-clock").textContent = "IST " + (data.generated_at_ist ? formatIST(data.generated_at_ist) : "-");
        var updatedEl = $("monitoring-live-updated");
        if (updatedEl) updatedEl.textContent = "Last updated: " + (data.generated_at_ist ? formatIST(data.generated_at_ist) : "-");
        $("monitoring-live-summary").innerHTML = [
            ["Running crons", totals.running || 0],
            ["CronOps backlog", queueBacklog],
            ["Financial recovery", automaticRecovery + " active / " + completedRecovery + " complete"],
            ["Lane workers", workerCoverage.running + "/" + workerCoverage.total],
            ["DB spool", (spool.pending || 0) + " pending"],
            ["Cron procs", totals.processes || 0],
            ["Stale", totals.stale || 0],
            ["Cron CPU", formatNumber(totals.cpu_percent, 1) + "%"],
            ["Cron RAM", formatBytes(totals.rss_bytes || 0)],
            ["DB queries", totals.db_queries || 0],
            ["Slow DB", totals.slow_db_queries || 0],
            ["Server CPU", formatNumber(server.cpu_percent, 1) + "%"],
            ["Server RAM", formatNumber(server.memory_percent, 1) + "%"],
        ].map(function (item) {
            return '<div class="live-summary-card"><span>' + esc(item[0]) + '</span><strong>' + esc(item[1]) + '</strong></div>';
        }).join("");
    }

    function renderCronOpsLanes(data) {
        var hodl = data.hodl_cronops || {};
        var queueSummary = hodl.queue_summary || {};
        var queueRows = hodl.queue_by_queue || [];
        var workers = hodl.workers || [];
        var workerCoverage = cronopsWorkerCoverage(hodl);
        var spool = hodl.spool_summary || {};
        var ingestion = hodl.svr4plus_ingestion || {};
        var unresolvedIngestion = Number(ingestion.pending || 0) + Number(ingestion.dead_letter || 0);
        var runningWorkers = workerCoverage.running;
        var staleWorkers = workers.filter(function (worker) {
            return (worker.status || "").toLowerCase() === "stale";
        }).length;
        var backlog = Number(queueSummary.queued || 0) +
            Number(queueSummary.waiting_for_capacity || 0) +
            Number(queueSummary.retrying || 0) +
            Number(queueSummary.deferred_by_pressure || 0);
        var automaticRecovery = Number(queueSummary.auto_recovery_pending || 0);
        var missedSla = Number(queueSummary.missed_sla_24h || 0);
        var completedRecovery = (hodl.recent_auto_recoveries || []).filter(function (recovery) {
            return cronOpsStatus(recovery.effective_status || recovery.status) === "success";
        }).length;
        var cards = [
            ["Backlog", backlog, backlog ? "warn" : "ok"],
            ["Financial recovery", automaticRecovery + " active / " + completedRecovery + " complete", automaticRecovery ? "warn" : "ok"],
            ["Running", Number(queueSummary.running || 0), Number(queueSummary.running || 0) ? "ok" : ""],
            ["Workers", runningWorkers + "/" + workerCoverage.total, workerCoverage.unavailable.length || !workerCoverage.total ? "warn" : "ok"],
            ["Stale workers", staleWorkers, staleWorkers ? "warn" : "ok"],
            ["Missed SLA (24h)", missedSla, missedSla ? "warn" : "ok"],
            ["DB spool", Number(spool.pending || 0) + " pending", Number(spool.pending || 0) || Number(spool.failed || 0) ? "bad" : "ok"],
            ["Replay failed", Number(spool.failed || 0), Number(spool.failed || 0) ? "bad" : "ok"],
            ["SVR4+ review", unresolvedIngestion, unresolvedIngestion ? "warn" : "ok"]
        ];
        var cardsEl = $("monitoring-cronops-cards");
        if (cardsEl) {
            cardsEl.innerHTML = cards.map(function (card) {
                return '<div class="live-summary-card cronops-card ' + esc(card[2]) + '">' +
                    '<span>' + esc(card[0]) + '</span><strong>' + esc(card[1]) + '</strong>' +
                '</div>';
            }).join("");
        }
        var queuesEl = $("monitoring-cronops-queues");
        if (queuesEl) {
            queuesEl.innerHTML = queueRows.length ? queueRows.map(function (row) {
                return '<tr>' +
                    '<td><strong>' + esc(row.queue_name || "default") + '</strong></td>' +
                    '<td><span class="monitoring-status ' + esc(row.status || "") + '">' + esc((row.status || "-").replace(/_/g, " ")) + '</span></td>' +
                    '<td>' + esc(row.count || 0) + '</td>' +
                '</tr>';
            }).join("") : '<tr><td colspan="3" class="monitoring-muted">No queued CronOps triggers.</td></tr>';
        }
        var workersEl = $("monitoring-cronops-workers");
        if (workersEl) {
            workersEl.innerHTML = workers.length ? workers.slice(0, 16).map(function (worker) {
                var metadata = worker.metadata || {};
                var queues = metadata.queues && metadata.queues.length ? metadata.queues.join(", ") : "*";
                return '<tr>' +
                    '<td><strong>' + esc(queues) + '</strong></td>' +
                    '<td><span class="monitoring-status ' + esc(worker.status || "") + '">' + esc(worker.status || "-") + '</span></td>' +
                    '<td>' + esc(worker.pid || "-") + '</td>' +
                    '<td>' + esc(worker.active_jobs || 0) + '</td>' +
                    '<td>' + esc(formatSeconds(worker.heartbeat_age_seconds || 0)) + '</td>' +
                '</tr>';
            }).join("") : '<tr><td colspan="5" class="monitoring-muted">No CronOps workers found.</td></tr>';
        }
        var updated = $("monitoring-cronops-updated");
        if (updated) updated.textContent = "Updated " + (data.generated_at_ist ? formatIST(data.generated_at_ist) : "-");
        var ingestionEl = $("monitoring-svr4plus-ingestion");
        if (ingestionEl) {
            var streams = ingestion.streams || [];
            ingestionEl.innerHTML = streams.length ? streams.map(function (stream) {
                var result = stream.last_error_type || (stream.last_success_at ? "success" : "waiting");
                var reasons = (stream.dead_letter_reasons || []).map(function (reason) {
                    return String(reason.count || 0) + " " + String(reason.error_type || "unknown").replace(/_/g, " ");
                }).join(", ");
                return '<tr>' +
                    '<td><strong>' + esc(stream.key || "-") + '</strong><br><small>' + esc(stream.chain_id || "-") + '</small></td>' +
                    '<td>' + esc(stream.cursor_block != null ? stream.cursor_block : "-") + '</td>' +
                    '<td>' + esc(stream.finalized_tip != null ? stream.finalized_tip : "-") + '</td>' +
                    '<td>' + esc(stream.pending || 0) + '</td>' +
                    '<td>' + esc(stream.dead_letter || 0) + '</td>' +
                    '<td><span class="monitoring-status ' + esc(stream.last_error_type ? "down" : "up") + '">' + esc(result) + '</span>' +
                    (reasons ? '<br><small>' + esc(reasons) + '</small>' : '') +
                    '<br><small>' + esc(stream.last_success_at ? formatIST(stream.last_success_at) : formatIST(stream.last_scan_at)) + '</small></td>' +
                '</tr>';
            }).join("") : '<tr><td colspan="6" class="monitoring-muted">' +
                esc(ingestion.status === "unavailable" ? "Ingestion data is unavailable: " + (ingestion.error || "unknown error") : "No SVR4 Plus ingestion streams have run yet.") +
                '</td></tr>';
        }
    }

    function cronOpsStatus(status) {
        return String(status || "unknown").toLowerCase();
    }

    function cronOpsStatusClass(status) {
        status = cronOpsStatus(status);
        if (status === "success") return "success";
        if (["failed", "failure", "missed_sla", "stale", "timeout"].indexOf(status) !== -1) return "failure";
        if (["degraded", "running", "queued", "scheduled", "retrying", "waiting_for_capacity", "deferred_by_pressure"].indexOf(status) !== -1) return "warning";
        return "paused";
    }

    function cronOpsStatusLabel(status) {
        return cronOpsStatus(status).replace(/_/g, " ");
    }

    function cronOpsLatestRun(job) {
        return job.latest_run || {};
    }

    function hasCounter(counters, key) {
        return Boolean(counters) && Object.prototype.hasOwnProperty.call(counters, key) && counters[key] != null;
    }

    function formatWhole(value) {
        if (value == null || !Number.isFinite(Number(value))) return String(value == null ? "-" : value);
        return Math.round(Number(value)).toLocaleString("en-IN");
    }

    function cronOpsCounters(job) {
        var run = cronOpsLatestRun(job);
        var metadata = job.metadata || {};
        var counters = run.item_counters || metadata.result || {};
        return counters && typeof counters === "object" ? counters : {};
    }

    function cronOpsBusinessResult(job) {
        var run = cronOpsLatestRun(job);
        var counters = cronOpsCounters(job);
        var parts = [];
        var add = function (key, label) {
            if (hasCounter(counters, key)) parts.push(label + " " + formatWhole(counters[key]));
        };

        add("profiles_scanned", "profiles checked");
        add("investments_scanned", "investments checked");
        add("purchases", "purchases checked");
        add("rank_updates", "rank changes");
        add("personal_earnings", "personal earnings");
        add("team_earnings", "team earnings");
        add("aggregate_created", "aggregate created");
        add("aggregate_updated", "aggregate updated");
        add("personal_created", "personal earnings");
        add("team_created", "team earnings");
        add("earnings_created", "earnings created");
        add("earnings_skipped_existing", "earnings already present");
        add("created", "created");
        add("updated", "updated");
        add("deleted_logs", "logs deleted");
        add("profile_updates", "profiles updated");
        add("descendant_updates", "descendants updated");
        add("volume_rows", "volume rows");
        add("user_level_rows", "user-level rows");
        add("level_rows", "level rows");
        add("m2m_rows", "level links");
        add("pending_total", "pending source rows");
        add("dead_letter_total", "quarantined source rows");
        add("unresolved_event_total", "unresolved source rows");
        add("missing_user", "unmatched wallets");

        if (parts.length) return parts.slice(0, 5).join(" · ");
        if (run.business_success === true || counters.business_success === true) return "Business completed";
        return "No business counters reported";
    }

    function cronOpsActivity(job) {
        var run = cronOpsLatestRun(job);
        var status = cronOpsStatus(job.effective_status || job.status || run.effective_status || run.status);
        var at = job.finished_at || run.finished_at || job.started_at || run.started_at || job.scheduled_for;
        var label = status === "running" ? "Started" : (job.finished_at || run.finished_at ? "Finished" : "Scheduled");
        var businessDate = job.target_business_date || run.target_business_date;
        var recovery = job.auto_recovery || ((job.metadata || {}).auto_recovery_pending) || ((job.metadata || {}).auto_recovery_running) || ((job.metadata || {}).auto_recovery);
        var html = at ? '<strong>' + esc(label + " " + timeAgo(at)) + '</strong><br><small>' + esc(formatIST(at)) + '</small>' : '<span class="monitoring-muted">No execution record</span>';
        if (businessDate) html += '<br><small>Business date ' + esc(businessDate) + '</small>';
        if (recovery) html += '<br><small>Automatic missed-day recovery</small>';
        return html;
    }

    function cronOpsProgress(job) {
        var run = cronOpsLatestRun(job);
        var stage = run.stage || "";
        var parts = [];
        if (stage && stage !== "finished") parts.push(stage.replace(/_/g, " "));
        if (run.current != null || run.total != null) {
            parts.push(formatWhole(run.current || 0) + " / " + formatWhole(run.total || 0));
        }
        if (run.percent != null) parts.push(formatNumber(run.percent, 0) + "%");
        if (job.wait_seconds != null && job.wait_seconds > 0) parts.push("wait " + formatSeconds(job.wait_seconds));
        if (job.run_seconds != null) parts.push("ran " + formatSeconds(job.run_seconds));
        return parts.length ? parts.join(" · ") : "No step progress";
    }

    function cronOpsAttention(job) {
        var run = cronOpsLatestRun(job);
        var counters = cronOpsCounters(job);
        var status = cronOpsStatus(job.effective_status || job.status || run.effective_status || run.status);
        var lane = job.queue_name || run.queue_name || "";
        var businessDate = job.target_business_date || run.target_business_date;
        var error = job.last_error_message || run.error_message || "";
        var recovery = job.auto_recovery || ((job.metadata || {}).auto_recovery_pending) || ((job.metadata || {}).auto_recovery_running) || ((job.metadata || {}).auto_recovery);
        if (recovery) {
            return {text: "Automatic recovery for business date " + (businessDate || "-"), state: "warn"};
        }
        if (lane === "financial" && !businessDate && ["missed_sla", "failed", "failure", "stale", "timeout"].indexOf(status) !== -1) {
            return {text: "Missed SLA; approved business-date review required", state: "bad"};
        }
        if (error) {
            return {text: error, state: "bad"};
        }
        if (counters.data_integrity_status === "degraded") {
            return {
                text: "Source review: " + (counters.data_integrity_reason || "data integrity issue"),
                state: "warn"
            };
        }
        if (Number(counters.dead_letter_total || 0) > 0 || Number(counters.unresolved_event_total || 0) > 0) {
            return {text: "Source review required", state: "warn"};
        }
        if (["missed_sla", "failed", "failure", "stale", "timeout"].indexOf(status) !== -1) {
            return {text: job.terminal_reason ? job.terminal_reason.replace(/_/g, " ") : "Operator review required", state: "bad"};
        }
        if (["queued", "scheduled", "retrying", "waiting_for_capacity", "deferred_by_pressure"].indexOf(status) !== -1) {
            return {text: job.next_attempt_at ? "Next attempt " + timeUntil(job.next_attempt_at) : "Awaiting worker", state: "warn"};
        }
        return {text: "None", state: "ok"};
    }

    function cronOpsStatusOrder(job) {
        var status = cronOpsStatus(job.effective_status || job.status);
        var order = {
            failed: 0,
            failure: 0,
            missed_sla: 0,
            stale: 0,
            timeout: 0,
            degraded: 1,
            running: 2,
            retrying: 3,
            deferred_by_pressure: 4,
            waiting_for_capacity: 4,
            queued: 5,
            scheduled: 5,
            duplicate_skipped: 7,
            cancelled: 7,
            success: 8
        };
        return order[status] == null ? 6 : order[status];
    }

    function bindCronOpsJobRows() {
        Array.prototype.forEach.call(document.querySelectorAll(".monitoring-cronops-job-row"), function (row) {
            if (row.dataset.bound === "1") return;
            row.dataset.bound = "1";
            var openHistory = function () {
                if (row.dataset.jobKey) loadCronHistory(row.dataset.jobKey);
            };
            row.addEventListener("click", openHistory);
            row.addEventListener("keydown", function (event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openHistory();
                }
            });
        });
    }

    function cronOpsRecoveryResult(recovery) {
        var counters = cronOpsLatestRun(recovery).item_counters || {};
        if (hasCounter(counters, "earnings_created")) {
            var skipped = hasCounter(counters, "earnings_skipped_existing")
                ? " / " + formatWhole(counters.earnings_skipped_existing) + " existing"
                : "";
            return formatWhole(counters.earnings_created) + " rows created" + skipped;
        }
        if (hasCounter(counters, "business_success")) {
            return counters.business_success ? "Business success" : "Business result needs review";
        }
        return recovery.terminal_reason || "-";
    }

    function cronOpsRecoverySnapshot(recovery) {
        var counters = cronOpsLatestRun(recovery).item_counters || {};
        var metadata = recovery.metadata || {};
        var snapshot = metadata.business_snapshot || {};
        var snapshotId = counters.snapshot_id || snapshot.snapshot_id;
        var hash = counters.snapshot_hash || snapshot.snapshot_hash;
        if (!snapshotId && !hash) return "No snapshot reference";
        return "#" + (snapshotId || "-") + (hash ? " / " + String(hash).slice(0, 12) : "");
    }

    function formatDistributionAmount(value, symbol) {
        if (value == null || !Number.isFinite(Number(value))) return "-";
        var amount = Number(value).toLocaleString("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        return amount + (symbol ? " " + symbol : "");
    }

    function distributionStatusClass(status) {
        status = String(status || "unknown").toLowerCase();
        if (["distributed", "recovered", "recorded", "no_eligible_earnings"].indexOf(status) !== -1) return "success";
        if (["failed", "failure", "timeout", "stale", "missed_sla", "degraded", "cancelled", "not_recorded"].indexOf(status) !== -1) return "failure";
        if (["awaiting_run", "queued", "scheduled", "running", "retrying", "waiting_for_capacity", "deferred_by_pressure"].indexOf(status) !== -1) return "warning";
        return "paused";
    }

    function renderSvr4PlusEarnings(data) {
        var hodl = data.hodl_cronops || {};
        var earnings = hodl.svr4plus_earnings || {};
        var summary = $("monitoring-svr4plus-earnings-summary");
        var table = $("monitoring-svr4plus-earnings");
        var updated = $("monitoring-svr4plus-earnings-updated");
        if (updated) updated.textContent = "Updated " + (earnings.generated_at ? formatIST(earnings.generated_at) : "-");
        if (!summary || !table) return;
        if (earnings.status !== "ok") {
            summary.innerHTML = "";
            table.innerHTML = '<tr><td colspan="7" class="monitoring-muted">SVR4 Plus distribution data is unavailable: ' + esc(earnings.error || "unknown error") + '</td></tr>';
            return;
        }

        var totals = earnings.totals || {};
        var primary = earnings.primary_token || {};
        var cards = [
            [(primary.symbol || "USDT") + " total", formatDistributionAmount(totals.primary_amount, primary.symbol), "ok"],
            ["Paid dates", totals.paid_days || 0, "ok"],
            ["Recovered dates", totals.recovered_days || 0, totals.recovered_days ? "warn" : "ok"],
            [(primary.symbol || "USDT") + " rows", formatWhole(totals.primary_rows || 0), "ok"],
            ["Needs review", totals.attention_days || 0, totals.attention_days ? "bad" : "ok"]
        ];
        summary.innerHTML = cards.map(function (card) {
            return '<div class="live-summary-card cronops-card ' + esc(card[2]) + '"><span>' + esc(card[0]) + '</span><strong>' + esc(card[1]) + '</strong></div>';
        }).join("");

        var days = earnings.days || [];
        if (!days.length) {
            table.innerHTML = '<tr><td colspan="7" class="monitoring-muted">No SVR4 Plus distribution rows were found for this period.</td></tr>';
            return;
        }
        table.innerHTML = days.map(function (day) {
            var distribution = day.primary_distribution || {};
            var run = day.run || {};
            var snapshot = day.snapshot || {};
            var status = day.business_status || "unknown";
            var other = (day.other_distributions || []).map(function (token) {
                return esc(formatDistributionAmount(token.amount, token.symbol) + " / " + formatWhole(token.rows || 0) + " rows");
            }).join("<br>");
            var execution = [];
            if (run.scheduled_for) execution.push("Scheduled " + formatIST(run.scheduled_for));
            if (run.started_at) execution.push("Ran " + formatIST(run.started_at) + (run.run_seconds != null ? " / " + formatSeconds(run.run_seconds) : ""));
            if (run.earnings_created != null) execution.push(formatWhole(run.earnings_created) + " rows created");
            if (snapshot.id || snapshot.snapshot_hash) {
                execution.push("Snapshot #" + (snapshot.id || "-") + (snapshot.snapshot_hash ? " / " + String(snapshot.snapshot_hash).slice(0, 12) : ""));
            }
            return '<tr>' +
                '<td><strong>' + esc(day.business_date || "-") + '</strong></td>' +
                '<td><strong>' + esc(formatDistributionAmount(distribution.amount, distribution.symbol || primary.symbol)) + '</strong></td>' +
                '<td>' + esc(formatDistributionAmount(distribution.personal_amount, distribution.symbol || primary.symbol)) +
                    '<br><small>' + esc(formatDistributionAmount(distribution.passive_amount, distribution.symbol || primary.symbol)) + '</small></td>' +
                '<td>' + esc(formatWhole(distribution.rows || 0)) + '</td>' +
                '<td>' + (other || '<span class="monitoring-muted">-</span>') + '</td>' +
                '<td><span class="monitoring-status ' + esc(distributionStatusClass(status)) + '">' + esc(String(status).replace(/_/g, " ")) + '</span>' +
                    (day.attention ? '<br><small>' + esc(day.attention) + '</small>' : '') + '</td>' +
                '<td><small>' + esc(execution.length ? execution.join(" / ") : "No CronOps run record") + '</small></td>' +
            '</tr>';
        }).join("");
    }

    function renderCronOpsRecoveries(data) {
        var hodl = data.hodl_cronops || {};
        var recoveries = hodl.recent_auto_recoveries || [];
        var table = $("monitoring-cronops-recoveries");
        var summary = $("monitoring-cronops-recoveries-summary");
        var updated = $("monitoring-cronops-recoveries-updated");
        if (updated) updated.textContent = "Updated " + (data.generated_at_ist ? formatIST(data.generated_at_ist) : "-");
        if (!table || !summary) return;
        if (hodl.status !== "ok") {
            summary.innerHTML = "";
            table.innerHTML = '<tr><td colspan="7" class="monitoring-muted">CronOps recovery data is unavailable: ' + esc(hodl.error || "unknown error") + '</td></tr>';
            return;
        }

        var completed = 0;
        var active = 0;
        var attention = 0;
        recoveries.forEach(function (recovery) {
            var status = cronOpsStatus(recovery.effective_status || recovery.status);
            if (status === "success") completed += 1;
            else if (["queued", "scheduled", "running", "retrying", "waiting_for_capacity", "deferred_by_pressure"].indexOf(status) !== -1) active += 1;
            else attention += 1;
        });
        var cards = [
            ["Completed (24h)", completed, "ok"],
            ["Active", active, active ? "warn" : "ok"],
            ["Needs attention", attention, attention ? "bad" : "ok"]
        ];
        summary.innerHTML = cards.map(function (card) {
            return '<div class="live-summary-card cronops-card ' + esc(card[2]) + '"><span>' + esc(card[0]) + '</span><strong>' + esc(card[1]) + '</strong></div>';
        }).join("");

        if (!recoveries.length) {
            table.innerHTML = '<tr><td colspan="7" class="monitoring-muted">No snapshot-backed financial recoveries in the last 24 hours.</td></tr>';
            return;
        }
        table.innerHTML = recoveries.map(function (recovery) {
            var status = recovery.effective_status || recovery.status || "unknown";
            var executed = recovery.started_at
                ? formatIST(recovery.started_at) + (recovery.finished_at ? " / " + formatSeconds(recovery.run_seconds) : "")
                : "Not started";
            return '<tr class="monitoring-cronops-recovery-row" data-job-key="' + attr(recovery.job_key || "") + '" tabindex="0" role="button" aria-label="Open history for ' + attr(recovery.job_name || recovery.job_key || "financial recovery") + '">' +
                '<td><strong>' + esc(recovery.job_name || recovery.job_key || "Unknown job") + '</strong><br><small>' + esc(recovery.job_key || "-") + '</small></td>' +
                '<td><strong>' + esc(recovery.target_business_date || "-") + '</strong></td>' +
                '<td><span class="monitoring-status ' + esc(cronOpsStatusClass(status)) + '">' + esc(cronOpsStatusLabel(status)) + '</span></td>' +
                '<td>' + esc(formatIST(recovery.scheduled_for)) + '</td>' +
                '<td>' + esc(executed) + '</td>' +
                '<td>' + esc(cronOpsRecoveryResult(recovery)) + '</td>' +
                '<td><code>' + esc(cronOpsRecoverySnapshot(recovery)) + '</code></td>' +
            '</tr>';
        }).join("");
        bindCronOpsJobRows();
    }

    function renderCronOpsJobs(data) {
        var hodl = data.hodl_cronops || {};
        var jobs = hodl.job_statuses || [];
        var table = $("monitoring-cronops-jobs");
        var summary = $("monitoring-cronops-jobs-summary");
        var updated = $("monitoring-cronops-jobs-updated");
        if (updated) updated.textContent = "Updated " + (data.generated_at_ist ? formatIST(data.generated_at_ist) : "-");
        if (!table || !summary) return;
        if (hodl.status !== "ok") {
            summary.innerHTML = "";
            table.innerHTML = '<tr><td colspan="7" class="monitoring-muted">CronOps job data is unavailable: ' + esc(hodl.error || "unknown error") + '</td></tr>';
            return;
        }

        var counts = {healthy: 0, attention: 0, running: 0, queued: 0};
        jobs.forEach(function (job) {
            var status = cronOpsStatus(job.effective_status || job.status);
            if (status === "success") counts.healthy += 1;
            else if (status === "running") counts.running += 1;
            else if (["queued", "scheduled", "retrying", "waiting_for_capacity", "deferred_by_pressure"].indexOf(status) !== -1) counts.queued += 1;
            else counts.attention += 1;
        });
        var cards = [
            ["Configured jobs", jobs.length || hodl.jobs || 0, "ok"],
            ["Business healthy", counts.healthy, "ok"],
            ["Needs attention", counts.attention, counts.attention ? "bad" : "ok"],
            ["Running", counts.running, counts.running ? "warn" : "ok"],
            ["Queued / retrying", counts.queued, counts.queued ? "warn" : "ok"]
        ];
        summary.innerHTML = cards.map(function (card) {
            return '<div class="live-summary-card cronops-card ' + esc(card[2]) + '"><span>' + esc(card[0]) + '</span><strong>' + esc(card[1]) + '</strong></div>';
        }).join("");

        if (!jobs.length) {
            table.innerHTML = '<tr><td colspan="7" class="monitoring-muted">No active CronOps jobs are registered.</td></tr>';
            return;
        }
        jobs.sort(function (left, right) {
            var statusOrder = cronOpsStatusOrder(left) - cronOpsStatusOrder(right);
            if (statusOrder) return statusOrder;
            return String(left.job_name || left.job_key || "").localeCompare(String(right.job_name || right.job_key || ""));
        });
        table.innerHTML = jobs.map(function (job) {
            var run = cronOpsLatestRun(job);
            var status = job.effective_status || job.status || run.effective_status || run.status;
            var attention = cronOpsAttention(job);
            var lane = job.queue_name || run.queue_name || "inline";
            var schedule = job.schedule || run.schedule || "-";
            return '<tr class="monitoring-cronops-job-row" data-job-key="' + attr(job.job_key || "") + '" tabindex="0" role="button" aria-label="Open history for ' + attr(job.job_name || job.job_key || "cron job") + '">' +
                '<td><strong>' + esc(job.job_name || job.job_key || "Unknown job") + '</strong><br><small>' + esc(job.job_key || "-") + '</small></td>' +
                '<td><strong>' + esc(lane) + '</strong><br><small>' + esc(schedule) + '</small></td>' +
                '<td><span class="monitoring-status ' + esc(cronOpsStatusClass(status)) + '">' + esc(cronOpsStatusLabel(status)) + '</span></td>' +
                '<td>' + cronOpsActivity(job) + '</td>' +
                '<td class="monitoring-cronops-job-result">' + esc(cronOpsProgress(job)) + '</td>' +
                '<td class="monitoring-cronops-job-result">' + esc(cronOpsBusinessResult(job)) + '</td>' +
                '<td class="monitoring-cronops-job-attention ' + esc(attention.state) + '">' + esc(attention.text) + '</td>' +
            '</tr>';
        }).join("");
        bindCronOpsJobRows();
    }

    function renderLiveCrons(data) {
        var rows = data.active_crons || [];
        if (!rows.length) {
            $("monitoring-live-crons").innerHTML = '<tr><td colspan="8" class="monitoring-muted">No crons are running right now. This panel updates every second during active runs.</td></tr>';
            return;
        }
        $("monitoring-live-crons").innerHTML = rows.map(function (item) {
            var process = item.process || {};
            var db = item.db || {};
            var http = item.http || {};
            var trace = item.latest_trace || {};
            var staleClass = item.stuck ? " live-stuck" : "";
            var jobKey = (item.job_key || item.function || "").replace(/'/g, "");
            var clickAttr = jobKey ? ' onclick="loadCronHistory(\'' + jobKey + '\')" style="cursor:pointer"' : '';
            return '<tr class="' + staleClass + '"' + clickAttr + '>' +
                '<td><strong>' + esc(item.project) + '</strong><br><small>' + esc(liveCronName(item)) + '</small></td>' +
                '<td>' + esc(formatSeconds(item.elapsed_seconds)) + '<br><small>PID ' + esc(item.pid) + '</small></td>' +
                '<td>' + esc(formatNumber(process.cpu_percent, 1)) + '%</td>' +
                '<td>' + esc(formatBytes(process.rss_bytes)) + '<br><small>' + esc(process.threads || "-") + ' threads</small></td>' +
                '<td>' + esc(db.query_count || 0) + '<br><small>slow ' + esc(db.slow_count || 0) + '</small></td>' +
                '<td>' + esc(http.request_count || 0) + '<br><small>errors ' + esc(http.error_count || 0) + '</small></td>' +
                '<td class="live-stage">' + esc(item.stage || "-") + '<br><small>' + esc((trace.function || "") + (trace.line ? ":" + trace.line : "")) + '</small></td>' +
                '<td>' + esc(formatSeconds(item.seconds_since_progress)) + '<br><small>' + esc(formatIST(item.updated_at_ist || item.updated_at_utc)) + '</small></td>' +
            '</tr>';
        }).join("");
    }

    function renderRecentRuns(data) {
        var runs = data.recent_runs || [];
        if (!runs.length) {
            $("monitoring-recent-runs").innerHTML = '<div class="monitoring-muted">No completed cron runs yet.</div>';
            return;
        }
        $("monitoring-recent-runs").innerHTML = runs.slice(0, 10).map(function (run) {
            var events = run.recent_events || [];
            var interesting = events.filter(function (event) {
                return ["http_response", "db_query", "failure", "run_end"].indexOf(event.type) !== -1;
            }).slice(-3).map(function (event) {
                return '<span>' + esc(event.type) + ': ' + esc(event.message || "") + '</span>';
            }).join("");
            return '<button class="recent-run ' + esc(run.status || "") + '" data-code="' + esc(run.ping_uuid || "") + '" data-run="' + esc(run.run_id || "") + '">' +
                '<strong>' + esc(run.project || "-") + ' / ' + esc(liveCronName(run)) + '</strong>' +
                '<small>' + esc(run.status || "-") + ' · ' + esc(formatSeconds(run.duration_seconds)) + ' · ' + esc(formatIST(run.started_at_ist || run.started_at)) + '</small>' +
                '<div>' + interesting + '</div>' +
            '</button>';
        }).join("");
        Array.prototype.forEach.call(document.querySelectorAll(".recent-run"), function (button) {
            button.addEventListener("click", function () {
                selectedCode = button.dataset.code;
                selectedRun = button.dataset.run;
                loadCheckSeries(selectedCode);
                loadRuns(selectedCode);
                loadCheckLive(selectedCode);
            });
        });
    }

    function renderExternalErrors(data) {
        var errors = data.external_errors || [];
        if (!errors.length) {
            $("monitoring-external-errors").innerHTML = '<div class="monitoring-muted">No external API errors detected in recent traces.</div>';
            return;
        }
        $("monitoring-external-errors").innerHTML = errors.slice(0, 8).map(function (item) {
            return '<div class="external-error ' + esc(item.severity || "warning") + '">' +
                '<strong>' + esc(item.type || "external_api_error") + '</strong>' +
                '<span>' + esc(item.project || "-") + ' / ' + esc(liveCronName(item)) + '</span>' +
                '<p>' + esc(item.message || "") + '</p>' +
                '<small>This is cron app/external API behavior, not a Healthchecks ping failure.</small>' +
            '</div>';
        }).join("");
    }

    function renderLiveAlerts(data, renderErrors) {
        var alerts = (renderErrors || []).map(function (item) {
            return "Monitoring panel failed: " + item;
        });
        var hodl = data.hodl_cronops || {};
        var workerCoverage = cronopsWorkerCoverage(hodl);
        var spool = hodl.spool_summary || {};
        var ingestion = hodl.svr4plus_ingestion || {};
        var earnings = hodl.svr4plus_earnings || {};
        var queueSummary = hodl.queue_summary || {};
        var recoveries = hodl.recent_auto_recoveries || [];
        if (!workerCoverage.total) {
            alerts.push("CronOps worker coverage is unavailable");
        } else if (workerCoverage.unavailable.length) {
            alerts.push("CronOps workers missing: " + workerCoverage.unavailableNames.join(", "));
        }
        if (Number(spool.pending || 0) > 0) {
            alerts.push("CronOps DB-outage spool has " + Number(spool.pending || 0) + " pending trigger(s)");
        }
        if (Number(spool.failed || 0) > 0) {
            alerts.push("CronOps DB-outage spool has " + Number(spool.failed || 0) + " failed replay file(s)");
        }
        if (Number(queueSummary.missed_sla_24h || 0) > 0) {
            alerts.push("CronOps recorded " + Number(queueSummary.missed_sla_24h || 0) + " missed SLA trigger(s) in the last 24 hours; current job status may have recovered");
        }
        if (Number(ingestion.pending || 0) > 0 || Number(ingestion.dead_letter || 0) > 0) {
            alerts.push("SVR4 Plus ingestion needs review: " + Number(ingestion.pending || 0) + " pending, " + Number(ingestion.dead_letter || 0) + " quarantined");
        }
        if (earnings.status && earnings.status !== "ok") {
            alerts.push("SVR4 Plus earnings distribution data is unavailable");
        } else if (Number((earnings.totals || {}).attention_days || 0) > 0) {
            alerts.push("SVR4 Plus earnings has " + Number(earnings.totals.attention_days || 0) + " business date(s) needing review");
        }
        recoveries.forEach(function (recovery) {
            var status = cronOpsStatus(recovery.effective_status || recovery.status);
            if (status !== "success") {
                alerts.push("Financial recovery needs attention: " + (recovery.job_name || recovery.job_key || "unknown job") + " for " + (recovery.target_business_date || "unknown date") + " is " + cronOpsStatusLabel(status));
            }
        });
        (data.active_crons || []).forEach(function (item) {
            if (item.stuck) alerts.push(item.project + " " + liveCronName(item) + " has no progress for " + formatSeconds(item.seconds_since_progress));
        });
        (data.stale_crons || []).forEach(function (item) {
            alerts.push(item.project + " " + liveCronName(item) + " has stale heartbeat data");
        });
        $("monitoring-live-alerts").innerHTML = alerts.length
            ? alerts.map(function (item) { return '<div class="live-alert">' + esc(item) + '</div>'; }).join("")
            : '<div class="monitoring-muted">No stuck or stale cron alerts.</div>';
    }

    function renderOrphans(data) {
        var orphans = data.orphans || [];
        var panel = $("monitoring-orphans-panel");
        var tbody = $("monitoring-orphans-tbody");
        var counter = $("monitoring-orphans-count");
        if (!panel || !tbody) return;
        if (!orphans.length) {
            panel.style.display = "none";
            return;
        }
        panel.style.display = "";
        if (orphans.length) expandPanel(panel);
        if (counter) counter.textContent = orphans.length + " orphan" + (orphans.length === 1 ? "" : "s");
        tbody.innerHTML = orphans.map(function (item) {
            var ageH = item.age_seconds != null ? formatSeconds(item.age_seconds) : "-";
            var hash = (item.job_hash || "").slice(0, 8);
            var cmd = item.cmdline || "";
            if (cmd.length > 120) cmd = cmd.slice(0, 117) + "...";
            return "<tr class=\"orphan-row orphan-" + esc(item.kind || "untracked") + "\">" +
                "<td>" + esc(item.pid) + "</td>" +
                "<td>" + esc(item.project_guess || "-") + "</td>" +
                "<td><span class=\"badge orphan-kind\">" + esc(item.kind) + "</span></td>" +
                "<td>" + esc(ageH) + "</td>" +
                "<td><code>" + esc(hash) + "</code></td>" +
                "<td><code class=\"orphan-cmd\">" + esc(cmd) + "</code></td>" +
            "</tr>";
        }).join("");
    }

    function renderLiveData(data) {
        var renderErrors = [];
        [
            ["Live summary", renderLiveSummary],
            ["CronOps lanes", renderCronOpsLanes],
            ["CronOps jobs", renderCronOpsJobs],
            ["Financial recoveries", renderCronOpsRecoveries],
            ["SVR4 Plus earnings", renderSvr4PlusEarnings],
            ["Live cron table", renderLiveCrons],
            ["Recent runs", renderRecentRuns],
            ["External errors", renderExternalErrors],
            ["Orphan processes", renderOrphans],
            ["PostgreSQL", renderPostgres],
            ["HTTP statistics", renderHttpStats],
            ["Slow SQL", renderSlowSQL],
            ["Action center", renderActionCenter]
        ].forEach(function (entry) {
            try {
                entry[1](data);
            } catch (error) {
                console.error("Monitoring render failed for " + entry[0], error);
                renderErrors.push(entry[0] + ": " + (error.message || String(error)));
            }
        });
        try {
            renderLiveAlerts(data, renderErrors);
        } catch (error) {
            console.error("Monitoring render failed for live alerts", error);
            showError("monitoring-live-alerts", "Live alerts failed: " + (error.message || String(error)));
        }
    }

    function showLiveDataError(message) {
        [
            "monitoring-live-summary",
            "monitoring-cronops-cards",
            "monitoring-cronops-queues",
            "monitoring-cronops-workers",
            "monitoring-svr4plus-ingestion",
            "monitoring-cronops-jobs-summary",
            "monitoring-cronops-jobs",
            "monitoring-cronops-recoveries-summary",
            "monitoring-cronops-recoveries",
            "monitoring-svr4plus-earnings-summary",
            "monitoring-svr4plus-earnings",
            "monitoring-live-crons",
            "monitoring-live-alerts"
        ].forEach(function (id) {
            showError(id, message);
        });
    }

    function loadLive() {
        if (inflight.live) return inflight.live;
        inflight.live = true;
        return fetch(root.dataset.liveUrl, {credentials: "same-origin"})
            .then(responseJson)
            .then(function (data) {
                clearError("monitoring-live-crons");
                lastLive = data;
                renderLiveData(data);
            })
            .catch(function (err) {
                showLiveDataError("Live monitoring data failed to load: " + err.message);
            })
            .finally(function () { inflight.live = false; });
    }

    function renderPostgres(data) {
        var pg = data.postgres || {};
        var summary = $("monitoring-pg-summary");
        var body = $("monitoring-pg-body");
        if (!body) return;
        var states = pg.connections_by_state || [];
        var totalConns = states.reduce(function (a, s) { return a + (s.count || 0); }, 0);
        var locks = pg.ungranted_locks || [];
        if (summary) {
            summary.textContent = totalConns + " conns · " + locks.length + " ungranted locks · "
                + (pg.cache_hit_ratio != null ? (pg.cache_hit_ratio * 100).toFixed(2) + "% cache hit" : "?")
                + " · DB " + (pg.database_size || "-");
        }
        if (!states.length && !(pg.top_tables || []).length) {
            body.innerHTML = '<div class="monitoring-muted">Postgres data unavailable.</div>';
            return;
        }
        var html = '';
        html += '<div style="display:flex;gap:24px;flex-wrap:wrap">';
        // Connections by state
        html += '<div><strong>Connections</strong><ul>';
        states.forEach(function (s) {
            html += '<li>' + esc(s.state) + ': <strong>' + esc(s.count) + '</strong></li>';
        });
        html += '</ul></div>';
        // Ungranted locks
        html += '<div><strong>Ungranted Locks</strong>';
        if (!locks.length) {
            html += '<div class="monitoring-muted">none</div>';
        } else {
            html += '<ul>';
            locks.slice(0, 10).forEach(function (l) {
                html += '<li>pid ' + esc(l.pid) + ' ' + esc(l.mode) + ' on ' + esc(l.relation || '?') + '</li>';
            });
            html += '</ul>';
        }
        html += '</div>';
        html += '</div>';
        // Top tables
        var tables = pg.top_tables || [];
        if (tables.length) {
            html += '<table class="table table-condensed monitoring-live-table" style="margin-top:12px">';
            html += '<thead><tr><th>Table</th><th>Live rows</th><th>Dead rows</th><th>Dead %</th><th>Size</th></tr></thead><tbody>';
            tables.forEach(function (t) {
                var deadPct = (t.dead_ratio * 100).toFixed(1);
                var rowClass = t.dead_ratio > 0.3 ? 'orphan-row orphan-untracked' : '';
                html += '<tr class="' + rowClass + '">' +
                    '<td><code>' + esc(t.table) + '</code></td>' +
                    '<td>' + esc((t.live || 0).toLocaleString()) + '</td>' +
                    '<td>' + esc((t.dead || 0).toLocaleString()) + '</td>' +
                    '<td>' + deadPct + '%</td>' +
                    '<td>' + esc(t.size_pretty) + '</td>' +
                '</tr>';
            });
            html += '</tbody></table>';
        }
        body.innerHTML = html;
    }

    function actionLabel(action) {
        return {
            vacuum_analyze: "Vacuum",
            reindex_concurrently: "Reindex",
            vacuum_full: "Vacuum Full",
            truncate_empty: "Truncate"
        }[action] || action;
    }

    function confirmationFor(project, schemaName, tableName, action) {
        if (action === "vacuum_full") return "VACUUM FULL " + project + "." + schemaName + "." + tableName;
        if (action === "truncate_empty") return "TRUNCATE EMPTY " + project + "." + schemaName + "." + tableName;
        return tableName;
    }

    function renderDbMaintenance(data) {
        var container = $("monitoring-db-maintenance");
        if (!container) return;
        var projects = data.projects || [];
        var jobs = data.recent_jobs || [];
        var active = data.active_jobs || [];
        var canManage = !!data.can_manage;
        if (!projects.length) {
            container.innerHTML = '<div class="monitoring-muted">No DB maintenance data available.</div>';
            return;
        }
        var html = '<div class="db-maintenance-note">' +
            '<strong>Dead rows</strong> are old row versions waiting for VACUUM cleanup. Buttons queue audited background jobs; blocking actions are refused while crons, locks, or long transactions are active.' +
            (canManage ? '' : ' You can view recommendations, but only staff/superusers can queue jobs.') +
        '</div>';
        projects.forEach(function (project) {
            if (project.status === "error") {
                html += '<div class="external-error"><strong>' + esc(project.project_label) + '</strong><p>' + esc(project.error) + '</p></div>';
                return;
            }
            var totalConns = (project.connections_by_state || []).reduce(function (sum, item) { return sum + (item.count || 0); }, 0);
            html += '<div class="db-project-block">' +
                '<div class="monitoring-project-head">' +
                    '<div><h3>' + esc(project.project_label) + '</h3>' +
                    '<p class="monitoring-muted">' + esc(totalConns) + ' conns · ' + esc((project.ungranted_locks || []).length) +
                    ' locks · active crons ' + esc(project.active_crons || 0) + ' · DB ' + esc(project.database_size || "-") + '</p></div>' +
                '</div>';
            html += '<div class="table-responsive five-row-table-wrap"><table class="table table-condensed monitoring-live-table db-maintenance-table">';
            html += '<thead><tr><th>Risk</th><th>Table</th><th>Dead</th><th>Size</th><th>Recommendation</th><th>Actions</th></tr></thead><tbody>';
            (project.top_tables || []).forEach(function (table) {
                var rec = table.recommendation || {};
                var severity = rec.severity || "ok";
                var deadPct = ((table.dead_ratio || 0) * 100).toFixed(1) + "%";
                var actions = ["vacuum_analyze", "reindex_concurrently", "vacuum_full", "truncate_empty"].map(function (action) {
                    var disabled = "";
                    if (!canManage) disabled = " disabled";
                    if (action === "truncate_empty" && (table.live || 0) !== 0) disabled = " disabled";
                    if ((action === "vacuum_full" || action === "truncate_empty") && (project.active_crons || 0) > 0) disabled = " disabled";
                    var cls = action === "vacuum_full" || action === "truncate_empty" ? " danger" : "";
                    return '<button class="db-action-btn' + cls + '"' + disabled +
                        ' data-project="' + attr(project.project) + '"' +
                        ' data-schema="' + attr(table.schema || "public") + '"' +
                        ' data-table="' + attr(table.table) + '"' +
                        ' data-action="' + attr(action) + '">' + esc(actionLabel(action)) + '</button>';
                }).join("");
                html += '<tr class="db-risk-' + esc(severity) + '">' +
                    '<td><span class="db-risk-chip ' + esc(severity) + '">' + esc(severity) + '</span></td>' +
                    '<td><code>' + esc((table.schema || "public") + "." + table.table) + '</code><br><small>live ' + esc((table.live || 0).toLocaleString()) + '</small></td>' +
                    '<td>' + esc((table.dead || 0).toLocaleString()) + '<br><small>' + esc(deadPct) + '</small></td>' +
                    '<td>' + esc(table.size_pretty || "-") + '</td>' +
                    '<td>' + esc(rec.message || "-") + '<br><small>' + esc((rec.tags || []).join(", ")) + '</small></td>' +
                    '<td><div class="db-action-row">' + actions + '</div></td>' +
                '</tr>';
            });
            html += '</tbody></table></div></div>';
        });

        html += '<div class="db-jobs"><h3>Maintenance Jobs</h3>';
        if (active.length) {
            html += '<p class="monitoring-muted">Active: ' + esc(active.map(function (job) {
                return job.project_label + " " + job.action + " " + job.schema_name + "." + job.table_name;
            }).join(" · ")) + '</p>';
        }
        if (!jobs.length) {
            html += '<div class="monitoring-muted">No maintenance jobs queued yet.</div>';
        } else {
            html += '<div class="table-responsive five-row-table-wrap"><table class="table table-condensed monitoring-live-table"><thead><tr><th>Status</th><th>Project</th><th>Action</th><th>Table</th><th>Requested</th><th>Result</th></tr></thead><tbody>';
            jobs.slice(0, 12).forEach(function (job) {
                html += '<tr class="db-job-' + esc(job.status) + '">' +
                    '<td><span class="db-risk-chip ' + esc(job.status) + '">' + esc(job.status) + '</span></td>' +
                    '<td>' + esc(job.project_label) + '</td>' +
                    '<td>' + esc(job.action) + '</td>' +
                    '<td><code>' + esc(job.schema_name + "." + job.table_name) + '</code></td>' +
                    '<td>' + esc(formatIST(job.requested_at)) + '<br><small>' + esc(job.requested_by || "-") + '</small></td>' +
                    '<td>' + esc(job.error || job.output || "-") + '</td>' +
                '</tr>';
            });
            html += '</tbody></table></div>';
        }
        html += '</div>';
        container.innerHTML = html;

        Array.prototype.forEach.call(container.querySelectorAll(".db-action-btn"), function (button) {
            button.addEventListener("click", function () {
                var project = button.dataset.project;
                var schemaName = button.dataset.schema;
                var tableName = button.dataset.table;
                var action = button.dataset.action;
                var expected = confirmationFor(project, schemaName, tableName, action);
                var typed = window.prompt("Type exactly to queue " + actionLabel(action) + ":\n" + expected);
                if (typed == null) return;
                queueDbMaintenance(project, schemaName, tableName, action, typed);
            });
        });
    }

    function queueDbMaintenance(project, schemaName, tableName, action, confirmation) {
        fetch(root.dataset.dbMaintenanceActionUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken")
            },
            body: JSON.stringify({
                project: project,
                schema: schemaName,
                table: tableName,
                action: action,
                confirmation: confirmation
            })
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok || !data.ok) throw new Error(data.error || "Failed to queue maintenance job");
                return data;
            });
        }).then(function () {
            loadDbMaintenance();
            expandPanel($("monitoring-db-maintenance") && $("monitoring-db-maintenance").closest(".monitoring-collapsible"));
        }).catch(function (err) {
            window.alert(err.message);
        });
    }

    function loadDbMaintenance() {
        if (!root.dataset.dbMaintenanceUrl) return Promise.resolve();
        if (inflight.dbMaintenance) return inflight.dbMaintenance;
        inflight.dbMaintenance = true;
        return fetch(root.dataset.dbMaintenanceUrl, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(renderDbMaintenance)
            .catch(function (err) {
                var container = $("monitoring-db-maintenance");
                if (container) container.innerHTML = '<div class="monitoring-muted">DB maintenance data failed to load: ' + esc(err.message) + '</div>';
            })
            .finally(function () { inflight.dbMaintenance = false; });
    }

    function renderHttpStats(data) {
        var hosts = data.http_stats || [];
        var counter = $("monitoring-http-count");
        var tbody = $("monitoring-http-tbody");
        if (counter) counter.textContent = hosts.length + (hosts.length === 1 ? " host" : " hosts");
        if (!tbody) return;
        if (!hosts.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="monitoring-muted">No HTTP calls captured yet.</td></tr>';
            return;
        }
        tbody.innerHTML = hosts.map(function (h) {
            var fmt = function (v) { return (v || 0).toFixed(0) + "ms"; };
            var errCls = h.errors > 0 ? "orphan-row orphan-untracked" : "";
            return '<tr class="' + errCls + '">' +
                '<td><code>' + esc(h.host) + '</code></td>' +
                '<td>' + esc(h.count) + '</td>' +
                '<td>' + esc(h.errors) + (h.errors > 0 ? ' (' + ((h.error_rate||0)*100).toFixed(1) + '%)' : '') + '</td>' +
                '<td>' + esc(fmt(h.p50_ms)) + '</td>' +
                '<td>' + esc(fmt(h.p95_ms)) + '</td>' +
                '<td>' + esc(fmt(h.p99_ms)) + '</td>' +
                '<td>' + esc(fmt(h.avg_ms)) + '</td>' +
            '</tr>';
        }).join('');
    }

    function renderSlowSQL(data) {
        var rows = data.slow_queries || [];
        var counter = $("monitoring-slow-sql-count");
        var tbody = $("monitoring-slow-sql-tbody");
        if (counter) counter.textContent = rows.length + " in 24h";
        if (!tbody) return;
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="4" class="monitoring-muted">No slow queries in last 24 h.</td></tr>';
            return;
        }
        tbody.innerHTML = rows.slice(0, 50).map(function (r) {
            var sqlShort = (r.sql || "").slice(0, 240);
            var time = r.created_at ? new Date(r.created_at).toLocaleString() : "-";
            var dur = r.duration_ms != null ? Math.round(r.duration_ms) + "ms" : "-";
            var planTitle = r.plan ? "EXPLAIN plan available — see CronEvent " : "";
            return '<tr title="' + esc(planTitle) + '">' +
                '<td>' + esc(time) + '</td>' +
                '<td><small>' + esc(r.job_key || "-") + '</small></td>' +
                '<td>' + esc(dur) + '</td>' +
                '<td><code style="white-space:pre-wrap;word-break:break-all">' + esc(sqlShort) + '</code></td>' +
            '</tr>';
        }).join('');
    }

    function loadCronHistory(jobKey) {
        var panel = $("monitoring-cron-history-panel");
        var titleEl = $("monitoring-history-job");
        var body = $("monitoring-history-body");
        if (!panel || !body) return;
        panel.style.display = "";
        expandPanel(panel);
        if (titleEl) titleEl.textContent = jobKey;
        body.innerHTML = '<div class="monitoring-muted">Loading...</div>';
        var url = "/monitoring/api/cron-history/" + encodeURIComponent(jobKey) + "/";
        fetch(url, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                var days = data.days || [];
                if (!days.length) {
                    body.innerHTML = '<div class="monitoring-muted">No history yet (cron has never finished a run in the last 30 days).</div>';
                    return;
                }
                var html = '<div class="table-responsive five-row-table-wrap"><table class="table table-condensed monitoring-live-table"><thead><tr>';
                html += '<th>Date</th><th>Runs</th><th>OK</th><th>Fail</th><th>Skip</th><th>p50</th><th>p95</th><th>Avg DB queries</th>';
                html += '</tr></thead><tbody>';
                days.forEach(function (d) {
                    var failCls = (d.failure || 0) > 0 ? "orphan-row orphan-untracked" : "";
                    html += '<tr class="' + failCls + '">' +
                        '<td>' + esc(d.date) + '</td>' +
                        '<td>' + esc(d.total) + '</td>' +
                        '<td>' + esc(d.success) + '</td>' +
                        '<td>' + esc(d.failure) + '</td>' +
                        '<td>' + esc(d.skipped || 0) + '</td>' +
                        '<td>' + (d.p50_seconds != null ? esc(d.p50_seconds.toFixed(1) + "s") : "-") + '</td>' +
                        '<td>' + (d.p95_seconds != null ? esc(d.p95_seconds.toFixed(1) + "s") : "-") + '</td>' +
                        '<td>' + (d.avg_db_queries != null ? esc(d.avg_db_queries.toFixed(0)) : "-") + '</td>' +
                    '</tr>';
                });
                html += '</tbody></table></div>';
                body.innerHTML = html;
            })
            .catch(function (err) {
                body.innerHTML = '<div class="monitoring-muted">Failed to load: ' + esc(err.message) + '</div>';
            });
    }
    window.loadCronHistory = loadCronHistory;

    function loadCheckSeries(code) {
        if (inflight.checkSeries) return inflight.checkSeries;
        inflight.checkSeries = true;
        var url = root.dataset.seriesTemplate.replace("__CODE__", code);
        return fetch(url, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                clearError("monitoring-duration-chart");
                var points = data.durations || [];
                var values = points.map(function (point) { return point.value; });
                var stats = statsFromValues(values);
                $("monitoring-selected-empty").style.display = "none";
                $("monitoring-selected").style.display = "";
                expandPanel($("monitoring-selected-panel"));
                expandPanel($("monitoring-log-panel"));
                $("monitoring-selected-title").textContent = data.check.name + " duration";
                drawChart($("monitoring-duration-chart"), points, {color: "#00f5d4", unit: "s"});
                $("monitoring-duration-stats").innerHTML =
                    '<span>Latest <strong>' + esc(formatSeconds(stats.latest)) + '</strong></span>' +
                    '<span>Avg <strong>' + esc(formatSeconds(stats.avg)) + '</strong></span>' +
                    '<span>Max <strong>' + esc(formatSeconds(stats.max)) + '</strong></span>';
                $("monitoring-run-timeline").innerHTML = (data.flips || []).slice(-18).map(function (flip) {
                    return '<span class="run-dot ' + (flip.status === "up" ? "success" : "failure") + '" title="' +
                        esc(flip.status + " " + formatIST(flip.ts_ist || flip.ts * 1000)) + '"></span>';
                }).join("");
                $("monitoring-selected-links").innerHTML =
                    '<a class="btn monitoring-mini-btn" href="' + esc(data.check.details_url) + '">Details</a>' +
                    '<a class="btn monitoring-mini-btn" href="' + esc(data.check.log_url) + '">Ping/Event Log</a>';
            })
            .catch(function (err) {
                showError("monitoring-duration-chart", "Check series data failed to load: " + err.message);
            })
            .finally(function () { inflight.checkSeries = false; });
    }

    function loadCheckLive(code) {
        if (!code) return Promise.resolve();
        if (inflight.checkLive) return inflight.checkLive;
        inflight.checkLive = true;
        var url = root.dataset.liveTemplate.replace("__CODE__", code);
        return fetch(url, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                clearError("monitoring-trace-events");
                var active = data.active || [];
                var lastRun = data.last_run || {};
                var events = active.length ? (active[0].recent_events || []) : (lastRun.recent_events || []);
                renderEventList("monitoring-trace-events", events, "No deep trace events yet. New runs will include HTTP, DB, stack, and Python trace events.");
            })
            .catch(function (err) {
                showError("monitoring-trace-events", "Trace events failed to load: " + err.message);
            })
            .finally(function () { inflight.checkLive = false; });
    }

    function loadRuns(code) {
        if (inflight.runs) return inflight.runs;
        inflight.runs = true;
        var url = root.dataset.runsTemplate.replace("__CODE__", code);
        return fetch(url, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                clearError("monitoring-log-runs");
                var runs = data.runs || [];
                if (!runs.length) {
                    $("monitoring-log-runs").innerHTML = "";
                    $("monitoring-execution-log").textContent = "No execution logs yet. Logs will appear after the cron runs through the monitoring wrapper.";
                    $("monitoring-log-status").textContent = "No runs";
                    return;
                }
                if (!selectedRun) selectedRun = runs[0].run_id;
                $("monitoring-log-runs").innerHTML = runs.slice(0, 8).map(function (run) {
                    return '<button class="log-run-btn ' + esc(run.status) + (run.run_id === selectedRun ? " active" : "") +
                        '" data-run="' + esc(run.run_id) + '">' +
                        esc(run.status) + ' · ' + esc(timeAgo(run.started_at)) + ' · ' +
                        esc(formatSeconds(run.duration_seconds)) + '</button>';
                }).join("");

                Array.prototype.forEach.call(document.querySelectorAll(".log-run-btn"), function (button) {
                    button.addEventListener("click", function () {
                        selectedRun = button.dataset.run;
                        loadExecutionLog(selectedCode, selectedRun);
                    });
                });

                loadExecutionLog(code, selectedRun);
            })
            .catch(function (err) {
                showError("monitoring-log-runs", "Run history failed to load: " + err.message);
            })
            .finally(function () { inflight.runs = false; });
    }

    function loadExecutionLog(code, runId) {
        if (!code) return Promise.resolve();
        if (inflight.executionLog) return inflight.executionLog;
        inflight.executionLog = true;
        var url = root.dataset.logTemplate.replace("__CODE__", code);
        if (runId) url += "?run=" + encodeURIComponent(runId);
        return fetch(url, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                clearError("monitoring-execution-log");
                var eventText = (data.events || []).slice(-40).map(function (event) {
                    return "[" + formatIST(event.at_ist || event.at_utc) + "] " + event.type + " " + event.severity + " - " + (event.message || "");
                }).join("\n");
                $("monitoring-log-status").textContent = data.found ? (data.truncated ? "Tail shown" : "Full log") : "No log";
                $("monitoring-execution-log").textContent =
                    (eventText ? "Structured trace events\n" + eventText + "\n\nRaw execution log\n" : "") +
                    (data.content || data.message || "No log content.");
                renderEventList("monitoring-trace-events", data.events || [], "No deep trace events found for this run.");
            })
            .catch(function (err) {
                showError("monitoring-execution-log", "Execution log failed to load: " + err.message);
            })
            .finally(function () { inflight.executionLog = false; });
    }

    function refresh() {
        loadOverview();
        loadInfrastructure();
        loadLive();
        loadDbMaintenance();
        if (selectedCode) {
            loadCheckSeries(selectedCode);
            loadRuns(selectedCode);
            loadCheckLive(selectedCode);
        }
    }

    initCollapsiblePanels();
    root.addEventListener("click", function (event) {
        var button = event.target.closest(".monitoring-collapse-toggle");
        if (!button || !root.contains(button)) return;
        var panel = button.closest(".monitoring-collapsible");
        if (!panel) return;
        event.preventDefault();
        event.stopPropagation();
        setPanelExpanded(panel, panel.classList.contains("collapsed"));
    });
    $("monitoring-refresh").addEventListener("click", refresh);
    if ($("monitoring-cron-search")) {
        $("monitoring-cron-search").addEventListener("input", function (event) {
            cronFilter.text = event.target.value || "";
            applyCronFilter();
        });
    }
    Array.prototype.forEach.call(document.querySelectorAll(".monitoring-filter"), function (button) {
        button.addEventListener("click", function () {
            cronFilter.status = button.dataset.status || "all";
            Array.prototype.forEach.call(document.querySelectorAll(".monitoring-filter"), function (item) {
                item.classList.toggle("active", item === button);
            });
            applyCronFilter();
        });
    });
    refresh();
    setInterval(loadLive, 3000);
    setInterval(loadInfrastructure, 10000);
    setInterval(loadDbMaintenance, 30000);
    setInterval(refresh, 30000);
    setInterval(function () {
        if (selectedCode) {
            loadCheckLive(selectedCode);
            loadExecutionLog(selectedCode, selectedRun);
        } else if (lastLive) {
            renderRecentRuns(lastLive);
        }
    }, 10000);
})();
