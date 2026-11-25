"""Parent consultation views with strict per-child server isolation (CON-01, SEC-05)."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from passage.models import User
from passage.permissions import deny_access, role_required
from passage.repositories import school_repo


@require_GET
@role_required(User.Role.PARENT)
def parent_dashboard(request: HttpRequest) -> HttpResponse:
    """List only the children explicitly linked to the authenticated parent."""
    students = school_repo.list_students_for_parent(request.user)
    return render(
        request,
        "passage/parent_dashboard.html",
        {"students": students},
    )


@require_GET
@role_required(User.Role.PARENT)
def parent_student_detail(request: HttpRequest, student_id: int) -> HttpResponse:
    """Show recent passages and anomalies for one child, denying any unlinked student."""
    student = school_repo.get_student_for_parent(request.user, student_id)
    if student is None:
        return deny_access(
            request,
            target_type="student",
            target_id=student_id,
            details="Tentative d'accès parent à un élève non rattaché.",
        )

    passages = school_repo.list_passages_for_student(student)
    anomalies = school_repo.list_anomalies_for_student(student)
    return render(
        request,
        "passage/parent_student_detail.html",
        {
            "student": student,
            "passages": passages,
            "anomalies": anomalies,
        },
    )
