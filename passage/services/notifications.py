"""Parent outgoing notifications with isolated gateway and daily cap (NOT-01..06)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from passage.models import Anomaly, OutgoingMessage, Student, User
from passage.services.gateways.notification_gateway import (
    NotificationGateway,
    get_notification_gateway,
)

CAP_REACHED_REASON = "plafond atteint"


def _get_daily_message_cap() -> int:
    raw = getattr(settings, "PASSAGE_MAX_MESSAGES_PER_STUDENT_PER_DAY", 3)
    return max(0, int(raw))


def _format_anomaly_local_time(anomaly: Anomaly) -> str:
    if anomaly.passage_id is None or anomaly.passage is None:
        return ""
    school_tz = ZoneInfo(anomaly.school.timezone)
    local_dt = anomaly.passage.retained_at.astimezone(school_tz)
    return f"{local_dt:%H} h {local_dt:%M}"


def build_parent_message_body(anomaly: Anomaly) -> str:
    """Build a neutral, plain-French parent message with zero sensitive identifiers (NOT-02)."""
    school_name = anomaly.school.name
    first_name = anomaly.student.first_name
    cp_label = anomaly.checkpoint.label if anomaly.checkpoint is not None else "point de contrôle"
    local_time = _format_anomaly_local_time(anomaly)

    if anomaly.anomaly_type == Anomaly.AnomalyType.POINT_MANQUANT:
        return (
            f"{school_name} : {first_name} n'a pas été enregistré(e) "
            f"au point {cp_label} le {anomaly.date:%d/%m/%Y}."
        )

    if anomaly.anomaly_type == Anomaly.AnomalyType.HORS_FENETRE:
        time_part = f" à {local_time}" if local_time else ""
        return (
            f"{school_name} : {first_name} a été enregistré(e) au point {cp_label}"
            f"{time_part}, en dehors de l'horaire habituel."
        )

    if anomaly.anomaly_type == Anomaly.AnomalyType.POINT_INCONNU:
        time_part = f" à {local_time}" if local_time else ""
        return (
            f"{school_name} : {first_name} a été enregistré(e){time_part} "
            f"sur un point de contrôle inhabituel ({cp_label})."
        )

    if anomaly.anomaly_type == Anomaly.AnomalyType.DOUBLE_SCAN:
        time_part = f" à {local_time}" if local_time else ""
        return (
            f"{school_name} : deux passages rapprochés ont été enregistrés "
            f"pour {first_name} au point {cp_label}{time_part}."
        )

    if anomaly.anomaly_type == Anomaly.AnomalyType.BADGE_NON_ACTIF:
        time_part = f" à {local_time}" if local_time else ""
        return (
            f"{school_name} : le badge de {first_name} n'est plus actif et n'a pas pu être "
            f"validé{time_part}. Merci de contacter l'établissement."
        )

    return (
        f"{school_name} : un événement inhabituel de suivi a été constaté pour "
        f"{first_name} le {anomaly.date:%d/%m/%Y}."
    )


@transaction.atomic
def notify_parents_for_anomaly(
    anomaly: Anomaly,
    *,
    gateway: NotificationGateway | None = None,
) -> list[OutgoingMessage]:
    """Create and dispatch outgoing messages to every linked parent with daily cap enforcement (NOT-01..06)."""
    # Reload relations safely if called right after creation.
    anomaly = (
        Anomaly.objects.select_related("school", "student", "checkpoint", "passage")
        .get(pk=anomaly.pk)
    )
    parents = list(
        User.objects.filter(
            student_links__student_id=anomaly.student_id,
            role=User.Role.PARENT,
        )
        .distinct()
        .order_by("id")
    )
    if not parents:
        return []

    # Lock the student row so concurrent anomalies cannot race past the daily cap.
    Student.objects.select_for_update().get(pk=anomaly.student_id)

    daily_cap = _get_daily_message_cap()
    sent_today = OutgoingMessage.objects.filter(
        student_id=anomaly.student_id,
        target_date=anomaly.date,
        status=OutgoingMessage.Status.SENT,
    ).count()

    active_gateway = gateway or get_notification_gateway()
    body = build_parent_message_body(anomaly)
    now = timezone.now()
    messages: list[OutgoingMessage] = []

    for parent in parents:
        # NOT-06: at most one outgoing message per (anomaly, parent).
        existing = OutgoingMessage.objects.filter(
            anomaly_id=anomaly.id,
            parent_id=parent.id,
        ).first()
        if existing is not None:
            messages.append(existing)
            continue

        if sent_today >= daily_cap:
            status = OutgoingMessage.Status.NOT_SENT
            reason = CAP_REACHED_REASON
            sent_at = None
        else:
            delivery = active_gateway.send(
                parent=parent,
                student=anomaly.student,
                body=body,
                anomaly=anomaly,
            )
            if delivery.sent:
                status = OutgoingMessage.Status.SENT
                reason = ""
                sent_at = delivery.sent_at or now
                sent_today += 1
            else:
                status = OutgoingMessage.Status.NOT_SENT
                reason = (delivery.non_delivery_reason or "échec d'envoi")[:120]
                sent_at = None

        try:
            with transaction.atomic():
                msg = OutgoingMessage.objects.create(
                    school=anomaly.school,
                    parent=parent,
                    student=anomaly.student,
                    anomaly=anomaly,
                    target_date=anomaly.date,
                    body=body,
                    status=status,
                    non_delivery_reason=reason,
                    created_at=now,
                    sent_at=sent_at,
                )
        except IntegrityError:
            msg = OutgoingMessage.objects.get(anomaly_id=anomaly.id, parent_id=parent.id)
        messages.append(msg)

    return messages
