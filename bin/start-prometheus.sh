#!/bin/bash
set -euo pipefail

exec /usr/bin/prometheus \
  --config.file=/home/ubuntu/monitoring/prometheus/prometheus.yml \
  --storage.tsdb.path=/home/ubuntu/monitoring/prometheus/data \
  --storage.tsdb.retention.time=15d \
  --storage.tsdb.retention.size=5GB \
  --web.listen-address=127.0.0.1:9090 \
  --web.console.templates=/usr/share/prometheus/consoles \
  --web.console.libraries=/usr/share/prometheus/console_libraries
