"""Portal agent view — single dedicated screen exposing no student/anomaly data (CON-03)."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from passage.models import User
from passage.permissions import deny_access, role_required
from passage.repositories import school_repo


@require_GET
@role_required(User.Role.AGENT)
def agent_scan_view(request: HttpRequest) -> HttpResponse:
    """Single screen accessible to portal agents (offline PWA scanning added in Lot 2)."""
    if request.user.school_id is None:
        return deny_access(
            request,
            target_type="scan",
            details="Agent sans établissement rattaché.",
        )

    checkpoints = school_repo.list_checkpoints_for_school(request.user.school_id).filter(
        is_active=True
    )
    return render(
        request,
        "passage/agent_scan.html",
        {"school": request.user.school, "checkpoints": checkpoints},
    )
