"""Manager views strictly scoped to the manager's own school (CON-02, GES-01..07)."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from passage.forms import CheckpointForm, RouteWindowForm, StudentForm
from passage.models import User
from passage.permissions import deny_access, role_required
from passage.repositories import school_repo
from passage.services.anomalies import mark_anomaly_handled


@require_GET
@role_required(User.Role.MANAGER)
def manager_dashboard(request: HttpRequest) -> HttpResponse:
    """Overview of students, passages, anomalies, and outgoing messages for the school."""
    if request.user.school_id is None:
        return deny_access(
            request,
            target_type="school",
            details="Responsable sans établissement rattaché.",
        )

    school_id = request.user.school_id
    context = {
        "school": request.user.school,
        "students": school_repo.list_students_for_school(school_id),
        "passages": school_repo.list_passages_for_school(school_id),
        "rejected_passages": school_repo.list_rejected_passages_for_school(school_id),
        "anomalies": school_repo.list_anomalies_for_school(school_id),
        "outgoing_messages": school_repo.list_outgoing_messages_for_school(school_id),
    }
    return render(request, "passage/manager_dashboard.html", context)


@require_GET
@role_required(User.Role.MANAGER)
def manager_student_detail(request: HttpRequest, student_id: int) -> HttpResponse:
    """View a student only if belonging to the manager's school (GES-05, SEC-05)."""
    student = school_repo.get_student_for_manager(request.user, student_id)
    if student is None:
        return deny_access(
            request,
            target_type="student",
            target_id=student_id,
            details="Tentative d'accès responsable à un élève hors établissement.",
        )

    context = {
        "student": student,
        "passages": school_repo.list_passages_for_student(student),
        "anomalies": school_repo.list_anomalies_for_student(student),
    }
    return render(request, "passage/manager_student_detail.html", context)


@require_GET
@role_required(User.Role.MANAGER)
def manager_audit_list(request: HttpRequest) -> HttpResponse:
    """Display the security audit log strictly scoped to the manager's school (AUD-01)."""
    if request.user.school_id is None:
        return deny_access(
            request,
            target_type="audit_log",
            details="Responsable sans établissement rattaché.",
        )

    audit_logs = school_repo.list_audit_logs_for_school(request.user.school_id)
    return render(
        request,
        "passage/manager_audit_list.html",
        {"school": request.user.school, "audit_logs": audit_logs},
    )


@require_http_methods(["GET", "POST"])
@role_required(User.Role.MANAGER)
def manager_student_create(request: HttpRequest) -> HttpResponse:
    """Create a student in the manager's own school with server-side validation (GES-01, GES-07)."""
    if request.user.school_id is None:
        return deny_access(request, target_type="student")

    if request.method == "POST":
        form = StudentForm(request.POST, school=request.user.school)
        if form.is_valid():
            student = form.save()
            return redirect("passage:manager_student_detail", student_id=student.id)
    else:
        form = StudentForm(school=request.user.school)

    return render(
        request,
        "passage/manager_form.html",
        {"form": form, "form_title": "Nouvel élève"},
    )


@require_http_methods(["GET", "POST"])
@role_required(User.Role.MANAGER)
def manager_checkpoint_create(request: HttpRequest) -> HttpResponse:
    """Create a checkpoint in the manager's own school (GES-03, GES-07)."""
    if request.user.school_id is None:
        return deny_access(request, target_type="checkpoint")

    if request.method == "POST":
        form = CheckpointForm(request.POST, school=request.user.school)
        if form.is_valid():
            form.save()
            return redirect("passage:manager_dashboard")
    else:
        form = CheckpointForm(school=request.user.school)

    return render(
        request,
        "passage/manager_form.html",
        {"form": form, "form_title": "Nouveau point de contrôle"},
    )


@require_http_methods(["GET", "POST"])
@role_required(User.Role.MANAGER)
def manager_route_window_create(request: HttpRequest) -> HttpResponse:
    """Create a validated itinerary window within the manager's school (GES-04, GES-07)."""
    if request.user.school_id is None:
        return deny_access(request, target_type="route_window")

    if request.method == "POST":
        form = RouteWindowForm(request.POST, school=request.user.school)
        if form.is_valid():
            form.save()
            return redirect("passage:manager_dashboard")
    else:
        form = RouteWindowForm(school=request.user.school)

    return render(
        request,
        "passage/manager_form.html",
        {"form": form, "form_title": "Nouvelle fenêtre d'itinéraire"},
    )


@require_POST
@role_required(User.Role.MANAGER)
def manager_anomaly_handle(request: HttpRequest, anomaly_id: int) -> HttpResponse:
    """Mark a school anomaly as handled by the manager (ANO-05, GES-05, SEC-05)."""
    anomaly = school_repo.get_anomaly_for_manager(request.user, anomaly_id)
    if anomaly is None:
        return deny_access(
            request,
            target_type="anomaly",
            target_id=anomaly_id,
            details="Tentative de traitement d'une anomalie hors établissement.",
        )

    updated = mark_anomaly_handled(anomaly, actor=request.user)
    accept = request.headers.get("Accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JsonResponse(
            {
                "anomaly_id": updated.id,
                "status": updated.status,
                "handled_by": request.user.username,
            },
            status=200,
        )
    return redirect("passage:manager_dashboard")
