"""Security audit trail and login rate-limiting records (AUT-03, AUT-07, AUD-01..03)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from passage.models.school import School


class AuditLog(models.Model):
    """Minimal security audit log (never stores passwords, badge secrets, or API keys)."""

    class Action(models.TextChoices):
        LOGIN_SUCCESS = "login_success", "Connexion réussie"
        LOGIN_FAILURE = "login_failure", "Échec de connexion"
        LOGOUT = "logout", "Déconnexion"
        BADGE_STATUS_CHANGED = "badge_status_changed", "Changement d'état de badge"
        ACTIVATION_LINK_CREATED = (
            "activation_link_created",
            "Génération de lien d'activation",
        )
        ACCESS_DENIED = "access_denied", "Refus d'accès"
        SUMMARY_GENERATED = "summary_generated", "Génération de synthèse"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Succès"
        DENIED = "denied", "Refusé"
        FAILURE = "failure", "Échec"

    occurred_at = models.DateTimeField("date", default=timezone.now, db_index=True)
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="établissement",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="acteur",
    )
    actor_identifier = models.CharField("identifiant acteur", max_length=150, blank=True)
    action = models.CharField(
        "action",
        max_length=40,
        choices=Action.choices,
        db_index=True,
    )
    target_type = models.CharField("type d'objet", max_length=64, blank=True)
    target_id = models.CharField("identifiant d'objet", max_length=64, blank=True)
    outcome = models.CharField(
        "résultat",
        max_length=16,
        choices=Outcome.choices,
        db_index=True,
    )
    ip_address = models.GenericIPAddressField("adresse IP", null=True, blank=True)
    details = models.CharField("détails", max_length=255, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name = "entrée d'audit"
        verbose_name_plural = "journal d'audit"
        indexes = [
            models.Index(fields=["school", "occurred_at"], name="idx_audit_school_time"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(outcome__in=["success", "denied", "failure"]),
                name="ck_audit_valid_outcome",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M} | {self.action} ({self.outcome})"


class LoginAttempt(models.Model):
    """Persistent login attempt log backing per-(identifier, IP) lockout (AUT-03)."""

    identifier = models.CharField("identifiant saisi", max_length=150, db_index=True)
    ip_address = models.GenericIPAddressField("adresse IP", db_index=True)
    attempted_at = models.DateTimeField("tentative le", default=timezone.now, db_index=True)
    succeeded = models.BooleanField("réussie", default=False)

    class Meta:
        ordering = ["-attempted_at"]
        verbose_name = "tentative de connexion"
        verbose_name_plural = "tentatives de connexion"
        indexes = [
            models.Index(
                fields=["identifier", "ip_address", "attempted_at"],
                name="idx_login_attempt_lookup",
            ),
        ]

    def __str__(self) -> str:
        status = "OK" if self.succeeded else "KO"
        return f"{self.identifier}@{self.ip_address} [{status}]"
