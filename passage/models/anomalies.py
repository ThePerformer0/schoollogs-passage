"""Detected anomalies and outgoing parent notifications (ANO-01..07, NOT-01..06)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from passage.models.passages import Passage
from passage.models.routing import Checkpoint
from passage.models.school import School, Student


class Anomaly(models.Model):
    """Discrepancy between expected itinerary and actual badge scans."""

    class AnomalyType(models.TextChoices):
        POINT_MANQUANT = "POINT_MANQUANT", "Point attendu non validé"
        HORS_FENETRE = "HORS_FENETRE", "Validation hors fenêtre"
        POINT_INCONNU = "POINT_INCONNU", "Point absent de l'itinéraire"
        DOUBLE_SCAN = "DOUBLE_SCAN", "Double scan"
        BADGE_NON_ACTIF = "BADGE_NON_ACTIF", "Badge non actif utilisé"

    class Status(models.TextChoices):
        OPEN = "open", "Ouverte"
        AUTO_RESOLVED = "auto_resolved", "Résolue automatiquement"
        HANDLED = "handled", "Traitée"

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="anomalies",
        verbose_name="établissement",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="anomalies",
        verbose_name="élève",
    )
    checkpoint = models.ForeignKey(
        Checkpoint,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="anomalies",
        verbose_name="point de contrôle",
    )
    passage = models.ForeignKey(
        Passage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anomalies",
        verbose_name="passage concerné",
    )
    anomaly_type = models.CharField(
        "type",
        max_length=32,
        choices=AnomalyType.choices,
        db_index=True,
    )
    date = models.DateField("date", db_index=True)
    status = models.CharField(
        "état",
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    details = models.CharField("détails", max_length=255, blank=True)
    detected_at = models.DateTimeField("détectée le", default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField("résolue le", null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_anomalies",
        verbose_name="traitée par",
    )

    class Meta:
        ordering = ["-detected_at"]
        verbose_name = "anomalie"
        verbose_name_plural = "anomalies"
        indexes = [
            models.Index(fields=["school", "date"], name="idx_anomaly_school_date"),
            models.Index(fields=["student", "date"], name="idx_anomaly_student_date"),
        ]
        constraints = [
            # ANO-03: idempotent scheduler — at most one POINT_MANQUANT per (student, date, checkpoint).
            models.UniqueConstraint(
                fields=["student", "date", "checkpoint", "anomaly_type"],
                condition=Q(anomaly_type="POINT_MANQUANT"),
                name="uniq_missing_point_anomaly",
            ),
            models.CheckConstraint(
                condition=Q(
                    anomaly_type__in=[
                        "POINT_MANQUANT",
                        "HORS_FENETRE",
                        "POINT_INCONNU",
                        "DOUBLE_SCAN",
                        "BADGE_NON_ACTIF",
                    ]
                ),
                name="ck_anomaly_valid_type",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["open", "auto_resolved", "handled"]),
                name="ck_anomaly_valid_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.anomaly_type} — {self.student} ({self.date})"


class OutgoingMessage(models.Model):
    """Parent notification queued/emitted/suppressed for an anomaly (NOT-01..06)."""

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        SENT = "sent", "Émis"
        NOT_SENT = "not_sent", "Non émis"

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="outgoing_messages",
        verbose_name="établissement",
    )
    parent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="outgoing_messages",
        verbose_name="parent",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="outgoing_messages",
        verbose_name="élève",
    )
    anomaly = models.ForeignKey(
        Anomaly,
        on_delete=models.CASCADE,
        related_name="outgoing_messages",
        verbose_name="anomalie",
    )
    target_date = models.DateField("date concernée", db_index=True)
    body = models.TextField("texte")
    status = models.CharField(
        "état",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    non_delivery_reason = models.CharField("motif de non-envoi", max_length=120, blank=True)
    created_at = models.DateTimeField("créé le", default=timezone.now, db_index=True)
    sent_at = models.DateTimeField("émis le", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "message sortant"
        verbose_name_plural = "messages sortants"
        indexes = [
            models.Index(
                fields=["student", "target_date", "status"],
                name="idx_message_daily_cap",
            ),
        ]
        constraints = [
            # NOT-06: one message per (anomaly, parent).
            models.UniqueConstraint(
                fields=["anomaly", "parent"],
                name="uniq_outgoing_message_per_anomaly_parent",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["pending", "sent", "not_sent"]),
                name="ck_outgoing_message_valid_status",
            ),
        ]

    def __str__(self) -> str:
        return f"Message ({self.get_status_display()}) -> {self.parent} [{self.target_date}]"
