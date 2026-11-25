"""Manager badge lifecycle views and public one-time device activation views."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from passage.models import Badge, User
from passage.permissions import deny_access, role_required
from passage.repositories import badge_repo, school_repo
from passage.services.auth import get_client_ip
from passage.services.badges import (
    ActivationLinkError,
    BadgeTransitionError,
    change_badge_status,
    consume_activation_link,
    create_activation_link,
    inspect_activation_link,
    issue_badge,
)


def _wants_json(request: HttpRequest) -> bool:
    accept = request.headers.get("Accept", "")
    return (
        "application/json" in accept
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


@require_GET
@role_required(User.Role.MANAGER)
def manager_badge_list(request: HttpRequest) -> HttpResponse:
    """List students and badges strictly within the manager's school (never exposing secrets)."""
    if request.user.school_id is None:
        return deny_access(request, target_type="badge")

    school_id = request.user.school_id
    context = {
        "school": request.user.school,
        "students": school_repo.list_students_for_school(school_id),
        "badges": badge_repo.list_badges_for_school(school_id),
        "status_choices": Badge.Status.choices,
    }
    return render(request, "passage/manager_badges.html", context)


@require_POST
@role_required(User.Role.MANAGER)
def manager_badge_issue(request: HttpRequest, student_id: int) -> HttpResponse:
    """Issue or reissue an active badge for a student of the manager's school (BAD-01, BAD-06)."""
    student = school_repo.get_student_for_manager(request.user, student_id)
    if student is None:
        return deny_access(
            request,
            target_type="student",
            target_id=student_id,
            details="Tentative d'émission de badge pour un élève hors établissement.",
        )

    reason = (request.POST.get("reason") or "").strip()[:255]
    new_badge = issue_badge(
        student,
        actor=request.user,
        reason=reason,
        ip_address=get_client_ip(request),
    )

    if _wants_json(request):
        # Never expose badge.secret in manager responses (SEC-10).
        return JsonResponse(
            {
                "badge_id": new_badge.id,
                "public_id": new_badge.public_id,
                "status": new_badge.status,
                "student_id": student.id,
                "replaced_badge_id": new_badge.replaced_badge_id,
            },
            status=201,
        )
    return redirect("passage:manager_badge_list")


@require_POST
@role_required(User.Role.MANAGER)
def manager_badge_change_status(request: HttpRequest, badge_id: int) -> HttpResponse:
    """Change a badge's status within the manager's school (BAD-05)."""
    badge = badge_repo.get_badge_for_manager(request.user, badge_id)
    if badge is None:
        return deny_access(
            request,
            target_type="badge",
            target_id=badge_id,
            details="Tentative de modification d'un badge hors établissement.",
        )

    new_status = (request.POST.get("status") or "").strip()
    reason = (request.POST.get("reason") or "").strip()[:255]

    try:
        updated = change_badge_status(
            badge,
            new_status=new_status,
            actor=request.user,
            reason=reason,
            ip_address=get_client_ip(request),
        )
    except BadgeTransitionError as exc:
        if _wants_json(request):
            return JsonResponse({"error": "invalid_transition", "detail": str(exc)}, status=400)
        return HttpResponse(str(exc), status=400, content_type="text/plain; charset=utf-8")

    if _wants_json(request):
        return JsonResponse(
            {
                "badge_id": updated.id,
                "public_id": updated.public_id,
                "status": updated.status,
            },
            status=200,
        )
    return redirect("passage:manager_badge_list")


@never_cache
@require_POST
@role_required(User.Role.MANAGER)
def manager_badge_activation_link(request: HttpRequest, badge_id: int) -> HttpResponse:
    """Generate a one-time activation link for an active badge (BAD-07, SEC-10)."""
    badge = badge_repo.get_badge_for_manager(request.user, badge_id)
    if badge is None:
        return deny_access(
            request,
            target_type="badge",
            target_id=badge_id,
            details="Tentative de génération de lien d'activation hors établissement.",
        )

    try:
        link, raw_token = create_activation_link(
            badge,
            actor=request.user,
            ip_address=get_client_ip(request),
        )
    except BadgeTransitionError as exc:
        if _wants_json(request):
            return JsonResponse({"error": "badge_inactive", "detail": str(exc)}, status=400)
        return HttpResponse(str(exc), status=400, content_type="text/plain; charset=utf-8")

    activation_path = reverse("passage:badge_activate", kwargs={"token": raw_token})
    activation_url = request.build_absolute_uri(activation_path)

    if _wants_json(request):
        response = JsonResponse(
            {
                "badge_id": badge.id,
                "public_id": badge.public_id,
                "activation_path": activation_path,
                "activation_url": activation_url,
                "expires_at": link.expires_at.isoformat(),
            },
            status=201,
        )
        response["Cache-Control"] = "no-store"
        return response

    response = render(
        request,
        "passage/manager_activation_link.html",
        {
            "badge": badge,
            "activation_url": activation_url,
            "expires_at": link.expires_at,
        },
    )
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def badge_activate_view(request: HttpRequest, token: str) -> HttpResponse:
    """Inspect (GET) or consume once (POST) a badge activation link (BAD-07, SEC-10)."""
    if request.method == "GET":
        try:
            link = inspect_activation_link(token)
        except ActivationLinkError as exc:
            status_code = 404 if exc.code == "not_found" else 410
            if _wants_json(request):
                return JsonResponse({"error": exc.code, "detail": exc.message}, status=status_code)
            response = render(
                request,
                "passage/badge_activate.html",
                {"error_message": exc.message, "link_valid": False},
                status=status_code,
            )
            response["Cache-Control"] = "no-store"
            return response

        response = render(
            request,
            "passage/badge_activate.html",
            {
                "link_valid": True,
                "expires_at": link.expires_at,
            },
        )
        response["Cache-Control"] = "no-store"
        return response

    # POST: consume atomically and deliver the secret ONCE (SEC-10).
    try:
        activation_payload = consume_activation_link(token)
    except ActivationLinkError as exc:
        status_code = 404 if exc.code == "not_found" else 410
        if _wants_json(request):
            return JsonResponse({"error": exc.code, "detail": exc.message}, status=status_code)
        response = render(
            request,
            "passage/badge_activate.html",
            {"error_message": exc.message, "link_valid": False},
            status=status_code,
        )
        response["Cache-Control"] = "no-store"
        return response

    if _wants_json(request):
        response = JsonResponse(activation_payload, status=200)
        response["Cache-Control"] = "no-store"
        return response

    response = render(
        request,
        "passage/badge_activate.html",
        {
            "link_valid": False,
            "activated": True,
            "activation_payload": activation_payload,
        },
    )
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def my_badge_view(request: HttpRequest) -> HttpResponse:
    """Public PWA page displaying the rotating QR badge from device local storage (BAD-08)."""
    return render(
        request,
        "passage/my_badge.html",
        {"code_step_seconds": settings.PASSAGE_CODE_STEP_SECONDS},
    )
