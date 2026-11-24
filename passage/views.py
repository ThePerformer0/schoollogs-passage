"""Base views for the Passage module foundation."""

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health_check(request: HttpRequest) -> JsonResponse:
    """Minimal liveness probe exposing no internal details (SEC-11)."""
    return JsonResponse({"status": "ok"})
