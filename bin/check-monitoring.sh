#!/bin/bash
set -euo pipefail

pm2 list
curl -fsS http://127.0.0.1:9000/api/v1/status/ >/dev/null
curl -fsS http://127.0.0.1:9000/monitoring/metrics/ >/dev/null
curl -fsS http://127.0.0.1:9090/-/ready >/dev/null
curl -fsS http://127.0.0.1:9100/metrics >/dev/null
curl -fsS http://127.0.0.1:9113/metrics >/dev/null
test -s /home/ubuntu/monitoring/runtime/observer/state.json
