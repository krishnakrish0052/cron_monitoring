# Monitoring Work Log

## Completed

- Moved monitoring runtime into `/home/ubuntu/monitoring`.
- Moved Healthchecks into `/home/ubuntu/monitoring/healthchecks`.
- Moved AK1111 and HODL monitoring wrappers into `/home/ubuntu/monitoring/django`.
- Added PM2-managed Prometheus using `/home/ubuntu/monitoring/prometheus/prometheus.yml`.
- Added an authenticated Healthchecks `/monitoring/` dashboard and top-navigation link.
- Added Prometheus-backed infrastructure metrics for CPU, memory, disk, and NGINX.
- Added wrapper-only cron execution logging in `/home/ubuntu/monitoring/django/monitoring_common`.
- Added dashboard APIs for recent cron runs and execution log tails.
- Changed dashboard wording for Healthchecks `new` checks to `waiting first run`.
- Added next expected run time calculation for cron checks.
- Sorted dashboard cron rows by status priority so `down` and `up` checks are easier to see before first-run daily jobs.
- Added `monitoring_observer`, a standalone Python observer service for live cron state.
- Extended cron wrappers with per-second heartbeat files, `/proc` CPU/RAM sampling, stack summaries, DB query tracing, slow query detection, and no-progress detection.
- Added live dashboard APIs and UI sections for running crons, aggregate cron resource usage, stale/stuck alerts, and IST timestamps.
- Initialized the monitoring codebase for sanitized GitHub versioning.
- Updated crontabs to point to `/home/ubuntu/monitoring/django/.../manage_monitored.py`.
- Rebranded the Healthchecks UI and dashboard as `HODL Crons Monitoring`.
- Added the monitoring-owned SVG logo and AMOLED black dashboard styling.
- Added structured event streams beside each cron execution log.
- Added request tracing for `requests`, DB tracing summaries, bounded Python line/function tracing, and external API failure classification.
- Added observer-owned live server samples so CPU/RAM/disk values are consistent between live and infrastructure panels.
- Added recent completed runs and external API error panels to `/monitoring/`.
- Added an operator-first Action Center, sticky quick navigation, and cron table search/status filters to make `/monitoring/` easier to use during incidents.
- Patched the local AK1111 server checkout for the same explorer response-shape crash in `lplock.utils.fetch_data.fetchInvestmentsFromBlockchain` and `dex.utils.fetch_investments.fetchTokenInvestments`.
- Added AK1111 `config.explorer.fetch_normal_transactions()` to use Etherscan V2 request shape for Base (`chainid=8453`), prefer `ETHERSCAN_API_KEY` with `BASESCAN_API_KEY` fallback, validate `result` before iterating transactions, classify explorer failures, and avoid advancing block checkpoints after malformed explorer responses.
- Verified the next AK1111 explorer cron run no longer raises `TypeError`; it now surfaces the real upstream blocker as `etherscan_paid_tier_required` when the current Etherscan plan does not include Base chain API coverage.

## Design Decisions

- Do not edit `/home/ubuntu/ak1111-backend` or `/home/ubuntu/hodlbackend2/HODL-2025`; Buddy pipeline owns those repos.
- Capture cron stdout, stderr, Python logging, wrapper start/end metadata, and tracebacks from the wrapper layer.
- Keep Healthchecks ping/event logs separate from execution logs.
- Use native SVG charts instead of Grafana or external chart CDNs.
- Keep Prometheus bound to localhost.
- Use a monitoring-owned observer service instead of editing Buddy-managed application repositories.
- Exclude runtime files, secrets, venvs, logs, DB files, and Prometheus data from Git.
- Show IST as the primary dashboard timezone. Keep UTC only in raw JSON/log metadata for debugging.
- Treat BscScan/BaseScan/Etherscan string-result failures as app/external API issues, not Healthchecks ping issues.

## Operational Notes

- Real cron logs appear only after a cron runs through the monitoring wrapper.
- `waiting first run` means the check exists but has never received its first Healthchecks ping. This is expected for daily jobs until their first scheduled execution.
- The dashboard next-run value is calculated from each Healthchecks cron schedule and timezone; it is display-only and does not change cron execution.
- Live observer data is written to `/home/ubuntu/monitoring/runtime/observer/state.json` every second.
- Per-run heartbeat files live under `/home/ubuntu/monitoring/runtime/observer/heartbeats` and are intentionally not committed.
- Per-run structured trace files live beside raw cron logs as `<run-id>.events.jsonl`.
- Stuck detection currently means no output, logging, or DB query progress for the configured threshold.
- If a cron has no internal print/log statements, the dashboard still shows HTTP, DB, stack, and Python trace events, but cannot invent business-specific step names.
- Maximum Python tracing is bounded by `MONITORING_CRON_MAX_TRACE_EVENTS` and can be disabled with `MONITORING_CRON_TRACE=0`.
- External explorer API errors like deprecated V1 endpoint responses should be fixed in the app repositories in a separate Buddy-safe change.
- The AK1111 checkout at `/home/ubuntu/ak1111-backend` is not a Git repository on this server. The local explorer fix must be copied into the AK1111 source-control repository used by Buddy/deployment, or a future deploy may overwrite it.
- On 2026-05-09, HODL app-side cron reliability work started on branch `cron-reliability-monitoring` in `/home/ubuntu/hodlbackend2/HODL-2025`.
- HODL now has an app-owned `cronops` Django app with DB-backed cron jobs, runs, events, checkpoints, and per-cron locks.
- HODL duplicate cron runs are skipped per cron key, while different cron jobs can still run in parallel.
- HODL explorer fetchers now use Etherscan V2-style `txlist` calls through `cronops.explorer` and validate response shape before iterating transactions.
- HODL long-running cron paths now emit business progress checkpoints for LP earnings, SVR4 earning/rank/business aggregation, Korean marking, and blackcard sync.
- The monitoring dashboard now merges HODL `cronops` state from `http://127.0.0.1:8001/api/cronops/live/` into `/monitoring/api/live/`.
- `ETHERSCAN_API_KEY` is not currently present in the HODL `.env`; the code falls back to existing explorer keys, but a real Etherscan V2 key should be added for production reliability.
- The Healthchecks Django check currently warns that `EMAIL_HOST` is not set; this warning existed before the dashboard/log changes.
- PM2 may warn that the daemon version differs from the local CLI version; use `pm2 update` during a quiet window if needed.
