"""JSON API endpoints for offline PWA sync, checkpoints cache, and clock sync."""

from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from passage.models import User
from passage.permissions import api_role_required
from passage.repositories import school_repo
from passage.services.sync import SyncPayloadError, sync_scan_batch

MAX_SYNC_BODY_BYTES = 512 * 1024  # 512 KB cap per batch


@never_cache
@require_GET
def server_time_api(request: HttpRequest) -> JsonResponse:
    """Public time reference allowing the 'Mon badge' PWA page to recalibrate its clock (BAD-08)."""
    now = timezone.now()
    return JsonResponse(
        {
            "server_time": now.isoformat(),
            "epoch_seconds": int(now.timestamp()),
            "code_step_seconds": settings.PASSAGE_CODE_STEP_SECONDS,
        }
    )


@never_cache
@require_GET
@api_role_required(User.Role.AGENT)
def agent_checkpoints_api(request: HttpRequest) -> JsonResponse:
    """Return active checkpoints of the authenticated agent's school for offline caching (SCN-02)."""
    if request.user.school_id is None:
        return JsonResponse(
            {"error": "forbidden", "detail": "Agent sans établissement rattaché."},
            status=403,
        )

    checkpoints = school_repo.list_checkpoints_for_school(request.user.school_id).filter(
        is_active=True
    )
    return JsonResponse(
        {
            "checkpoints": [
                {
                    "id": cp.id,
                    "label": cp.label,
                    "checkpoint_type": cp.checkpoint_type,
                }
                for cp in checkpoints
            ]
        }
    )


@never_cache
@require_POST
@api_role_required(User.Role.AGENT)
def agent_sync_api(request: HttpRequest) -> JsonResponse:
    """Idempotent CSRF-protected batch sync endpoint for portal agents (SCN-06..10, SEC-08)."""
    if request.user.school_id is None:
        return JsonResponse(
            {"error": "forbidden", "detail": "Agent sans établissement rattaché."},
            status=403,
        )

    if len(request.body) > MAX_SYNC_BODY_BYTES:
        return JsonResponse(
            {"error": "payload_too_large", "detail": "Lot de synchronisation trop volumineux."},
            status=400,
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse(
            {"error": "invalid_json", "detail": "Corps de requête JSON invalide."},
            status=400,
        )

    try:
        result = sync_scan_batch(agent=request.user, payload=payload)
    except SyncPayloadError as exc:
        return JsonResponse(
            {"error": "invalid_payload", "detail": str(exc)},
            status=400,
        )

    return JsonResponse(result, status=200)
