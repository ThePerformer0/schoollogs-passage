"""Rotating digital badge lifecycle and one-time activation links (BAD-01..09)."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from passage.models.school import Student


def generate_badge_public_id() -> str:
    """Generate an opaque, non-identifying badge public ID (BAD-01)."""
    return secrets.token_hex(8)


def generate_badge_secret() -> str:
    """Generate a 256-bit per-badge HMAC signing secret (BAD-02, SEC-10)."""
    return secrets.token_hex(32)


class Badge(models.Model):
    """Rotating digital badge assigned to a student (at most one active per student)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        SUSPENDED = "suspended", "Suspendu"
        LOST = "lost", "Perdu"
        REVOKED = "revoked", "Révoqué"

    TERMINAL_STATUSES = frozenset({Status.LOST, Status.REVOKED})

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="badges",
        verbose_name="élève",
    )
    public_id = models.CharField(
        "identifiant opaque",
        max_length=32,
        unique=True,
        default=generate_badge_public_id,
        editable=False,
    )
    secret = models.CharField(
        "secret cryptographique",
        max_length=128,
        default=generate_badge_secret,
        editable=False,
    )
    status = models.CharField(
        "état",
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    issued_at = models.DateTimeField("émis le", default=timezone.now)
    replaced_badge = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replacement_badge",
        verbose_name="badge remplacé",
    )
    is_device_activated = models.BooleanField("support activé", default=False)
    device_activated_at = models.DateTimeField(
        "support activé le", null=True, blank=True
    )

    class Meta:
        ordering = ["-issued_at"]
        verbose_name = "badge"
        verbose_name_plural = "badges"
        constraints = [
            models.UniqueConstraint(
                fields=["student"],
                condition=Q(status="active"),
                name="uniq_active_badge_per_student",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["active", "suspended", "lost", "revoked"]),
                name="ck_badge_valid_status",
            ),
        ]

    def __str__(self) -> str:
        # Never expose self.secret in string representations (SEC-10).
        return f"Badge {self.public_id} ({self.get_status_display()})"


class BadgeStatusHistory(models.Model):
    """Immutable history of every badge status transition (BAD-05)."""

    badge = models.ForeignKey(
        Badge,
        on_delete=models.CASCADE,
        related_name="status_history",
        verbose_name="badge",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="badge_status_changes",
        verbose_name="modifié par",
    )
    previous_status = models.CharField(
        "ancien état",
        max_length=20,
        choices=Badge.Status.choices,
        blank=True,
    )
    new_status = models.CharField(
        "nouvel état",
        max_length=20,
        choices=Badge.Status.choices,
    )
    reason = models.CharField("motif", max_length=255, blank=True)
    changed_at = models.DateTimeField("modifié le", default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-changed_at"]
        verbose_name = "historique d'état de badge"
        verbose_name_plural = "historiques d'état de badge"
        constraints = [
            models.CheckConstraint(
                condition=Q(new_status__in=["active", "suspended", "lost", "revoked"]),
                name="ck_badge_history_valid_new_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.badge.public_id}: {self.previous_status or '-'} -> {self.new_status}"


class BadgeActivationLink(models.Model):
    """One-time time-limited activation link delivering the badge secret once (BAD-07)."""

    badge = models.ForeignKey(
        Badge,
        on_delete=models.CASCADE,
        related_name="activation_links",
        verbose_name="badge",
    )
    # SHA-256 hex digest of the raw one-time token (never store the raw token in DB).
    token_hash = models.CharField("empreinte du jeton", max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_activation_links",
        verbose_name="créé par",
    )
    created_at = models.DateTimeField("créé le", default=timezone.now)
    expires_at = models.DateTimeField("expire le")
    used_at = models.DateTimeField("utilisé le", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "lien d'activation de badge"
        verbose_name_plural = "liens d'activation de badge"
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("created_at")),
                name="ck_activation_link_expires_after_creation",
            ),
        ]

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def is_valid_at(self, reference_time=None) -> bool:
        now = reference_time or timezone.now()
        return self.used_at is None and now < self.expires_at

    def __str__(self) -> str:
        state = "utilisé" if self.is_used else "actif"
        return f"Lien d'activation {self.badge.public_id} ({state})"
