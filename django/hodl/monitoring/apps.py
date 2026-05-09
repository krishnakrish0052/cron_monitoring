from django.apps import AppConfig


class MonitoringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "monitoring"

    def ready(self):
        from monitoring.hc_ping import patch_all_crons

        patch_all_crons()

        self._register_urls()

    def _register_urls(self):
        from django.urls import include, path
        from config.urls import urlpatterns

        urlpatterns.append(path("api/monitoring/", include("monitoring.urls")))
