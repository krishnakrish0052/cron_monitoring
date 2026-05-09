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
- After a monitored cron runs, a `.json` metadata file and `.log` execution file appear in `/home/ubuntu/monitoring/logs/crons`.
- While a monitored cron runs, a heartbeat file appears in `/home/ubuntu/monitoring/runtime/observer/heartbeats`.
- Daily checks that have never run show `waiting first run` and a future next-run time, not `down`.

## Rollback

Restart from the previous PM2 dump if needed:

```bash
pm2 resurrect
```

If only the new dashboard code is faulty, revert the Healthchecks custom files and reload:

```bash
pm2 startOrReload /home/ubuntu/monitoring/ecosystem.config.js --update-env
```
