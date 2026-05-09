# Monitoring Rollout

## What Changed

- Consolidated monitoring under `/home/ubuntu/monitoring`.
- Added a richer authenticated Healthchecks dashboard at `/monitoring/`.
- Added Prometheus-backed infrastructure graphs for CPU, memory, disk, and NGINX.
- Added wrapper-only cron execution logging under `/home/ubuntu/monitoring/logs/crons`.
- Renamed first-run checks in the dashboard from raw `new` to `waiting first run`.
- Added next expected run times to cron rows.
- Sorted cron rows so active problems appear before jobs waiting for their first scheduled run.
- Added `cron-observer`, a PM2-managed Python service for per-second live cron state.
- Added live heartbeat tracking with PID, elapsed time, CPU/RAM, DB query counts, slow query detection, stack summaries, and IST timestamps.
- Added `/monitoring/api/live/` and `/monitoring/api/checks/<uuid>/live/`.
- Rebranded the UI to `HODL Crons Monitoring` with an AMOLED black dashboard and monitoring-owned SVG logo.
- Replaced mixed CPU windows with one observer-owned live metric source. CPU is labeled as live 1s, average, and max 1h.
- Added structured `.events.jsonl` trace streams for stdout/stderr/logging, DB queries, HTTP requests, Python trace events, failures, and run end.
- Added HTTP/API classification for BscScan/BaseScan/Etherscan response-shape errors, including deprecated V1 endpoint responses.
- Converted dashboard-facing times, graph labels, run history, and live panels to IST.
- Added an operator Action Center, sticky quick navigation, and cron table search/status filters so the dashboard is easier to use during incidents.
- Added sanitized Git versioning for `https://github.com/krishnakrish0052/cron_monitoring.git`.
- Kept Healthchecks ping/event logs as the canonical ping history.
- Kept Prometheus internal only and did not add Grafana.

## Deploy

```bash
cd /home/ubuntu/monitoring/healthchecks
DEBUG=False PYTHONPATH=/home/ubuntu/monitoring/django SITE_ROOT=http://43.204.86.173:9000 ALLOWED_HOSTS=43.204.86.173,localhost,127.0.0.1 venv/bin/python manage.py check
DEBUG=False PYTHONPATH=/home/ubuntu/monitoring/django SITE_ROOT=http://43.204.86.173:9000 ALLOWED_HOSTS=43.204.86.173,localhost,127.0.0.1 venv/bin/python manage.py collectstatic --noinput
DEBUG=False PYTHONPATH=/home/ubuntu/monitoring/django SITE_ROOT=http://43.204.86.173:9000 ALLOWED_HOSTS=43.204.86.173,localhost,127.0.0.1 venv/bin/python manage.py compress --force

promtool check config /home/ubuntu/monitoring/prometheus/prometheus.yml
pm2 startOrReload /home/ubuntu/monitoring/ecosystem.config.js --update-env
pm2 save
```

Before pushing code, verify runtime files are excluded:

```bash
git status --short
git diff --cached --name-only
git check-ignore healthchecks/hc/local_settings.py healthchecks/search.db healthchecks/venv/bin/python logs/crons prometheus/data runtime/observer
```

Reload crontabs if wrapper paths or cron settings change:

```bash
/home/ubuntu/monitoring/bin/reload-crontabs.sh
```

## Verify

```bash
/home/ubuntu/monitoring/bin/check-monitoring.sh
curl -I http://43.204.86.173:9000/
curl -I http://127.0.0.1:9000/monitoring/
curl http://127.0.0.1:9000/monitoring/api/live/
curl http://127.0.0.1:9090/api/v1/query?query=up
curl http://127.0.0.1:9090/api/v1/targets
```

Expected results:

- PM2 shows `backend`, `hodl-backend`, `healthchecks-web`, `healthchecks-alerts`, `healthchecks-reports`, and `prometheus` online.
- `/monitoring/` redirects unauthenticated users to login.
- Prometheus targets are up for Healthchecks, node exporter, and NGINX exporter.
- PM2 shows `cron-observer` online.
- `/monitoring/api/live/` returns `generated_at_ist`, `active_crons`, `recent_runs`, and aggregate cron CPU/RAM usage.
- `/monitoring/api/live/` also returns `server_series`, `external_errors`, and recent trace events for completed runs.
- `/monitoring/api/checks/<uuid>/live/` returns the active heartbeat, stale heartbeat, and last completed run trace for one cron.
- After a monitored cron runs, a `.json` metadata file and `.log` execution file appear in `/home/ubuntu/monitoring/logs/crons`.
- After a monitored cron runs, a `.events.jsonl` file appears beside the `.log` and `.json` metadata files.
- While a monitored cron runs, a heartbeat file appears in `/home/ubuntu/monitoring/runtime/observer/heartbeats`.
- Dashboard-visible timestamps show IST.
- Daily checks that have never run show `waiting first run` and a future next-run time, not `down`.

## Known App-Side Issue Surfaced By Monitoring

Some current AK1111/HODL failures are real cron code failures, not Healthchecks ping failures. Several app functions call explorer APIs and assume `data["result"]` is a list. When the external API returns a string error such as a deprecated V1 endpoint warning, the app code raises `TypeError: string indices must be integers, not 'str'`.

This rollout classifies and displays that root cause. It intentionally does not edit the AK1111 or HODL app repositories.

Follow-up on 2026-05-09: the AK1111 server checkout was patched locally for the same explorer response-shape issue in `lplock.utils.fetch_data.fetchInvestmentsFromBlockchain` and `dex.utils.fetch_investments.fetchTokenInvestments`. The fix adds `config.explorer.fetch_normal_transactions()`, uses Etherscan V2-style `chainid=8453`, prefers `ETHERSCAN_API_KEY` with `BASESCAN_API_KEY` fallback, validates that `result` is a list before transaction processing, records classified explorer errors in `AppLogs`, and does not advance block checkpoints on malformed explorer responses. The AK1111 checkout on this server is not a Git repository, so this local patch must be ported to the deployment source repository before a Buddy deploy.

## HODL App-Side Cronops Rollout

On 2026-05-09, HODL app code was updated on branch `cron-reliability-monitoring` to fix the HODL side of the issue:

- Added the `cronops` Django app and applied migration `cronops.0001_initial`.
- Added per-cron DB locks so the same HODL cron cannot stack duplicate copies while another run is active.
- Added DB-backed HODL cron runs, events, checkpoints, DB query counters, slow query counters, memory samples, and stale/no-progress fields.
- Fixed HODL explorer fetchers for Truebreath BSC, Truebreath Base, and AK1111 Super Nodes to use Etherscan V2 request shape and validate `result` before processing.
- Added app-emitted progress/checkpoints for long LP/SVR4/Korean/blackcard cron workflows.
- Updated Healthchecks custom monitoring views to merge `http://127.0.0.1:8001/api/cronops/live/` into `/monitoring/api/live/`.

Verification performed:

```bash
cd /home/ubuntu/hodlbackend2/HODL-2025
/home/ubuntu/hodlbackend2/HODL-2025/venv/bin/python manage.py check
/home/ubuntu/hodlbackend2/HODL-2025/venv/bin/python manage.py makemigrations --check --dry-run
/home/ubuntu/hodlbackend2/HODL-2025/venv/bin/python manage.py migrate cronops
curl http://127.0.0.1:8001/api/cronops/live/
```

Operational note: `ETHERSCAN_API_KEY` should be added to the HODL deployment environment. Existing `BSCSCAN_API_KEY` and `BASESCAN_API_KEY` are present, but Etherscan V2 is designed around the unified Etherscan API key.

## Rollback

Restart from the previous PM2 dump if needed:

```bash
pm2 resurrect
```

If only the new dashboard code is faulty, revert the Healthchecks custom files and reload:

```bash
pm2 startOrReload /home/ubuntu/monitoring/ecosystem.config.js --update-env
```
