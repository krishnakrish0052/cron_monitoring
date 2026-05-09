from __future__ import annotations

from django.urls import path

from healthchecks_custom import views


urlpatterns = [
    path("", views.monitoring_dashboard, name="hc-monitoring"),
    path("api/overview/", views.monitoring_overview, name="hc-monitoring-overview"),
    path("api/live/", views.monitoring_live, name="hc-monitoring-live"),
    path(
        "api/checks/<uuid:code>/series/",
        views.monitoring_check_series,
        name="hc-monitoring-check-series",
    ),
    path(
        "api/checks/<uuid:code>/live/",
        views.monitoring_check_live,
        name="hc-monitoring-check-live",
    ),
    path(
        "api/checks/<uuid:code>/runs/",
        views.monitoring_check_runs,
        name="hc-monitoring-check-runs",
    ),
    path(
        "api/checks/<uuid:code>/logs/",
        views.monitoring_check_log,
        name="hc-monitoring-check-log",
    ),
    path(
        "api/infrastructure/",
        views.monitoring_infrastructure,
        name="hc-monitoring-infrastructure",
    ),
    path("metrics/", views.monitoring_metrics, name="hc-monitoring-metrics"),
    path(
        "api/cron-history/<path:job_key>/",
        views.monitoring_cron_history,
        name="hc-monitoring-cron-history",
    ),
]
