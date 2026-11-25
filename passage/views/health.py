"""Liveness health check view exposing no internal details (SEC-11)."""

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health_check(request: HttpRequest) -> JsonResponse:
    """Minimal liveness probe exposing no internal details."""
    return JsonResponse({"status": "ok"})
