"""Root URL configuration for the SchoolLogs Passage module."""

from django.contrib import admin
from django.urls import path

from passage.views import health_check

urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("admin/", admin.site.urls),
]
