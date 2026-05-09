"""
Monitoring dashboard that aggregates data from Healthchecks API
and serves a single-page dashboard with performance graphs.
"""

import json
import os
from datetime import datetime, timezone

import requests
from django.http import HttpResponse

# Healthchecks API config
HC_BASE = "http://localhost:9000"
PROJECTS = {
    "ak1111-backend": {
        "api_key": os.environ.get("AK1111_HEALTHCHECKS_API_KEY", ""),
        "health_url": "http://localhost:8000/api/monitoring/health/",
    },
    "HODL-2025": {
        "api_key": os.environ.get("HODL_HEALTHCHECKS_API_KEY", ""),
        "health_url": "http://localhost:8001/api/monitoring/health/",
    },
}


def _fetch_checks(api_key):
    """Fetch all checks for a project from Healthchecks API."""
    try:
        resp = requests.get(
            f"{HC_BASE}/api/v1/checks/",
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("checks", [])
    except Exception:
        return []


def _fetch_pings(api_key, check_uuid):
    """Fetch recent pings for a check."""
    try:
        resp = requests.get(
            f"{HC_BASE}/api/v1/checks/{check_uuid}/pings/",
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("pings", [])
    except Exception:
        return []


def _fetch_flips(api_key, check_uuid):
    """Fetch status flips for a check."""
    try:
        resp = requests.get(
            f"{HC_BASE}/api/v1/checks/{check_uuid}/flips/",
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def _fetch_health(url):
    """Fetch server health data."""
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"status": "unreachable", "checks": {}}


def dashboard_api(request):
    """JSON API endpoint that returns all dashboard data."""
    data = {"projects": {}, "generated_at": datetime.now(timezone.utc).isoformat()}

    for project_name, config in PROJECTS.items():
        checks = _fetch_checks(config["api_key"])
        health = _fetch_health(config["health_url"])

        project_data = {
            "health": health,
            "checks": [],
            "summary": {"total": 0, "up": 0, "down": 0, "grace": 0, "new": 0, "paused": 0},
        }

        for check in checks:
            check_info = {
                "name": check.get("name", ""),
                "slug": check.get("slug", ""),
                "tags": check.get("tags", ""),
                "status": check.get("status", "new"),
                "last_ping": check.get("last_ping"),
                "last_duration": check.get("last_duration"),
                "schedule": check.get("schedule", ""),
                "tz": check.get("tz", "UTC"),
                "uuid": check.get("ping_url", "").split("/")[-1] if check.get("ping_url") else "",
                "desc": check.get("desc", ""),
            }

            # Use n_pings to see if it has any history
            check_info["n_pings"] = check.get("n_pings", 0)

            project_data["checks"].append(check_info)
            project_data["summary"]["total"] += 1
            status = check.get("status", "new")
            if status in project_data["summary"]:
                project_data["summary"][status] += 1

        # Sort: down first, then up, then new
        status_order = {"down": 0, "grace": 1, "up": 2, "paused": 3, "new": 4}
        project_data["checks"].sort(key=lambda c: (status_order.get(c["status"], 5), c["name"]))

        data["projects"][project_name] = project_data

    return HttpResponse(
        json.dumps(data, default=str),
        content_type="application/json",
    )


def dashboard_pings_api(request):
    """Fetch ping history for a specific check. Query params: project, uuid."""
    project_name = request.GET.get("project", "")
    check_uuid = request.GET.get("uuid", "")

    if project_name not in PROJECTS or not check_uuid:
        return HttpResponse(
            json.dumps({"error": "Missing project or uuid"}),
            content_type="application/json",
            status=400,
        )

    api_key = PROJECTS[project_name]["api_key"]
    pings = _fetch_pings(api_key, check_uuid)
    flips = _fetch_flips(api_key, check_uuid)

    return HttpResponse(
        json.dumps({"pings": pings, "flips": flips}, default=str),
        content_type="application/json",
    )


def dashboard_view(request):
    """Serve the monitoring dashboard HTML page."""
    return HttpResponse(DASHBOARD_HTML, content_type="text/html")


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>1111Swap Monitoring Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --surface2: #232734;
    --border: #2d3348; --text: #e4e6ed; --text2: #8b8fa3;
    --green: #22c55e; --red: #ef4444; --yellow: #eab308;
    --blue: #3b82f6; --purple: #a855f7; --cyan: #06b6d4;
    --green-bg: rgba(34,197,94,0.12); --red-bg: rgba(239,68,68,0.12);
    --yellow-bg: rgba(234,179,8,0.12); --blue-bg: rgba(59,130,246,0.12);
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }

  .header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
  .header h1 { font-size: 20px; font-weight: 600; }
  .header h1 span { color: var(--blue); }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .refresh-btn { background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; transition: all 0.2s; }
  .refresh-btn:hover { background: var(--blue); border-color: var(--blue); }
  .last-updated { font-size: 12px; color: var(--text2); }
  .auto-refresh { font-size: 12px; color: var(--text2); display: flex; align-items: center; gap: 6px; }
  .auto-refresh input { accent-color: var(--blue); }

  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }

  /* Summary cards */
  .summary-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .summary-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
  .summary-card .label { font-size: 12px; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  .summary-card .value { font-size: 28px; font-weight: 700; }
  .summary-card .sub { font-size: 12px; color: var(--text2); margin-top: 4px; }
  .summary-card.ok .value { color: var(--green); }
  .summary-card.warn .value { color: var(--yellow); }
  .summary-card.bad .value { color: var(--red); }
  .summary-card.info .value { color: var(--blue); }

  /* Server health */
  .health-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .health-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
  .health-card h3 { font-size: 14px; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
  .health-card h3 .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .health-card h3 .dot.ok { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .health-card h3 .dot.bad { background: var(--red); box-shadow: 0 0 8px var(--red); }
  .health-metric { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border); }
  .health-metric:last-child { border-bottom: none; }
  .health-metric .name { font-size: 13px; color: var(--text2); }
  .health-metric .val { font-size: 14px; font-weight: 600; }
  .progress-bar { width: 100px; height: 6px; background: var(--surface2); border-radius: 3px; overflow: hidden; margin-left: 12px; }
  .progress-bar .fill { height: 100%; border-radius: 3px; transition: width 0.3s; }

  /* Project sections */
  .project-section { margin-bottom: 32px; }
  .project-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .project-title { font-size: 18px; font-weight: 600; }
  .project-badges { display: flex; gap: 8px; }
  .badge { padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
  .badge.up { background: var(--green-bg); color: var(--green); }
  .badge.down { background: var(--red-bg); color: var(--red); }
  .badge.new { background: var(--blue-bg); color: var(--blue); }
  .badge.grace { background: var(--yellow-bg); color: var(--yellow); }

  /* Checks table */
  .checks-table { width: 100%; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .checks-table table { width: 100%; border-collapse: collapse; }
  .checks-table th { text-align: left; padding: 12px 16px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); background: var(--surface2); border-bottom: 1px solid var(--border); }
  .checks-table td { padding: 12px 16px; border-bottom: 1px solid var(--border); font-size: 13px; }
  .checks-table tr:last-child td { border-bottom: none; }
  .checks-table tr:hover { background: var(--surface2); }
  .checks-table tr { cursor: pointer; transition: background 0.15s; }

  .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 8px; }
  .status-dot.up { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .status-dot.down { background: var(--red); box-shadow: 0 0 6px var(--red); animation: pulse 2s infinite; }
  .status-dot.grace { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }
  .status-dot.new { background: var(--text2); }
  .status-dot.paused { background: var(--purple); }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }

  .check-name { font-weight: 500; }
  .check-tags { font-size: 11px; color: var(--text2); margin-top: 2px; }
  .duration-bar { display: inline-block; height: 16px; background: var(--blue); border-radius: 3px; min-width: 2px; opacity: 0.7; vertical-align: middle; margin-right: 6px; }
  .time-ago { color: var(--text2); }

  /* Modal for ping details */
  .modal-overlay { display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.7); z-index:200; justify-content:center; align-items:center; }
  .modal-overlay.active { display:flex; }
  .modal { background:var(--surface); border:1px solid var(--border); border-radius:16px; width:90%; max-width:900px; max-height:85vh; overflow-y:auto; padding:24px; }
  .modal h2 { font-size:18px; margin-bottom:4px; }
  .modal .modal-sub { font-size:13px; color:var(--text2); margin-bottom:20px; }
  .modal-close { position:absolute; top:16px; right:16px; background:none; border:none; color:var(--text2); font-size:24px; cursor:pointer; }
  .modal-charts { display: grid; grid-template-columns: 1fr; gap: 20px; }
  .chart-container { background: var(--surface2); border-radius: 8px; padding: 16px; }
  .chart-container h4 { font-size: 13px; color: var(--text2); margin-bottom: 12px; }
  .chart-container canvas { max-height: 200px; }

  /* Ping log */
  .ping-log { max-height: 300px; overflow-y: auto; }
  .ping-entry { display: flex; align-items: center; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 12px; }
  .ping-entry .ping-type { width: 50px; text-align: center; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px; }
  .ping-entry .ping-type.success { background: var(--green-bg); color: var(--green); }
  .ping-entry .ping-type.start { background: var(--blue-bg); color: var(--blue); }
  .ping-entry .ping-type.fail { background: var(--red-bg); color: var(--red); }
  .ping-entry .ping-time { color: var(--text2); width: 160px; }
  .ping-entry .ping-duration { color: var(--cyan); width: 80px; }

  /* Loading */
  .loading { text-align:center; padding:60px; color:var(--text2); }
  .loading .spinner { width:40px; height:40px; border:3px solid var(--border); border-top-color:var(--blue); border-radius:50%; animation:spin 0.8s linear infinite; margin:0 auto 16px; }
  @keyframes spin { to { transform:rotate(360deg); } }

  /* Responsive */
  @media (max-width: 768px) {
    .container { padding: 12px; }
    .summary-row { grid-template-columns: repeat(2, 1fr); }
    .health-grid { grid-template-columns: 1fr; }
    .checks-table td, .checks-table th { padding: 8px 10px; }
  }
</style>
</head>
<body>

<div class="header">
  <h1><span>1111Swap</span> Monitoring</h1>
  <div class="header-right">
    <label class="auto-refresh">
      <input type="checkbox" id="autoRefresh" checked> Auto-refresh 30s
    </label>
    <span class="last-updated" id="lastUpdated"></span>
    <button class="refresh-btn" onclick="loadDashboard()">Refresh</button>
  </div>
</div>

<div class="container">
  <div id="loading" class="loading">
    <div class="spinner"></div>
    Loading monitoring data...
  </div>
  <div id="content" style="display:none">
    <div class="summary-row" id="summaryCards"></div>
    <div class="health-grid" id="healthGrid"></div>
    <div id="projectSections"></div>
  </div>
</div>

<!-- Detail Modal -->
<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal" style="position:relative">
    <button class="modal-close" onclick="closeModal()">&times;</button>
    <h2 id="modalTitle"></h2>
    <div class="modal-sub" id="modalSub"></div>
    <div class="modal-charts" id="modalCharts"></div>
  </div>
</div>

<script>
const API_URL = window.location.pathname.replace('/dashboard/', '/dashboard/api/');
const PINGS_URL = window.location.pathname.replace('/dashboard/', '/dashboard/pings/');
let dashboardData = null;
let refreshTimer = null;

async function loadDashboard() {
  try {
    const resp = await fetch(API_URL);
    dashboardData = await resp.json();
    renderDashboard(dashboardData);
    document.getElementById('loading').style.display = 'none';
    document.getElementById('content').style.display = 'block';
    document.getElementById('lastUpdated').textContent = 'Updated: ' + new Date().toLocaleTimeString();
  } catch (err) {
    document.getElementById('loading').innerHTML = '<div style="color:var(--red)">Failed to load data: ' + err.message + '</div>';
  }
}

function renderDashboard(data) {
  renderSummary(data);
  renderHealth(data);
  renderProjects(data);
}

function renderSummary(data) {
  let totalUp = 0, totalDown = 0, totalNew = 0, totalChecks = 0;
  for (const [name, proj] of Object.entries(data.projects)) {
    totalChecks += proj.summary.total;
    totalUp += proj.summary.up;
    totalDown += proj.summary.down;
    totalNew += proj.summary.new;
  }
  const html = `
    <div class="summary-card ${totalDown === 0 ? 'ok' : 'bad'}">
      <div class="label">Overall Status</div>
      <div class="value">${totalDown === 0 ? 'HEALTHY' : 'ISSUES'}</div>
      <div class="sub">${totalChecks} total checks across ${Object.keys(data.projects).length} projects</div>
    </div>
    <div class="summary-card ok">
      <div class="label">Checks Passing</div>
      <div class="value">${totalUp}</div>
      <div class="sub">Running successfully</div>
    </div>
    <div class="summary-card ${totalDown > 0 ? 'bad' : 'ok'}">
      <div class="label">Checks Failing</div>
      <div class="value">${totalDown}</div>
      <div class="sub">${totalDown > 0 ? 'Needs attention' : 'All clear'}</div>
    </div>
    <div class="summary-card info">
      <div class="label">Awaiting First Ping</div>
      <div class="value">${totalNew}</div>
      <div class="sub">Scheduled but not yet run</div>
    </div>
  `;
  document.getElementById('summaryCards').innerHTML = html;
}

function renderHealth(data) {
  let html = '';
  for (const [name, proj] of Object.entries(data.projects)) {
    const h = proj.health;
    const isOk = h.status === 'ok';
    const db = h.checks?.database || {};
    const disk = h.checks?.disk || {};
    const mem = h.checks?.memory || {};

    const diskPct = disk.usage_percent || 0;
    const memPct = mem.usage_percent || 0;
    const diskColor = diskPct > 80 ? 'var(--red)' : diskPct > 60 ? 'var(--yellow)' : 'var(--green)';
    const memColor = memPct > 80 ? 'var(--red)' : memPct > 60 ? 'var(--yellow)' : 'var(--green)';

    html += `
      <div class="health-card">
        <h3><span class="dot ${isOk ? 'ok' : 'bad'}"></span>${name} Server</h3>
        <div class="health-metric">
          <span class="name">Database</span>
          <span class="val" style="color:${db.ok ? 'var(--green)' : 'var(--red)'}">
            ${db.ok ? db.latency_ms + ' ms' : 'DOWN'}
          </span>
        </div>
        <div class="health-metric">
          <span class="name">Disk Usage</span>
          <div style="display:flex;align-items:center">
            <span class="val">${diskPct}%</span>
            <div class="progress-bar"><div class="fill" style="width:${diskPct}%;background:${diskColor}"></div></div>
          </div>
        </div>
        <div class="health-metric">
          <span class="name">Disk Free</span>
          <span class="val">${disk.free_gb || 0} GB</span>
        </div>
        <div class="health-metric">
          <span class="name">Memory Usage</span>
          <div style="display:flex;align-items:center">
            <span class="val">${memPct}%</span>
            <div class="progress-bar"><div class="fill" style="width:${memPct}%;background:${memColor}"></div></div>
          </div>
        </div>
        <div class="health-metric">
          <span class="name">Memory Available</span>
          <span class="val">${(mem.available_mb/1024).toFixed(1)} GB / ${(mem.total_mb/1024).toFixed(1)} GB</span>
        </div>
      </div>
    `;
  }
  document.getElementById('healthGrid').innerHTML = html;
}

function renderProjects(data) {
  let html = '';
  for (const [name, proj] of Object.entries(data.projects)) {
    html += `
      <div class="project-section">
        <div class="project-header">
          <span class="project-title">${name}</span>
          <div class="project-badges">
            ${proj.summary.up > 0 ? `<span class="badge up">${proj.summary.up} up</span>` : ''}
            ${proj.summary.down > 0 ? `<span class="badge down">${proj.summary.down} down</span>` : ''}
            ${proj.summary.grace > 0 ? `<span class="badge grace">${proj.summary.grace} grace</span>` : ''}
            ${proj.summary.new > 0 ? `<span class="badge new">${proj.summary.new} new</span>` : ''}
          </div>
        </div>
        <div class="checks-table">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Name</th>
                <th>Schedule</th>
                <th>Last Ping</th>
                <th>Duration</th>
                <th>Pings</th>
              </tr>
            </thead>
            <tbody>
    `;
    for (const check of proj.checks) {
      const lastPing = check.last_ping ? timeAgo(new Date(check.last_ping)) : 'Never';
      const duration = check.last_duration != null ? check.last_duration.toFixed(1) + 's' : '-';
      const maxDur = 30;
      const durWidth = check.last_duration ? Math.min(check.last_duration / maxDur * 60, 60) : 0;
      const durColor = check.last_duration > 10 ? 'var(--red)' : check.last_duration > 5 ? 'var(--yellow)' : 'var(--blue)';

      html += `
        <tr onclick="openDetail('${name}','${check.uuid}','${escHtml(check.name)}','${check.schedule || ''}')">
          <td><span class="status-dot ${check.status}"></span>${check.status.toUpperCase()}</td>
          <td>
            <div class="check-name">${escHtml(check.name)}</div>
            ${check.tags ? `<div class="check-tags">${escHtml(check.tags)}</div>` : ''}
          </td>
          <td><code style="font-size:12px;color:var(--cyan)">${check.schedule || '-'}</code></td>
          <td><span class="time-ago">${lastPing}</span></td>
          <td>
            ${durWidth > 0 ? `<span class="duration-bar" style="width:${durWidth}px;background:${durColor}"></span>` : ''}
            ${duration}
          </td>
          <td>${check.n_pings}</td>
        </tr>
      `;
    }
    html += '</tbody></table></div></div>';
  }
  document.getElementById('projectSections').innerHTML = html;
}

async function openDetail(project, uuid, name, schedule) {
  document.getElementById('modalTitle').textContent = name;
  document.getElementById('modalSub').textContent = `Project: ${project} | Schedule: ${schedule || 'N/A'} | UUID: ${uuid}`;
  document.getElementById('modalCharts').innerHTML = '<div class="loading"><div class="spinner"></div>Loading ping history...</div>';
  document.getElementById('modalOverlay').classList.add('active');

  try {
    const resp = await fetch(`${PINGS_URL}?project=${encodeURIComponent(project)}&uuid=${uuid}`);
    const data = await resp.json();
    renderDetail(data, name);
  } catch (err) {
    document.getElementById('modalCharts').innerHTML = '<div style="color:var(--red)">Failed to load: ' + err.message + '</div>';
  }
}

function renderDetail(data, name) {
  const pings = data.pings || [];
  const flips = data.flips || [];

  // Build duration chart data (only success pings with duration)
  const durations = [];
  const successTimes = [];
  const failTimes = [];

  for (const p of pings) {
    const ts = new Date(p.date);
    if (p.type === '' && p.duration != null) {
      durations.push({ x: ts, y: p.duration });
    }
    if (p.type === '' || p.type === 'success') {
      successTimes.push(ts);
    }
    if (p.type === 'fail') {
      failTimes.push(ts);
    }
  }

  let html = '';

  // Duration chart
  html += `
    <div class="chart-container">
      <h4>Execution Duration (seconds)</h4>
      <canvas id="durationChart"></canvas>
    </div>
  `;

  // Status timeline
  html += `
    <div class="chart-container">
      <h4>Status Timeline</h4>
      <canvas id="statusChart"></canvas>
    </div>
  `;

  // Recent pings log
  html += '<div class="chart-container"><h4>Recent Pings</h4><div class="ping-log">';
  for (const p of pings.slice(0, 50)) {
    const typeClass = p.type === 'fail' ? 'fail' : p.type === 'start' ? 'start' : 'success';
    const typeLabel = p.type === '' ? 'OK' : p.type === 'start' ? 'START' : p.type.toUpperCase();
    const dur = p.duration != null ? p.duration.toFixed(2) + 's' : '-';
    html += `
      <div class="ping-entry">
        <span class="ping-type ${typeClass}">${typeLabel}</span>
        <span class="ping-time">${new Date(p.date).toLocaleString()}</span>
        <span class="ping-duration">${dur}</span>
      </div>
    `;
  }
  html += '</div></div>';

  document.getElementById('modalCharts').innerHTML = html;

  // Render duration chart
  if (durations.length > 0) {
    const ctx1 = document.getElementById('durationChart').getContext('2d');
    new Chart(ctx1, {
      type: 'line',
      data: {
        datasets: [{
          label: 'Duration (s)',
          data: durations.reverse(),
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 3,
          pointBackgroundColor: '#3b82f6',
        }]
      },
      options: {
        responsive: true,
        scales: {
          x: { type: 'time', time: { tooltipFormat: 'MMM d, HH:mm' }, grid: { color: '#2d3348' }, ticks: { color: '#8b8fa3' } },
          y: { beginAtZero: true, grid: { color: '#2d3348' }, ticks: { color: '#8b8fa3', callback: v => v + 's' } }
        },
        plugins: { legend: { display: false } }
      }
    });
  }

  // Render status timeline
  if (flips.length > 0) {
    const flipData = flips.map(f => ({
      x: new Date(f.timestamp),
      y: f.up === 1 ? 1 : 0
    }));
    const ctx2 = document.getElementById('statusChart').getContext('2d');
    new Chart(ctx2, {
      type: 'line',
      data: {
        datasets: [{
          label: 'Status',
          data: flipData,
          borderColor: flipData[flipData.length-1]?.y === 1 ? '#22c55e' : '#ef4444',
          backgroundColor: 'rgba(34,197,94,0.1)',
          fill: true,
          stepped: true,
          pointRadius: 4,
        }]
      },
      options: {
        responsive: true,
        scales: {
          x: { type: 'time', grid: { color: '#2d3348' }, ticks: { color: '#8b8fa3' } },
          y: { min: -0.1, max: 1.1, grid: { color: '#2d3348' }, ticks: { color: '#8b8fa3', callback: v => v === 1 ? 'UP' : v === 0 ? 'DOWN' : '' } }
        },
        plugins: { legend: { display: false } }
      }
    });
  } else if (pings.length > 0) {
    // Build status from pings if no flips
    const statusData = pings.slice(0, 100).reverse().filter(p => p.type !== 'start').map(p => ({
      x: new Date(p.date),
      y: p.type === 'fail' ? 0 : 1
    }));
    if (statusData.length > 0) {
      const ctx2 = document.getElementById('statusChart').getContext('2d');
      new Chart(ctx2, {
        type: 'line',
        data: {
          datasets: [{
            label: 'Status',
            data: statusData,
            borderColor: statusData[statusData.length-1]?.y === 1 ? '#22c55e' : '#ef4444',
            backgroundColor: statusData[statusData.length-1]?.y === 1 ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
            fill: true,
            stepped: true,
            pointRadius: 3,
            pointBackgroundColor: statusData.map(d => d.y === 1 ? '#22c55e' : '#ef4444'),
          }]
        },
        options: {
          responsive: true,
          scales: {
            x: { type: 'time', grid: { color: '#2d3348' }, ticks: { color: '#8b8fa3' } },
            y: { min: -0.1, max: 1.1, grid: { color: '#2d3348' }, ticks: { color: '#8b8fa3', callback: v => v === 1 ? 'UP' : v === 0 ? 'DOWN' : '' } }
          },
          plugins: { legend: { display: false } }
        }
      });
    }
  }
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('active');
}

function timeAgo(date) {
  const secs = Math.floor((new Date() - date) / 1000);
  if (secs < 60) return secs + 's ago';
  if (secs < 3600) return Math.floor(secs/60) + 'm ago';
  if (secs < 86400) return Math.floor(secs/3600) + 'h ago';
  return Math.floor(secs/86400) + 'd ago';
}

function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// Auto-refresh
function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  if (document.getElementById('autoRefresh').checked) {
    refreshTimer = setInterval(loadDashboard, 30000);
  }
}

document.getElementById('autoRefresh').addEventListener('change', startAutoRefresh);

// ESC to close modal
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

// Init
loadDashboard();
startAutoRefresh();
</script>
</body>
</html>
"""
