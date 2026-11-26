"""Scoped ORM queries enforcing strict parent and school isolation (GES-05, SEC-05)."""

from __future__ import annotations

from django.db.models import QuerySet

from passage.models import (
    Anomaly,
    AuditLog,
    Checkpoint,
    DailySummary,
    OutgoingMessage,
    Passage,
    RouteWindow,
    Student,
    User,
)


def list_students_for_parent(parent: User) -> QuerySet[Student]:
    """Return only active/known students explicitly linked to this parent."""
    if parent.role != User.Role.PARENT:
        return Student.objects.none()
    return (
        Student.objects.filter(parent_links__parent=parent)
        .select_related("school")
        .distinct()
        .order_by("last_name", "first_name")
    )


def get_student_for_parent(parent: User, student_id: int) -> Student | None:
    """Return the student only if linked to `parent`, otherwise None (SEC-05)."""
    return list_students_for_parent(parent).filter(pk=student_id).first()


def list_students_for_school(school_id: int | None) -> QuerySet[Student]:
    """Return students belonging exclusively to the given school (GES-05)."""
    if school_id is None:
        return Student.objects.none()
    return (
        Student.objects.filter(school_id=school_id)
        .select_related("school")
        .order_by("classroom", "last_name", "first_name")
    )


def get_student_for_manager(manager: User, student_id: int) -> Student | None:
    """Return the student only if owned by the manager's school (GES-05, SEC-05)."""
    if manager.role != User.Role.MANAGER or manager.school_id is None:
        return None
    return list_students_for_school(manager.school_id).filter(pk=student_id).first()


def list_checkpoints_for_school(school_id: int | None) -> QuerySet[Checkpoint]:
    """Return checkpoints belonging exclusively to the given school."""
    if school_id is None:
        return Checkpoint.objects.none()
    return Checkpoint.objects.filter(school_id=school_id).order_by("label")


def get_checkpoint_for_manager(manager: User, checkpoint_id: int) -> Checkpoint | None:
    if manager.role != User.Role.MANAGER or manager.school_id is None:
        return None
    return list_checkpoints_for_school(manager.school_id).filter(pk=checkpoint_id).first()


def list_passages_for_student(student: Student, *, limit: int = 50) -> QuerySet[Passage]:
    """Return recent passages for a single student (ordered newest first)."""
    return (
        Passage.objects.filter(student=student)
        .select_related("checkpoint", "badge")
        .order_by("-retained_at")[:limit]
    )


def list_anomalies_for_student(student: Student, *, limit: int = 50) -> QuerySet[Anomaly]:
    """Return recent anomalies for a single student (ordered newest first)."""
    return (
        Anomaly.objects.filter(student=student)
        .select_related("checkpoint", "passage", "handled_by")
        .order_by("-detected_at")[:limit]
    )


def list_route_windows_for_student(student: Student) -> QuerySet[RouteWindow]:
    """Return configured itinerary windows for a single student."""
    return (
        RouteWindow.objects.filter(student=student)
        .select_related("checkpoint")
        .order_by("order", "start_time")
    )


def list_passages_for_school(school_id: int | None, *, limit: int = 100) -> QuerySet[Passage]:
    if school_id is None:
        return Passage.objects.none()
    return (
        Passage.objects.filter(school_id=school_id)
        .select_related("student", "checkpoint", "badge", "agent")
        .order_by("-retained_at")[:limit]
    )


def list_rejected_passages_for_school(
    school_id: int | None, *, limit: int = 100
) -> QuerySet[Passage]:
    if school_id is None:
        return Passage.objects.none()
    return (
        Passage.objects.filter(school_id=school_id, status=Passage.Status.REJECTED)
        .select_related("student", "checkpoint", "badge", "agent")
        .order_by("-server_received_at")[:limit]
    )


def list_anomalies_for_school(school_id: int | None, *, limit: int = 100) -> QuerySet[Anomaly]:
    if school_id is None:
        return Anomaly.objects.none()
    return (
        Anomaly.objects.filter(school_id=school_id)
        .select_related("student", "checkpoint", "passage", "handled_by")
        .order_by("-detected_at")[:limit]
    )


def get_anomaly_for_manager(manager: User, anomaly_id: int) -> Anomaly | None:
    if manager.role != User.Role.MANAGER or manager.school_id is None:
        return None
    return (
        Anomaly.objects.filter(school_id=manager.school_id, pk=anomaly_id)
        .select_related("student", "checkpoint", "passage")
        .first()
    )


def list_outgoing_messages_for_school(
    school_id: int | None, *, limit: int = 100
) -> QuerySet[OutgoingMessage]:
    if school_id is None:
        return OutgoingMessage.objects.none()
    return (
        OutgoingMessage.objects.filter(school_id=school_id)
        .select_related("parent", "student", "anomaly")
        .order_by("-created_at")[:limit]
    )


def list_audit_logs_for_school(
    school_id: int | None, *, limit: int = 200
) -> QuerySet[AuditLog]:
    if school_id is None:
        return AuditLog.objects.none()
    return (
        AuditLog.objects.filter(school_id=school_id)
        .select_related("actor")
        .order_by("-occurred_at")[:limit]
    )


def list_classrooms_for_school(school_id: int | None) -> list[str]:
    """Return distinct classrooms belonging exclusively to `school_id`."""
    if school_id is None:
        return []
    return list(
        Student.objects.filter(school_id=school_id, status=Student.Status.ACTIVE)
        .values_list("classroom", flat=True)
        .distinct()
        .order_by("classroom")
    )


def list_daily_summaries_for_school(
    school_id: int | None, *, limit: int = 30
) -> QuerySet[DailySummary]:
    if school_id is None:
        return DailySummary.objects.none()
    return (
        DailySummary.objects.filter(school_id=school_id)
        .select_related("generated_by")
        .order_by("-created_at")[:limit]
    )
