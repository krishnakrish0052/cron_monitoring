from django.urls import path

from monitoring.views import health_check
from monitoring.dashboard import dashboard_view, dashboard_api, dashboard_pings_api

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("dashboard/", dashboard_view, name="dashboard"),
    path("dashboard/api/", dashboard_api, name="dashboard-api"),
    path("dashboard/pings/", dashboard_pings_api, name="dashboard-pings"),
]
