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
        SITE_ROOT: 'http://43.204.86.173:9000',
        SITE_NAME: 'HODL Crons Monitoring',
        SITE_LOGO_URL: '/static/img/hodl-monitoring-logo.svg',
        ALLOWED_HOSTS: '43.204.86.173,localhost,127.0.0.1'
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
        SITE_ROOT: 'http://43.204.86.173:9000',
        SITE_NAME: 'HODL Crons Monitoring',
        SITE_LOGO_URL: '/static/img/hodl-monitoring-logo.svg',
        ALLOWED_HOSTS: '43.204.86.173,localhost,127.0.0.1'
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
        SITE_ROOT: 'http://43.204.86.173:9000',
        SITE_NAME: 'HODL Crons Monitoring',
        SITE_LOGO_URL: '/static/img/hodl-monitoring-logo.svg',
        ALLOWED_HOSTS: '43.204.86.173,localhost,127.0.0.1'
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
        MONITORING_CRON_LOG_ROOT: '/home/ubuntu/monitoring/logs/crons'
      },
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s'
    }
  ]
};
