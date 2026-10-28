import logging
import os

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        domain = os.getenv("REPLIT_DEV_DOMAIN")
        if domain:
            logging.getLogger("eic.api").info("EIC API base URL: https://%s/api/v1", domain)
        else:
            logging.getLogger("eic.api").info("EIC API base URL: /api/v1")
