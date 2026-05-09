# Monitoring Stack

This directory owns the monitoring runtime for AK1111 and HODL. The main app repositories stay in their original locations and are not edited by the monitoring layer.

## Layout

- Healthchecks UI: `/home/ubuntu/monitoring/healthchecks`
- AK1111 wrapper: `/home/ubuntu/monitoring/django/ak1111`
- HODL wrapper: `/home/ubuntu/monitoring/django/hodl`
- Shared wrapper utilities: `/home/ubuntu/monitoring/django/monitoring_common`
- Live observer service: `/home/ubuntu/monitoring/django/monitoring_observer`
- Healthchecks dashboard extension: `/home/ubuntu/monitoring/django/healthchecks_custom`
- Prometheus config and data: `/home/ubuntu/monitoring/prometheus`
- Cron execution logs: `/home/ubuntu/monitoring/logs/crons`
- Live observer runtime state: `/home/ubuntu/monitoring/runtime/observer`
- PM2 source of truth: `/home/ubuntu/monitoring/ecosystem.config.js`

## Services

Healthchecks remains available at `http://43.204.86.173:9000/`. The authenticated monitoring dashboard is at `/monitoring/`.

Prometheus is internal only on `127.0.0.1:9090` and scrapes:

- Healthchecks custom metrics at `127.0.0.1:9000/monitoring/metrics/`
- Node exporter at `127.0.0.1:9100`
- NGINX exporter at `127.0.0.1:9113`

All monitoring-related services are managed by PM2 from `/home/ubuntu/monitoring/ecosystem.config.js`.

`cron-observer` is a PM2-managed Python service that aggregates live cron heartbeat files every second. It does not touch the AK1111 or HODL app repositories.

## Dashboard

The `/monitoring/` dashboard shows:

- AK1111 and HODL cron status from Healthchecks.
- Selected cron duration charts with latest, average, and max duration.
- Infrastructure cards for CPU, memory, disk, and NGINX requests.
- Memory and disk total/used/free values.
- NGINX request rate, active connections, and requests over the graph window.
- Healthchecks ping/event logs through the existing check log links.
- Real cron execution logs captured by the monitoring wrapper.
- Checks with no first ping are labeled `waiting first run` instead of the raw Healthchecks `new` state.
- Cron rows include the next expected run time, calculated from the cron schedule and timezone.
- Rows are sorted by operational priority: `down`, `grace`, `up`, `waiting first run`, then `paused`.
- Live Cron Observer shows running crons, elapsed time, current stage, PID, CPU/RAM, DB query counts, slow queries, stale heartbeats, and IST timestamps.
- `/monitoring/api/live/` returns the live observer state for the dashboard.
- `/monitoring/api/checks/<uuid>/live/` returns live observer state for one cron.

`waiting first run` means Healthchecks has not received any ping for that check yet. This is normal for daily jobs until their first scheduled execution after the check was created.

## Cron Execution Logs

Cron execution logs are captured without editing the main app repos. The wrappers monkey-patch configured cron functions and capture:

- Start and end timestamps.
- Project, cron function, Healthchecks UUID, and run ID.
- Status, duration, stdout, stderr, Python logging output, and tracebacks.
- Per-second heartbeat state including UTC/IST timestamps, PID, elapsed time, CPU/RAM from `/proc`, stack summary, DB query counts, latest query, slow query count, and stuck/no-progress warning.

Logs are stored as:

```text
/home/ubuntu/monitoring/logs/crons/<project>/<healthchecks-uuid>/<run-id>.log
/home/ubuntu/monitoring/logs/crons/<project>/<healthchecks-uuid>/<run-id>.json
/home/ubuntu/monitoring/runtime/observer/heartbeats/<project>/<healthchecks-uuid>/<run-id>.json
```

If a cron function does not print or log internal progress, the execution log will still show wrapper start/end/status/duration, but it cannot show hidden business steps without adding logging inside the app code.

## GitHub Versioning

The monitoring codebase is pushed as a sanitized repository. Runtime files are excluded by `.gitignore`, including:

- `healthchecks/hc/local_settings.py`
- `healthchecks/search.db`
- `healthchecks/venv/`
- `logs/`
- `runtime/`
- `prometheus/data/`
- `healthchecks/.git/`

Only monitoring code, safe configuration, scripts, and documentation should be committed.

## Common Commands

```bash
pm2 startOrReload /home/ubuntu/monitoring/ecosystem.config.js --update-env
pm2 save
/home/ubuntu/monitoring/bin/reload-crontabs.sh
/home/ubuntu/monitoring/bin/check-monitoring.sh
curl http://127.0.0.1:9090/api/v1/targets
curl http://127.0.0.1:9000/monitoring/api/live/
```
