"""Scoped data-access helpers for badges and synchronization."""

from __future__ import annotations

from django.db.models import QuerySet

from passage.models import Badge, Passage, User


def list_badges_for_school(school_id: int | None) -> QuerySet[Badge]:
    """Return badges belonging strictly to students of the given school (GES-05)."""
    if school_id is None:
        return Badge.objects.none()
    return (
        Badge.objects.filter(student__school_id=school_id)
        .select_related("student", "replaced_badge")
        .order_by("student__classroom", "student__last_name", "-issued_at")
    )


def get_badge_for_manager(manager: User, badge_id: int) -> Badge | None:
    """Return a badge only if its student belongs to the manager's school (SEC-05)."""
    if manager.role != User.Role.MANAGER or manager.school_id is None:
        return None
    return (
        Badge.objects.filter(pk=badge_id, student__school_id=manager.school_id)
        .select_related("student", "student__school")
        .first()
    )


def get_badge_by_public_id(public_id: str) -> Badge | None:
    """Lookup a badge by its opaque public identifier (BAD-01)."""
    return (
        Badge.objects.filter(public_id=public_id)
        .select_related("student", "student__school")
        .first()
    )


def get_passage_by_device_event(device_id: str, client_event_id: str) -> Passage | None:
    """Return an existing passage for the idempotency key (device_id, client_event_id)."""
    return Passage.objects.filter(
        device_id=device_id,
        client_event_id=client_event_id,
    ).first()
