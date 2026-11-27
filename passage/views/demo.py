"""Demonstration bench view displaying up to 20 rotating QR badges when DEMO_MODE=1 (DEM-02, DEM-03, SEC-10)."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from passage.models import Badge, User
from passage.permissions import deny_access, role_required
from passage.services.badges import compute_time_step, sign_badge_code

MAX_DEMO_BENCH_BADGES = 20


def _is_demo_mode_enabled() -> bool:
    return bool(getattr(settings, "PASSAGE_DEMO_MODE", False))


def _list_school_demo_badges(school_id: int) -> list[Badge]:
    return list(
        Badge.objects.filter(
            student__school_id=school_id,
            status=Badge.Status.ACTIVE,
        )
        .select_related("student")
        .order_by("public_id")[:MAX_DEMO_BENCH_BADGES]
    )


@never_cache
@require_GET
def manager_demo_bench_view(request: HttpRequest) -> HttpResponse:
    """Render the multi-badge demonstration bench (404 when DEMO_MODE is disabled)."""
    if not _is_demo_mode_enabled():
        return HttpResponseNotFound("Page introuvable.")

    return _manager_demo_bench_protected(request)


@role_required(User.Role.MANAGER)
def _manager_demo_bench_protected(request: HttpRequest) -> HttpResponse:
    if request.user.school_id is None:
        return deny_access(
            request,
            target_type="demo_bench",
            details="Responsable sans établissement rattaché.",
        )

    badges = _list_school_demo_badges(request.user.school_id)
    context = {
        "school": request.user.school,
        "badge_count": len(badges),
        "code_step_seconds": int(getattr(settings, "PASSAGE_CODE_STEP_SECONDS", 30)),
    }
    return render(request, "passage/manager_demo_bench.html", context)


@never_cache
@require_GET
def manager_demo_bench_codes_api(request: HttpRequest) -> HttpResponse:
    """Return server-signed rotating codes without ever exposing badge secrets or student names (SEC-10)."""
    if not _is_demo_mode_enabled():
        return HttpResponseNotFound("Page introuvable.")

    return _manager_demo_bench_codes_protected(request)


@role_required(User.Role.MANAGER)
def _manager_demo_bench_codes_protected(request: HttpRequest) -> HttpResponse:
    if request.user.school_id is None:
        return deny_access(
            request,
            target_type="demo_bench",
            details="Responsable sans établissement rattaché.",
        )

    now = timezone.now()
    step_seconds = int(getattr(settings, "PASSAGE_CODE_STEP_SECONDS", 30))
    current_step = compute_time_step(now, step_seconds=step_seconds)
    epoch_sec = int(now.timestamp())
    seconds_left = step_seconds - (epoch_sec % step_seconds)

    badges = _list_school_demo_badges(request.user.school_id)
    items = [
        {
            "index": idx,
            "label": f"Badge {idx:02d}",
            "code": sign_badge_code(
                public_id=badge.public_id,
                secret=badge.secret,
                time_step=current_step,
            ),
        }
        for idx, badge in enumerate(badges, start=1)
    ]

    return JsonResponse(
        {
            "server_time": now.isoformat(),
            "code_step_seconds": step_seconds,
            "time_step": current_step,
            "seconds_left": seconds_left,
            "badges": items,
        },
        status=200,
    )
