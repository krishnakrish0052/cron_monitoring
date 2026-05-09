# HODL Crons Monitoring

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

Healthchecks remains available at `http://43.204.86.173:9000/`. The authenticated monitoring dashboard is branded as `HODL Crons Monitoring` and is available at `/monitoring/`.

Prometheus is internal only on `127.0.0.1:9090` and scrapes:

- Healthchecks custom metrics at `127.0.0.1:9000/monitoring/metrics/`
- Node exporter at `127.0.0.1:9100`
- NGINX exporter at `127.0.0.1:9113`

All monitoring-related services are managed by PM2 from `/home/ubuntu/monitoring/ecosystem.config.js`.

`cron-observer` is a PM2-managed Python service that aggregates live cron heartbeat files every second. It does not touch the AK1111 or HODL app repositories.

## Dashboard

The `/monitoring/` dashboard shows:

- AK1111 and HODL cron status from Healthchecks.
- AMOLED black UI with the HODL Crons Monitoring logo and GitHub footer link.
- Selected cron duration charts with latest, average, and max duration.
- Infrastructure cards for CPU, memory, disk, and NGINX requests using the observer's shared live metric source.
- CPU cards label `live 1s`, `avg`, and `max 1h` so values do not appear inconsistent.
- Memory and disk total/used/free values.
- NGINX request rate, active connections, and requests over the graph window.
- Healthchecks ping/event logs through the existing check log links.
- Real cron execution logs captured by the monitoring wrapper.
- Checks with no first ping are labeled `waiting first run` instead of the raw Healthchecks `new` state.
- Cron rows include the next expected run time, calculated from the cron schedule and timezone.
- Rows are sorted by operational priority: `down`, `grace`, `up`, `waiting first run`, then `paused`.
- Live Cron Observer shows running crons, elapsed time, current stage, PID, CPU/RAM, DB query counts, HTTP counts, slow queries, stale heartbeats, and IST timestamps.
- Recently Finished shows completed runs with trace summaries.
- External API Errors surfaces BscScan/BaseScan/Etherscan response-shape failures detected by the wrapper.
- `/monitoring/api/live/` returns the live observer state for the dashboard.
- `/monitoring/api/checks/<uuid>/live/` returns live observer state for one cron.
- All user-facing dashboard times are shown in IST. Raw JSON still includes UTC fields for debugging.

`waiting first run` means Healthchecks has not received any ping for that check yet. This is normal for daily jobs until their first scheduled execution after the check was created.

## Cron Execution Logs

Cron execution logs are captured without editing the main app repos. The wrappers monkey-patch configured cron functions and capture:

- Start and end timestamps.
- Project, cron function, Healthchecks UUID, and run ID.
- Status, duration, stdout, stderr, Python logging output, and tracebacks.
- Structured `.events.jsonl` traces for stdout, stderr, logging, DB queries, HTTP calls, Python trace events, failures, and run end.
- HTTP calls are traced through `requests.Session.request` with sanitized URLs, status code, duration, result type/count, and external API classification.
- Django DB calls are traced through `connection.execute_wrapper` with query duration, operation/table summary, fingerprint, latest query, and slow-query markers.
- Bounded Python tracing records app-file call/line/return/exception events without editing app repos.
- Per-second heartbeat state including UTC/IST timestamps, PID, elapsed time, CPU/RAM from `/proc`, stack summary, DB query counts, HTTP counts, latest trace, latest query, slow query count, and stuck/no-progress warning.

Logs are stored as:

```text
/home/ubuntu/monitoring/logs/crons/<project>/<healthchecks-uuid>/<run-id>.log
/home/ubuntu/monitoring/logs/crons/<project>/<healthchecks-uuid>/<run-id>.json
/home/ubuntu/monitoring/logs/crons/<project>/<healthchecks-uuid>/<run-id>.events.jsonl
/home/ubuntu/monitoring/runtime/observer/heartbeats/<project>/<healthchecks-uuid>/<run-id>.json
```

If a cron function only prints `success`, the dashboard still infers progress from HTTP calls, DB queries, stack snapshots, and Python trace events. It cannot invent app-specific business step names unless the app emits them.

## Known App-Side Cron Failure Pattern

Recent traces show several AK1111/HODL cron failures are not Healthchecks ping problems. The wrapper successfully calls the cron, then the app cron code receives BscScan/BaseScan/Etherscan-style JSON where `result` is a string error instead of a transaction list.

Typical root cause surfaced by monitoring:

```text
External explorer API returned deprecated V1 endpoint error; app code expects a transaction list.
TypeError: string indices must be integers, not 'str'
```

Fixing those endpoint/API parser issues belongs in the AK1111/HODL app repos and should be handled separately from this monitoring-only codebase.

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
