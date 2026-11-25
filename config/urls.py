"""Root URL configuration for the SchoolLogs Passage module."""

from django.urls import include, path

from passage.admin import passage_admin_site
from passage.views import health_check

urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("admin/", passage_admin_site.urls),
    path("", include("passage.urls")),
]
