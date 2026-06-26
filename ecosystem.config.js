const hodlCronopsWorker = (name, queues) => ({
  name,
  script: '/home/ubuntu/monitoring/bin/start-hodl-cronops-worker.sh',
  interpreter: 'bash',
  cwd: '/home/ubuntu/hodlbackend2/HODL-2025',
  autorestart: true,
  watch: false,
  max_memory_restart: '1G',
  env: {
    NODE_ENV: 'production',
    DJANGO_SETTINGS_MODULE: 'config.settings',
    HODL_CRONOPS_QUEUE_ENABLED: '1',
    HODL_CRONOPS_WORKER_QUEUES: queues,
    HODL_CRONOPS_WORKER_CAPACITY: '1',
    HODL_CRONOPS_WORKER_POLL_SECONDS: '5',
    HODL_CRONOPS_RECONCILE_EVERY_SECONDS: '60',
    HODL_CRONOPS_SPOOL_REPLAY_EVERY_SECONDS: '60',
    HODL_CRONOPS_SPOOL_REPLAY_LIMIT: '20',
    HODL_CRONOPS_SPOOL_DIR: '/home/ubuntu/monitoring/runtime/hodl-cronops-spool'
  },
  restart_delay: 4000,
  max_restarts: 10,
  min_uptime: '10s'
});

module.exports = {
  apps: [
    {
      name: 'backend',
      script: '/home/ubuntu/monitoring/bin/start-ak1111-backend.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/ak1111-backend',
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      env: {
        NODE_ENV: 'production',
        DJANGO_SETTINGS_MODULE: 'monitored_settings',
        PYTHONPATH: '/home/ubuntu/monitoring/django/ak1111'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'hodl-backend',
      script: '/home/ubuntu/monitoring/bin/start-hodl-backend.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/hodlbackend2/HODL-2025',
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      env: {
        NODE_ENV: 'production',
        DJANGO_SETTINGS_MODULE: 'monitored_settings',
        PYTHONPATH: '/home/ubuntu/monitoring/django/hodl'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    },
    hodlCronopsWorker('hodl-cronops-worker-financial', 'financial'),
    hodlCronopsWorker('hodl-cronops-worker-rank', 'rank'),
    hodlCronopsWorker('hodl-cronops-worker-analytics', 'analytics'),
    hodlCronopsWorker('hodl-cronops-worker-maintenance', 'maintenance'),
    {
      name: 'healthchecks-web',
      script: '/home/ubuntu/monitoring/bin/start-healthchecks-web.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/monitoring/healthchecks',
      autorestart: true,
      watch: false,
      max_memory_restart: '512M',
      env: {
        DEBUG: 'False',
        PYTHONPATH: '/home/ubuntu/monitoring/django',
        SITE_ROOT: 'https://monitoring.holdonfordearlife.io',
        SITE_NAME: 'HODL Crons Monitoring',
        SITE_LOGO_URL: '/static/img/hodl-monitoring-logo.svg',
        ALLOWED_HOSTS: 'monitoring.holdonfordearlife.io,43.204.86.173,localhost,127.0.0.1'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'healthchecks-alerts',
      script: '/home/ubuntu/monitoring/bin/start-healthchecks-alerts.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/monitoring/healthchecks',
      autorestart: true,
      watch: false,
      max_memory_restart: '256M',
      env: {
        DEBUG: 'False',
        PYTHONPATH: '/home/ubuntu/monitoring/django',
        SITE_ROOT: 'https://monitoring.holdonfordearlife.io',
        SITE_NAME: 'HODL Crons Monitoring',
        SITE_LOGO_URL: '/static/img/hodl-monitoring-logo.svg',
        ALLOWED_HOSTS: 'monitoring.holdonfordearlife.io,43.204.86.173,localhost,127.0.0.1'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'healthchecks-reports',
      script: '/home/ubuntu/monitoring/bin/start-healthchecks-reports.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/monitoring/healthchecks',
      autorestart: true,
      watch: false,
      max_memory_restart: '256M',
      env: {
        DEBUG: 'False',
        PYTHONPATH: '/home/ubuntu/monitoring/django',
        SITE_ROOT: 'https://monitoring.holdonfordearlife.io',
        SITE_NAME: 'HODL Crons Monitoring',
        SITE_LOGO_URL: '/static/img/hodl-monitoring-logo.svg',
        ALLOWED_HOSTS: 'monitoring.holdonfordearlife.io,43.204.86.173,localhost,127.0.0.1'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'prometheus',
      script: '/home/ubuntu/monitoring/bin/start-prometheus.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/monitoring',
      autorestart: true,
      watch: false,
      max_memory_restart: '512M',
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'cron-observer',
      script: '/home/ubuntu/monitoring/bin/start-cron-observer.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/monitoring',
      autorestart: true,
      watch: false,
      max_memory_restart: '256M',
      env: {
        PYTHONPATH: '/home/ubuntu/monitoring/django',
        MONITORING_ROOT: '/home/ubuntu/monitoring',
        MONITORING_RUNTIME_ROOT: '/home/ubuntu/monitoring/runtime/observer',
        MONITORING_CRON_LOG_ROOT: '/home/ubuntu/monitoring/logs/crons',
        MONITORING_OBSERVER_INTERVAL_SECONDS: '15'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'infra-alert-worker',
      script: '/home/ubuntu/monitoring/bin/start-infra-alert-worker.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/monitoring',
      autorestart: true,
      watch: false,
      max_memory_restart: '256M',
      env: {
        DEBUG: 'False',
        PYTHONPATH: '/home/ubuntu/monitoring/django',
        SITE_ROOT: 'https://monitoring.holdonfordearlife.io',
        SITE_NAME: 'HODL Crons Monitoring',
        SITE_LOGO_URL: '/static/img/hodl-monitoring-logo.svg',
        ALLOWED_HOSTS: 'monitoring.holdonfordearlife.io,43.204.86.173,localhost,127.0.0.1',
        PROMETHEUS_URL: 'http://127.0.0.1:9090',
        MONITORING_SLACK_WEBHOOK_URL: process.env.MONITORING_SLACK_WEBHOOK_URL || '',
        HODL_CRONOPS_LIVE_URL: 'http://127.0.0.1:8001/api/cronops/live/',
        MONITORING_INFRA_ALERT_RUNTIME: '/home/ubuntu/monitoring/runtime/infra-alerts',
        MONITORING_OBSERVER_STATE_PATH: '/home/ubuntu/monitoring/runtime/observer/state.json',
        MONITORING_HEALTHCHECKS_ROOT: '/home/ubuntu/monitoring/healthchecks',
        MONITORING_HEALTHCHECKS_SERVER_HEALTH_NAMES: 'Server Health Check,HODL-2025 Server Health',
        MONITORING_HEALTHCHECKS_SERVER_PING_SECONDS: '300',
        MONITORING_INFRA_ALERT_INTERVAL_SECONDS: '30',
        MONITORING_INFRA_ALERT_REMINDER_SECONDS: '1800'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'db-maintenance-worker',
      script: '/home/ubuntu/monitoring/bin/start-db-maintenance-worker.sh',
      interpreter: 'bash',
      cwd: '/home/ubuntu/monitoring',
      autorestart: true,
      watch: false,
      max_memory_restart: '256M',
      env: {
        PYTHONPATH: '/home/ubuntu/monitoring/django',
        DB_MAINTENANCE_RUNTIME: '/home/ubuntu/monitoring/runtime/db-maintenance'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    }
  ]
};
