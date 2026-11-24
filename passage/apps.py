"""Application configuration for the Passage module."""

from django.apps import AppConfig


class PassageConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "passage"
    verbose_name = "Passage"
