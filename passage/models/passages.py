"""Passages and device clock-offset records (SCN-04..11, HOR-01..05, BAD-03)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from passage.models.badges import Badge
from passage.models.routing import Checkpoint
from passage.models.school import School, Student


class ClockOffset(models.Model):
    """Clock skew observed for a scanner device during synchronization (HOR-02)."""

    device_id = models.CharField("identifiant d'appareil", max_length=64, db_index=True)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clock_offsets",
        verbose_name="agent",
    )
    synced_at = models.DateTimeField("synchronisé le", default=timezone.now, db_index=True)
    device_time = models.DateTimeField("heure appareil")
    offset_seconds = models.IntegerField("décalage (secondes)")
    is_suspect = models.BooleanField("horloge suspecte", default=False)

    class Meta:
        ordering = ["-synced_at"]
        verbose_name = "décalage d'horloge"
        verbose_name_plural = "décalages d'horloge"

    def __str__(self) -> str:
        return f"{self.device_id} ({self.offset_seconds:+d}s)"


class Passage(models.Model):
    """Recorded scan event (accepted or rejected) with full clock traceability."""

    class Status(models.TextChoices):
        VALID = "valid", "Valide"
        REJECTED = "rejected", "Rejeté"

    class RejectionReason(models.TextChoices):
        NONE = "", "Aucun"
        INVALID_SIGNATURE = "invalid_signature", "Signature invalide"
        EXPIRED_CODE = "expired_code", "Code expiré"
        ALREADY_USED = "already_used", "Code déjà utilisé"
        UNKNOWN_BADGE = "unknown_badge", "Badge inconnu"
        INACTIVE_BADGE = "inactive_badge", "Badge non actif"
        MALFORMED_CODE = "malformed_code", "Format de code invalide"
        SCHOOL_MISMATCH = "school_mismatch", "Badge hors établissement"

    class SyncStatus(models.TextChoices):
        SYNCED = "synced", "Synchronisé"

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="passages",
        verbose_name="établissement",
    )
    badge = models.ForeignKey(
        Badge,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="passages",
        verbose_name="badge",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="passages",
        verbose_name="élève",
    )
    checkpoint = models.ForeignKey(
        Checkpoint,
        on_delete=models.PROTECT,
        related_name="passages",
        verbose_name="point de contrôle",
    )
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_passages",
        verbose_name="agent",
    )
    badge_public_id = models.CharField("identifiant opaque présenté", max_length=64, blank=True)
    time_step = models.BigIntegerField("pas de temps", null=True, blank=True)
    device_id = models.CharField("identifiant d'appareil", max_length=64, db_index=True)
    client_event_id = models.CharField("identifiant d'événement local", max_length=64)

    local_scanned_at = models.DateTimeField("heure locale brute")
    server_received_at = models.DateTimeField(
        "heure de réception serveur", default=timezone.now, db_index=True
    )
    corrected_at = models.DateTimeField("heure corrigée")
    retained_at = models.DateTimeField("heure retenue", db_index=True)
    clock_offset_seconds = models.IntegerField("décalage estimé (s)", default=0)
    clock_suspect = models.BooleanField("horloge suspecte", default=False, db_index=True)

    status = models.CharField(
        "état",
        max_length=20,
        choices=Status.choices,
        db_index=True,
    )
    rejection_reason = models.CharField(
        "motif de rejet",
        max_length=32,
        choices=RejectionReason.choices,
        default=RejectionReason.NONE,
        blank=True,
    )
    sync_status = models.CharField(
        "état de synchronisation",
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.SYNCED,
    )

    class Meta:
        ordering = ["-retained_at"]
        verbose_name = "passage"
        verbose_name_plural = "passages"
        indexes = [
            models.Index(fields=["school", "retained_at"], name="idx_passage_school_retained"),
            models.Index(fields=["student", "retained_at"], name="idx_passage_student_retained"),
        ]
        constraints = [
            # SCN-08: idempotent sync — one event per (device, client_event_id).
            models.UniqueConstraint(
                fields=["device_id", "client_event_id"],
                name="uniq_passage_device_client_event",
            ),
            # BAD-03: single-use rotating code — each (badge, time_step) accepted at most once.
            models.UniqueConstraint(
                fields=["badge", "time_step"],
                condition=Q(status="valid", badge__isnull=False, time_step__isnull=False),
                name="uniq_valid_passage_badge_time_step",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["valid", "rejected"]),
                name="ck_passage_valid_status",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="valid", rejection_reason="")
                    | (Q(status="rejected") & ~Q(rejection_reason=""))
                ),
                name="ck_passage_rejection_reason_consistency",
            ),
        ]

    def __str__(self) -> str:
        return f"Passage {self.status} ({self.checkpoint.label} @ {self.retained_at:%Y-%m-%d %H:%M})"
