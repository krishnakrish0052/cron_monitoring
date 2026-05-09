(function () {
    var root = document.getElementById("monitoring-dashboard");
    if (!root) return;

    var selectedCode = null;
    var selectedRun = null;

    function $(id) {
        return document.getElementById(id);
    }

    function esc(value) {
        var div = document.createElement("div");
        div.textContent = value == null ? "" : value;
        return div.innerHTML;
    }

    function timeAgo(value) {
        if (!value) return "Never";
        var seconds = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 1000));
        if (seconds < 60) return seconds + "s ago";
        if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
        if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
        return Math.floor(seconds / 86400) + "d ago";
    }

    function timeUntil(value) {
        if (!value) return "";
        var seconds = Math.floor((Date.parse(value) - Date.now()) / 1000);
        if (!Number.isFinite(seconds)) return "";
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
        var width = 640;
        var height = options.small ? 128 : 180;
        var pad = {top: 14, right: 18, bottom: 28, left: 42};
        var values = (points || []).map(function (point) {
            return typeof point === "number" ? point : Number(point.value);
        }).filter(function (value) {
            return Number.isFinite(value);
        });

        if (!values.length) {
            svg.setAttribute("viewBox", "0 0 " + width + " " + height);
            svg.innerHTML = '<text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#777">No data yet</text>';
            return;
        }

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
        var path = values.map(function (value, index) {
            var x = pad.left + (values.length === 1 ? graphWidth : (index / (values.length - 1)) * graphWidth);
            var y = pad.top + graphHeight - ((value - min) / (max - min)) * graphHeight;
            return (index ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
        }).join(" ");

        var fillPath = path + " L" + (pad.left + graphWidth) + "," + (pad.top + graphHeight) + " L" + pad.left + "," + (pad.top + graphHeight) + " Z";
        var unit = options.unit || "";
        var color = options.color || "#337ab7";
        var labels = [
            '<text x="' + (pad.left - 8) + '" y="' + (pad.top + 5) + '" text-anchor="end">' + esc(formatNumber(max, 0) + unit) + '</text>',
            '<text x="' + (pad.left - 8) + '" y="' + (pad.top + graphHeight) + '" text-anchor="end">' + esc(formatNumber(min, 0) + unit) + '</text>',
        ].join("");

        svg.setAttribute("viewBox", "0 0 " + width + " " + height);
        svg.innerHTML =
            '<g class="chart-grid">' +
            '<line x1="' + pad.left + '" y1="' + pad.top + '" x2="' + pad.left + '" y2="' + (pad.top + graphHeight) + '"></line>' +
            '<line x1="' + pad.left + '" y1="' + (pad.top + graphHeight) + '" x2="' + (pad.left + graphWidth) + '" y2="' + (pad.top + graphHeight) + '"></line>' +
            '<line x1="' + pad.left + '" y1="' + (pad.top + graphHeight / 2) + '" x2="' + (pad.left + graphWidth) + '" y2="' + (pad.top + graphHeight / 2) + '"></line>' +
            labels +
            '</g>' +
            '<path d="' + fillPath + '" fill="' + color + '" opacity="0.12"></path>' +
            '<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="3" vector-effect="non-scaling-stroke"></path>';
    }

    function renderSummary(totals) {
        var items = [
            ["Total", totals.total || 0, "info"],
            ["Up", totals.up || 0, "ok"],
            ["Down", totals.down || 0, "bad"],
            ["Late", totals.grace || 0, "warn"],
            ["New", totals.new || 0, ""],
            ["Paused", totals.paused || 0, ""],
        ];
        $("monitoring-summary").innerHTML = items.map(function (item) {
            return '<div class="monitoring-card ' + esc(item[2]) + '"><div class="label-text">' +
                esc(item[0]) + '</div><div class="value">' + esc(item[1]) + '</div></div>';
        }).join("");
    }

    function renderProjects(projects) {
        $("monitoring-projects").innerHTML = projects.map(function (project) {
            var rows = project.checks.map(function (check) {
                var scheduleCell = '<code>' + esc(check.schedule || "-") + '</code>';
                if (check.next_due) {
                    scheduleCell += '<br><small class="monitoring-subtext">Next ' + esc(timeUntil(check.next_due)) + '</small>';
                }
                var lastPingCell = check.last_ping ? esc(timeAgo(check.last_ping)) : 'Waiting first run';
                if (!check.last_ping && check.next_due) {
                    lastPingCell += '<br><small class="monitoring-subtext">Expected ' + esc(timeUntil(check.next_due)) + '</small>';
                }
                return '<tr>' +
                    '<td><span class="monitoring-status ' + esc(check.status) + '">' + esc(check.status_label || check.status) + '</span></td>' +
                    '<td><strong>' + esc(check.name) + '</strong><br><small class="monitoring-muted">' + esc(check.tags) + '</small></td>' +
                    '<td>' + scheduleCell + '</td>' +
                    '<td>' + lastPingCell + '</td>' +
                    '<td>' + esc(check.last_duration == null ? "-" : check.last_duration + "s") + '</td>' +
                    '<td><button class="btn btn-default btn-xs monitoring-graph" data-code="' + esc(check.code) + '">Inspect</button></td>' +
                    '<td><a class="btn btn-default btn-xs" href="' + esc(check.details_url) + '">Details</a></td>' +
                    '<td><a class="btn btn-default btn-xs" href="' + esc(check.log_url) + '">Ping/Event Log</a></td>' +
                '</tr>';
            }).join("");

            var health = project.health || {};
            var healthLabel = health.status || "unknown";
            return '<div class="monitoring-panel">' +
                '<div class="monitoring-project-head">' +
                    '<h2>' + esc(project.name) + '</h2>' +
                    '<span class="monitoring-status ' + (healthLabel === "ok" ? "up" : "down") + '">' + esc(healthLabel) + '</span>' +
                '</div>' +
                '<div class="table-responsive"><table class="table table-condensed">' +
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
            });
        });
    }

    function loadCheckSeries(code) {
        var url = root.dataset.seriesTemplate.replace("__CODE__", code);
        return fetch(url, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                var values = data.durations.map(function (point) { return point.value; });
                var stats = statsFromValues(values);
                $("monitoring-selected-empty").style.display = "none";
                $("monitoring-selected").style.display = "";
                $("monitoring-selected-title").textContent = data.check.name + " duration";
                drawChart($("monitoring-duration-chart"), values, {color: "#337ab7", unit: "s"});
                $("monitoring-duration-stats").innerHTML =
                    '<span>Latest <strong>' + esc(formatNumber(stats.latest, 2)) + 's</strong></span>' +
                    '<span>Avg <strong>' + esc(formatNumber(stats.avg, 2)) + 's</strong></span>' +
                    '<span>Max <strong>' + esc(formatNumber(stats.max, 2)) + 's</strong></span>';
                $("monitoring-run-timeline").innerHTML = data.flips.slice(-12).map(function (flip) {
                    return '<span class="run-dot ' + (flip.status === "up" ? "success" : "failure") + '" title="' +
                        esc(flip.status) + '"></span>';
                }).join("");
                $("monitoring-selected-links").innerHTML =
                    '<a class="btn btn-default btn-sm" href="' + esc(data.check.details_url) + '">Details</a>' +
                    '<a class="btn btn-default btn-sm" href="' + esc(data.check.log_url) + '">Ping/Event Log</a>';
            });
    }

    function loadRuns(code) {
        var url = root.dataset.runsTemplate.replace("__CODE__", code);
        return fetch(url, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
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
                        esc(formatNumber(run.duration_seconds, 1)) + 's</button>';
                }).join("");

                Array.prototype.forEach.call(document.querySelectorAll(".log-run-btn"), function (button) {
                    button.addEventListener("click", function () {
                        selectedRun = button.dataset.run;
                        loadExecutionLog(selectedCode, selectedRun);
                    });
                });

                loadExecutionLog(code, selectedRun);
            });
    }

    function loadExecutionLog(code, runId) {
        if (!code) return;
        var url = root.dataset.logTemplate.replace("__CODE__", code);
        if (runId) url += "?run=" + encodeURIComponent(runId);
        return fetch(url, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                $("monitoring-log-status").textContent = data.found ? (data.truncated ? "Tail shown" : "Full log") : "No log";
                $("monitoring-execution-log").textContent = data.content || data.message || "No log content.";
            });
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
                '<div>Load 1m <strong>' + esc(formatNumber(details.load1, 2)) + '</strong></div>' +
                '<div>Cores <strong>' + esc(formatNumber(details.cores, 0)) + '</strong></div>';
        } else {
            detailHtml =
                '<div>Total/hour <strong>' + esc(formatNumber(details.total_requests_window, 0)) + '</strong></div>' +
                '<div>Active <strong>' + esc(formatNumber(details.active_connections, 0)) + '</strong></div>';
        }

        return '<div class="monitoring-metric-card">' +
            '<div class="metric-card-top"><span>' + esc(metric.label) + '</span><strong>' + esc(value) + '</strong></div>' +
            '<svg id="infra-' + esc(key) + '" class="monitoring-sparkline" viewBox="0 0 640 128"></svg>' +
            '<div class="monitoring-statline">' +
                '<span>Avg <strong>' + esc(formatNumber(metric.avg, 1)) + esc(unit) + '</strong></span>' +
                '<span>Max <strong>' + esc(formatNumber(metric.max, 1)) + esc(unit) + '</strong></span>' +
            '</div>' +
            '<div class="metric-details">' + detailHtml + '</div>' +
        '</div>';
    }

    function loadOverview() {
        return fetch(root.dataset.overviewUrl, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                renderSummary(data.totals || {});
                renderProjects(data.projects || []);
            });
    }

    function loadInfrastructure() {
        return fetch(root.dataset.infraUrl, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                var metrics = data.metrics || {};
                var order = ["cpu", "memory", "disk", "nginx_requests"];
                $("monitoring-infra-grid").innerHTML = order.map(function (key) {
                    return renderMetricCard(key, metrics[key] || {label: key, series: []});
                }).join("");
                drawChart($("infra-cpu"), metrics.cpu && metrics.cpu.series, {small: true, percent: true, unit: "%", color: "#d9534f"});
                drawChart($("infra-memory"), metrics.memory && metrics.memory.series, {small: true, percent: true, unit: "%", color: "#5bc0de"});
                drawChart($("infra-disk"), metrics.disk && metrics.disk.series, {small: true, percent: true, unit: "%", color: "#5cb85c"});
                drawChart($("infra-nginx_requests"), metrics.nginx_requests && metrics.nginx_requests.series, {small: true, unit: "", color: "#337ab7"});
            });
    }

    function liveCronName(item) {
        var name = item.function || "unknown";
        return name.split(".").slice(-2).join(".");
    }

    function renderLiveSummary(data) {
        var totals = data.totals || {};
        var server = data.server || {};
        $("monitoring-live-clock").textContent = "IST " + (data.generated_at_ist || "-");
        $("monitoring-live-summary").innerHTML = [
            ["Running crons", totals.running || 0],
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

    function renderLiveCrons(data) {
        var rows = data.active_crons || [];
        if (!rows.length) {
            $("monitoring-live-crons").innerHTML = '<tr><td colspan="7" class="monitoring-muted">No crons are running right now. This panel updates every second during active runs.</td></tr>';
            return;
        }
        $("monitoring-live-crons").innerHTML = rows.map(function (item) {
            var process = item.process || {};
            var db = item.db || {};
            var staleClass = item.stuck ? " live-stuck" : "";
            return '<tr class="' + staleClass + '">' +
                '<td><strong>' + esc(item.project) + '</strong><br><small>' + esc(liveCronName(item)) + '</small></td>' +
                '<td>' + esc(formatSeconds(item.elapsed_seconds)) + '<br><small>PID ' + esc(item.pid) + '</small></td>' +
                '<td>' + esc(formatNumber(process.cpu_percent, 1)) + '%</td>' +
                '<td>' + esc(formatBytes(process.rss_bytes)) + '<br><small>' + esc(process.threads || "-") + ' threads</small></td>' +
                '<td>' + esc(db.query_count || 0) + '<br><small>slow ' + esc(db.slow_count || 0) + '</small></td>' +
                '<td class="live-stage">' + esc(item.stage || "-") + '</td>' +
                '<td>' + esc(formatSeconds(item.seconds_since_progress)) + '<br><small>' + esc(item.updated_at_ist || "-") + '</small></td>' +
            '</tr>';
        }).join("");
    }

    function renderLiveAlerts(data) {
        var alerts = [];
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

    function loadLive() {
        return fetch(root.dataset.liveUrl, {credentials: "same-origin"})
            .then(function (response) { return response.json(); })
            .then(function (data) {
                renderLiveSummary(data);
                renderLiveCrons(data);
                renderLiveAlerts(data);
            });
    }

    function refresh() {
        loadOverview();
        loadInfrastructure();
        loadLive();
        if (selectedCode) {
            loadCheckSeries(selectedCode);
            loadRuns(selectedCode);
        }
    }

    $("monitoring-refresh").addEventListener("click", refresh);
    refresh();
    setInterval(loadLive, 1000);
    setInterval(refresh, 30000);
    setInterval(function () {
        if (selectedCode) loadExecutionLog(selectedCode, selectedRun);
    }, 10000);
})();
